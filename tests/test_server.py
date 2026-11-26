import http.client
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agent.server import make_server
from tests.fakes import FakeModel

KEY = "sk-test-SERVERKEY-0123456789"


def _request(port, method, path, body=None, headers=None, content_type=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        h = dict(headers or {})
        if content_type:
            h["Content-Type"] = content_type
        conn.request(method, path, body=body, headers=h)   # http.client sends our Host header as given
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


@pytest.fixture
def app(db_path, tmp_path, monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    queued, created, failure = [], [], []

    def factory(provider, model_name, key):
        if failure:
            raise failure[0]
        created.append((provider, model_name, key))
        return queued.pop(0)

    server = make_server(db_path, traces=str(tmp_path / "traces"), examples=["Q one?", "Q two?"], create_model=factory)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    def get_raw(path, headers=None):
        return _request(port, "GET", path, headers=headers)

    def post_raw(path, body, content_type="application/json", headers=None):
        return _request(port, "POST", path, body=body, headers=headers, content_type=content_type)

    def get(path, headers=None):
        status, body = get_raw(path, headers)
        return status, json.loads(body)

    def post(path, body, headers=None):
        status, out = post_raw(path, json.dumps(body).encode(), headers=headers)
        return status, json.loads(out)

    try:
        yield SimpleNamespace(get=get, post=post, get_raw=get_raw, post_raw=post_raw, queue=queued.append,
                              fail_with=lambda e: failure.append(e), created=created,
                              traces=tmp_path / "traces", db=db_path, port=port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_state_before_setup(app):
    status, s = app.get("/api/state")
    assert status == 200 and s["ready"] is False and s["provider"] is None
    assert s["database"] == "university.db" and s["examples"] == ["Q one?", "Q two?"]
    assert {p["id"]: p["needs_key"] for p in s["providers"]} == {"openai": True, "anthropic": True, "ollama": False}
    assert all(p["key_in_environment"] is False for p in s["providers"])

def test_setup_then_ask(app):
    app.queue(FakeModel([{"sql": "SELECT COUNT(*) AS n FROM students", "expects_single_value": True}, "There are 8."]))
    assert app.post("/api/setup", {"provider": "openai", "model": "m-1", "key": KEY}) == (200, {"ok": True})
    assert app.created == [("openai", "m-1", KEY)]
    status, rec = app.post("/api/ask", {"question": "How many?"})
    assert status == 200 and rec["status"] == "answered" and rec["answer"] == "There are 8."
    assert (rec["provider"], rec["model"], rec["database"]) == ("openai", "m-1", app.db)
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql", "execute_sql", "check_result", "answer"]
    assert (app.traces / f"{rec['run_id']}.json").exists()
    assert app.get("/api/runs")[1]["runs"][-1]["run_id"] == rec["run_id"]

def test_key_typed_in_the_page_never_comes_back(app, caplog, capsys):
    app.queue(FakeModel([RuntimeError(f"401 bad key {KEY}")]))
    with caplog.at_level(logging.DEBUG):
        app.post("/api/setup", {"provider": "openai", "model": "m-1", "key": KEY})
        bodies = [json.dumps(app.get(p)[1]) for p in ("/api/state", "/api/runs")]
        bodies.append(json.dumps(app.post("/api/ask", {"question": "q"})[1]))
    out = capsys.readouterr()
    files = [f.read_text() for f in app.traces.glob("*.json")]
    for text in bodies + files + [out.out, out.err, caplog.text]:
        assert KEY not in text
    assert files and "Run stopped at generate_sql: RuntimeError." in bodies[-1]

def test_ready_at_start_when_the_environment_has_everything(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai"); monkeypatch.setenv("LLM_MODEL", "m-env"); monkeypatch.setenv("OPENAI_API_KEY", KEY)
    created = []
    server = make_server(db_path, traces=str(tmp_path), create_model=lambda p, m, k: created.append((p, m, k)) or FakeModel([]))
    try:
        assert created == [("openai", "m-env", None)]        # the key is left to the provider package
    finally:
        server.server_close()

def test_setup_uses_the_environment_key_when_none_is_typed(app, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    assert app.get("/api/state")[1]["providers"][1]["key_in_environment"] is True
    app.queue(FakeModel([]))
    assert app.post("/api/setup", {"provider": "anthropic", "model": "m-2"})[0] == 200
    assert app.created == [("anthropic", "m-2", None)]

@pytest.mark.parametrize("body,reason", [
    ({"provider": "nope", "model": "m"}, "provider"),
    ({"provider": "openai", "model": " "}, "model"),
    ({"provider": "openai", "model": "m"}, "key"),
    ({"provider": ["openai"], "model": "m"}, "provider"),
    ({"provider": {}, "model": "m"}, "provider"),
])
def test_setup_refusals(app, body, reason):
    status, out = app.post("/api/setup", body)
    assert status == 400 and reason in out["error"].lower() and app.created == []

def test_model_creation_error_is_a_class_name_only(app):
    app.fail_with(ValueError(f"secret {KEY}"))
    status, out = app.post("/api/setup", {"provider": "ollama", "model": "m"})
    assert status == 400 and out == {"error": "Could not create the model: ValueError"}

def test_ask_before_setup(app):
    assert app.post("/api/ask", {"question": "q"}) == (409, {"error": "Choose a model first"})

def test_follow_up_by_choice_and_by_typed_reply(app):
    clarify = {"clarification": "What counts as passing?", "choices": ["How many scored at least 60?", "How many scored at least 75?"]}
    count = {"sql": "SELECT COUNT(*) AS n FROM students", "expects_single_value": True}
    app.queue(FakeModel([clarify, count, "8", count, "8"]))
    app.post("/api/setup", {"provider": "ollama", "model": "m"})
    first = app.post("/api/ask", {"question": "How many passed?"})[1]
    assert first["status"] == "clarification" and first["steps"][-1]["output"]["choices"] == clarify["choices"]
    picked = app.post("/api/ask", {"question": clarify["choices"][1]})[1]
    typed = app.post("/api/ask", {"original": "How many passed?", "reply": "anyone over seventy"})[1]
    assert picked["question"] == "How many scored at least 75?" and picked["status"] == "answered"
    assert typed["question"] == "How many passed? (anyone over seventy)" and typed["status"] == "answered"
    assert len(list(app.traces.glob("*.json"))) == 3

def test_two_asks_two_traces(app):
    count = {"sql": "SELECT COUNT(*) AS n FROM students"}
    app.queue(FakeModel([count, "8", count, "8"]))
    app.post("/api/setup", {"provider": "ollama", "model": "m"})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda q: app.post("/api/ask", {"question": q}), ["a?", "b?"]))
    assert {r[0] for r in results} == {200} and len({r[1]["run_id"] for r in results}) == 2

def test_runs_lists_only_this_database_and_at_most_twenty(app):
    for i in range(22):
        (app.traces).mkdir(exist_ok=True)
        (app.traces / f"20260101-0000{i:02d}-aaaaaa.json").write_text(json.dumps({"run_id": f"r{i}", "database": app.db, "steps": []}))
    (app.traces / "20260102-000000-bbbbbb.json").write_text(json.dumps({"run_id": "other", "database": "library.db", "steps": []}))
    (app.traces / "20260103-000000-cccccc.json").write_text("[]")
    (app.traces / "20260103-000000-dddddd.json").write_text("null")
    runs = app.get("/api/runs")[1]["runs"]
    assert [r["run_id"] for r in runs] == [f"r{i}" for i in range(2, 22)]

def test_question_length_limit(app):
    count = {"sql": "SELECT COUNT(*) AS n FROM students"}
    app.queue(FakeModel([count, "8"]))
    app.post("/api/setup", {"provider": "ollama", "model": "m"})
    assert app.post("/api/ask", {"question": "x" * 2001}) == (400, {"error": "The question is too long"})
    assert app.post("/api/ask", {"original": "x" * 1990, "reply": "y" * 10}) == (400, {"error": "The question is too long"})
    assert not list(app.traces.glob("*.json"))
    assert app.post("/api/ask", {"question": "x" * 2000})[0] == 200

def test_responses_forbid_framing(app):
    for path in ("/", "/api/state"):
        conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            resp.read()
            assert resp.getheader("X-Frame-Options") == "DENY"
            assert resp.getheader("Content-Security-Policy") == "frame-ancestors 'none'"
        finally:
            conn.close()

def test_ollama_models(app, monkeypatch):
    monkeypatch.setattr("agent.server.fetch_ollama_tags", lambda: {"models": [{"name": "llama3.2"}, {"name": "mistral"}]})
    assert app.get("/api/ollama") == (200, {"running": True, "models": ["llama3.2", "mistral"]})

def test_ollama_not_running(app, monkeypatch):
    def boom(): raise OSError("connection refused")
    monkeypatch.setattr("agent.server.fetch_ollama_tags", boom)
    assert app.get("/api/ollama") == (200, {"running": False, "models": []})

def test_rejects_foreign_host_and_form_posts(app):
    assert app.post("/api/ask", {"question": "q"}, headers={"Host": "evil.example"})[0] == 403
    assert app.get("/api/state", headers={"Host": "evil.example"})[0] == 403
    assert app.post_raw("/api/setup", b"provider=openai", content_type="application/x-www-form-urlencoded")[0] == 415

def test_page_is_served_and_unknown_paths_are_404(app):
    status, body = app.get_raw("/")
    assert status == 200 and b"<!doctype html>" in body.lower()
    assert app.get("/nope")[0] == 404

def test_handler_helpers_are_not_http_methods(app):
    for method in ("setup", "ask", "SETUP"):
        for path in ("/api/setup", "/api/ask"):
            try:
                status, _ = _request(app.port, method, path, body=b'{"provider": "ollama", "model": "m", "question": "q"}',
                                     content_type="application/json")
            except (http.client.HTTPException, OSError):
                status = None
            assert status == 501, (method, path, status)
    assert app.created == [] and not list(app.traces.glob("*.json"))
