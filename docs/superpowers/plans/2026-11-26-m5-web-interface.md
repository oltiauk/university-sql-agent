# M5 Web Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner's page design, connected to the real agent: ask a question in the browser, get the answer from an actual run, open that run's trace beside it, follow up a clarification by clicking a choice or typing, and choose the model on a setup screen when the app opens.

**Architecture:** One small local web server (`agent/server.py`, Python standard library only) serves one page (`agent/ui.html`) and five JSON endpoints. The server calls the same `build_graph` and `run_traced` the command line uses, so what the page shows is a saved trace record, nothing invented. The chosen model, and a key typed into the page, live in the server's memory until it stops.

**Tech Stack:** Python stdlib `http.server`, `json`, `urllib`. Plain HTML, CSS and JavaScript in one file, no build step, no framework. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-25-university-sql-agent-design.md` (section "M5: UI"). **Design:** two static mock-ups by the owner, placed in the repository by the orchestrator before Task 1: `docs/design/sql-agent-ui.html` (chat and trace panel) and `docs/design/start-screen.html` (the start screen).

## Decisions in this plan

Made by the orchestrator unless marked; the owner reviews them with the plan.

| Decision | Choice | Why |
|---|---|---|
| Server | Python's built-in `http.server` | No new dependency for five endpoints; nothing to explain beyond the file itself. |
| Who it serves | One person on this computer. Bound to `127.0.0.1` | It holds a key in memory; it must not be reachable from the network. |
| Where the key lives | In the server's memory, inside the model object, until the server stops (**owner:** session only, never on disk, never in a trace or log) | Reloading the page does not ask again; stopping the app forgets it. |
| When the answer appears | When the run finishes, as one response. The page shows "Working…" meanwhile | A run takes a few seconds. Streaming steps live would add a second code path for no new information; the trace has every step. |
| Example questions | Read from a text file given with `--examples`, one question per line | The design's example pills are about the university. Keeping them in a file beside the database keeps the app database-agnostic. |
| Earlier answers | On load the page shows the last 20 saved runs for this database | The original request: a list of answers, click one, see what happened. They are the saved traces. |
| Checking a key | Not checked at setup. A wrong key shows on the first question as "Run stopped at generate_sql: AuthenticationError.", with the model tag one click away | Checking would cost a model call and a second error path. |
| Typed clarification | Free text, any words (**owner**), joined to the original question with the existing `combine` | The design's box accepted only a number. |

## Global Constraints

- Layout and visual style follow the design file: the chat card and the trace card, their classes, colours, radii, spacing and fonts. Do not restyle. New elements (setup screen, "Working…" state, new status chips) reuse the design's existing classes and tokens.
- Everything the page shows about a run comes from the trace record the server returns. No canned answers, SQL, rows, timings or run ids in the page.
- All text that comes from the model, the database or the person is inserted with the design's `esc()` (or `textContent`). Never as raw HTML.
- `agent/` must not contain the words student, teacher, course, enrollment, grade, semester (any case). Task 1 extends the vocabulary test to every file in `agent/`, including `ui.html`.
- The API key never appears in a response, a trace, a log line, printed output, a URL or the page's HTML. It travels once, in the body of the setup request, and is passed straight to the model constructor.
- The server binds `127.0.0.1` only, rejects requests whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>`, and accepts `POST` only with `Content-Type: application/json`.
- No new dependencies. Nothing else under `agent/` changes: `graph.py`, `prompts.py`, `db.py`, `trace.py`, `cli.py` stay as they are.
- The default test run uses no network and no real model. pytest treats warnings as errors; servers started in tests are shut down and their sockets closed.
- Implementers never read `.env`, never run the real model, never set `RUN_LIVE`.

## Review Focus

1. A question is asked before a model is chosen (stale tab, direct request): a clear refusal, not a crash. (Task 1, `test_ask_before_setup`)
2. Model output or database text containing `<script>` or `<img onerror>`: shown as text, never run. (Task 2, Step 5 check; reviewed by reading every insertion point)
3. A page on another website posts to the local server: refused by the `Host` and content-type checks. (Task 1, `test_rejects_foreign_host_and_form_posts`)
4. A key typed at setup must not come back from any endpoint or reach a trace or log. (Task 1, `test_key_typed_in_the_page_never_comes_back`)
5. Ollama is not running, or answers slowly: the setup screen says so within about a second and the other providers still work. (Task 1, `test_ollama_not_running`)
6. Two questions sent at once (double click): each gets its own run and trace; nothing is lost. (Task 1, `test_two_asks_two_traces`)

---

### Task 1: The local server

**Files:**
- Create: `agent/server.py`, `tests/test_server.py`, `db/university_questions.txt`, `db/library_questions.txt`
- Modify: `tests/test_vocabulary.py`, `tests/test_key_safety.py`
- Create: `agent/ui.html` as a one-line placeholder (`<!doctype html><title>Ask the database</title>`); Task 2 replaces it.

**Interfaces:**
- Command: `python -m agent.server [--db university.db] [--traces traces] [--examples FILE] [--port 8000]`. Prints `Open http://127.0.0.1:<port>/` and serves until Ctrl-C.
- `agent.server.make_server(db: str, traces: str = "traces", examples: list[str] = (), port: int = 0, create_model=None) -> http.server.ThreadingHTTPServer`. `create_model(provider, model_name, key)` returns a model object; the default calls `init_chat_model(model_name, model_provider=provider)` and adds `api_key=key` only when a key was given. Tests pass a fake.
- Providers, a module constant: `{"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "ollama": None}`.

Endpoints. All responses are JSON except `/`.

| Method and path | Request | Response |
|---|---|---|
| `GET /` | | the bytes of `agent/ui.html`, `text/html; charset=utf-8` |
| `GET /api/state` | | `{"database": <file name only>, "ready": bool, "provider": str or null, "model": str or null, "providers": [{"id": "openai", "needs_key": true, "key_in_environment": bool}, ...], "default_provider": <LLM_PROVIDER or null>, "default_model": <LLM_MODEL or null>, "examples": [str, ...]}` |
| `GET /api/ollama` | | `{"running": bool, "models": [str, ...]}` from `http://127.0.0.1:11434/api/tags`, 1 second timeout; any failure is `{"running": false, "models": []}` |
| `POST /api/setup` | `{"provider", "model", "key"?}` | `{"ok": true}`; or status 400 with `{"error": "<reason>"}` for an unknown provider, an empty model name, or a provider that needs a key when none was sent and none is in the environment; or 400 with `{"error": "Could not create the model: <ExceptionClassName>"}` |
| `POST /api/ask` | `{"question"}` or `{"original", "reply"}` | the trace record returned by `run_traced`, as JSON (`default=repr`); 409 with `{"error": "Choose a model first"}` when not ready; 400 for an empty question |
| `GET /api/runs` | | `{"runs": [record, ...]}`: the saved traces in the traces folder whose `database` equals this server's `--db` value, oldest first, at most the last 20 |

Behaviour:
- At start, if `LLM_PROVIDER` and `LLM_MODEL` are set and that provider's key is in the environment (or it needs none), the server creates the model and is ready at once: the setup screen is never shown (**owner:** if it is already set, do not ask). `load_dotenv()` is called in `main()` only, not in `make_server`.
- `/api/ask` with `original` and `reply` asks `combine(original, reply)`.
- The record's `provider` and `model` are the chosen ones; `database` is the `--db` value as given.
- A lock around model replacement; each ask builds its own graph, so two asks can run together.
- Any other path: 404. Any unexpected exception in a handler: 500 with `{"error": "<ExceptionClassName>"}`, never the message.
- Override `log_message` so the request log goes through `logging` (the key-safety test captures it); request bodies are never logged.
- `# ponytail:` one model for the whole process; give each browser session its own if more than one person ever uses it.

`db/university_questions.txt`, one per line:

```
Which students scored above the class average in Databases in Fall 2025?
How many students passed Databases in Fall 2025?
Which students were enrolled in Databases in Spring 2024?
What is each teacher's email address?
```

`db/library_questions.txt`:

```
Which books are currently on loan?
Who has Dune right now?
Which books has nobody borrowed?
```

- [ ] **Step 1: Write the failing tests** in `tests/test_server.py`

A fixture `app(db_path, tmp_path, monkeypatch)` clears `LLM_PROVIDER`, `LLM_MODEL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` from the environment, starts `make_server(db_path, traces=str(tmp_path / "traces"), examples=["Q one?", "Q two?"], create_model=fake_factory)` on a background thread and shuts it down afterwards. `fake_factory` records its arguments in a list and returns the `FakeModel` the test queued. Helpers `get(path)` and `post(path, body, headers=None)` use `urllib` and return `(status, parsed_json)`.

```python
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
    runs = app.get("/api/runs")[1]["runs"]
    assert [r["run_id"] for r in runs] == [f"r{i}" for i in range(2, 22)]

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
```

Extend `tests/test_vocabulary.py` to scan every file under `agent/` (`rglob("*")`, files only, skipping `__pycache__`), not only `*.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py -q`
Expected: FAIL, `agent.server` cannot be imported.

- [ ] **Step 3: Implement `agent/server.py`**

`fetch_ollama_tags()` is a module-level function (`urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1)` then `json.load`) so tests can replace it. `make_server` builds the handler class as a closure over a small state dict (`model`, `provider`, `model_name`). `main()` parses the arguments, calls `load_dotenv()`, reads the examples file if given, prints the URL and calls `serve_forever()`, closing the server on Ctrl-C.

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, 7 skipped, no warnings.

Run: `grep -rilE "student|teacher|course|enrollment|grade|semester" agent/`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add agent/server.py agent/ui.html db/university_questions.txt db/library_questions.txt tests/
git commit -m "feat: local web server for the agent: state, setup, ask, runs"
```

---

### Task 2: The page, connected to real runs

**Files:**
- Replace: `agent/ui.html`
- Test: `tests/test_ui_page.py`

**Interfaces:**
- Consumes: the endpoints of Task 1.
- Produces: a page with two top-level views inside `#app`: the chat (this task) and the setup screen (Task 3). This task shows the chat whenever `/api/state` says `ready`, and otherwise a single line "Choose a model first" where the chat would be; Task 3 replaces that line with the start screen.

Start from the design file: keep its `<style>` block, its `esc()`, its icons, and the structure and class names produced by `turnHtml()` and `traceHtml()`. Remove everything that invents data: `seeded()`, `expand()`, the `sql…()` builders, `GRADES`, `runId()`, the fixed questions and the number parsing.

What the page does:

- **On load:** `GET /api/state`, then `GET /api/runs`. Each saved run becomes a turn in the thread, oldest first. The composer's two tags show the database name and `<provider> · <model>`. Example pills come from `state.examples`; none are drawn when the list is empty.
- **Asking:** submitting the composer, or clicking an example pill, posts `{"question": text}` to `/api/ask`. While waiting, a turn is shown with the question bubble and the answer text "Working…" and the composer is disabled. The response record replaces it, its trace opens in the trace panel, and the new turn scrolls into view. A non-200 response shows the server's `error` text as that turn's answer.
- **A turn** is drawn from a record: bubble = `record.question`; answer = `record.answer`; the See trace / Hide trace pill; a status chip; "1 retry" when the record has two `generate_sql` steps that produced SQL.
- **Status chips** (same pill style as the design): `clarification` "Needs a detail" and `empty` "No rows" and `failed` "Failed" as in the design; add `mismatch` "Not one value" and `no_value` "No value" in the design's amber, and `error` "Stopped" in the design's red. `answered` has no chip.
- **Clarification:** when a record's status is `clarification` and no later turn answers it, draw its choices (`steps[last].output.choices`) as `.choice` buttons, each showing the full choice text, followed by the design's `.own` form with a text input (placeholder "Or type your own answer", any words) and its round send button. Clicking a choice posts `{"question": <choice>}`. Submitting the form posts `{"original": record.question, "reply": text}`; the bubble of that follow-up shows what the person typed or the choice they clicked. A clarification with an empty choices list still shows the text input. Once followed up, the choices are no longer drawn. Long choice text must wrap: let `.choice` grow in height (`min-height:36px`, vertical padding, `text-align:left`) instead of the design's fixed `height:36px`.
- **The trace panel** is drawn from `record.steps`, one numbered step each, with the design's header: the question, then `run <run_id> · <database> · <provider> · <model> · <status>` in the small monospace line. Per step: the label, the node name in monospace, the duration as `<duration_ms> ms` with thousands separators.

| Step | Label | Body |
|---|---|---|
| `load_schema` | Read the tables | "`<n>` tables: `<names>`" taken from the `CREATE TABLE` / `CREATE VIEW` statements in `output.schema`; below it a closed `<details>` "Show what the model was given" holding the full schema text in a `<pre>` |
| `generate_sql` with `sql` | Write the SQL, or "Write the SQL again" when an earlier `generate_sql` step had SQL | the SQL in the design's `<pre>`; a small line "Expects a single value" when `expects_single_value` is true |
| `generate_sql` with `clarification` | Ask back (amber dot) | the clarification, the choices as a list, and "No SQL was run." |
| `generate_sql` with only `error` and `status` | Write the SQL (red dot) | the `answer` text of that step |
| `execute_sql` with `rows` | Run the SQL | the design's rows table from `columns` and `rows`; "Showing the first 100 rows" when `truncated`; "0 rows" with an amber dot when there are none |
| `execute_sql` with `error` | Run the SQL (red dot) | the error in the design's `.err` box |
| `check_result` | Check the result | "Fits the question." when `checked`; otherwise the step's `answer` with an amber dot |
| `answer` | Write the answer | the answer text; red dot and the text when the step's `status` is `failed` |
| any step whose output is only `{"error": <class name>}` | the node's usual label (red dot) | "This step stopped: `<class name>`" |

A `null` cell is shown as `NULL` in grey. Every value goes through `esc()`.

- [ ] **Step 1: Write the failing tests** in `tests/test_ui_page.py`

The page has no JavaScript test runner (none is added: no build step). These tests guard what can be checked from Python:

```python
PAGE = (ROOT / "agent" / "ui.html").read_text()

def test_page_has_no_canned_data():
    for canned in ("Arta", "Blerim", "Databases", "7c41e0", "GRADES", "seeded", "anthropic ·"):
        assert canned not in PAGE

def test_page_calls_the_real_endpoints():
    for path in ("/api/state", "/api/runs", "/api/ask"):
        assert path in PAGE

def test_page_keeps_the_design():
    design = (ROOT / "docs" / "design" / "sql-agent-ui.html").read_text()
    for token in ("#F7F9FC", "#1B2333", "border-radius:28px", ".bubble", ".orb", ".choice", ".own", ".trace-btn", ".steps", ".dot", ".rows", "JetBrains Mono"):
        assert token in design and token in PAGE

def test_every_dynamic_insertion_is_escaped():
    # every "+ <expression> +" that reads from a record, step, choice or state goes through esc( or a number formatter
    assert "innerHTML" in PAGE and "function esc(" in PAGE
    for raw in ("+ r.answer +", "+ r.question +", "+ s.sql +", "+ s.error +", "+ choice +", "+ rec.answer +", "+ rec.question +"):
        assert raw not in PAGE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ui_page.py -q`
Expected: FAIL (the placeholder page has none of this).

- [ ] **Step 3: Write `agent/ui.html`.**

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, no warnings. The vocabulary test now covers `ui.html`.

- [ ] **Step 5: Check it in a browser against a scripted server**

Write a throwaway script (not committed) that starts `make_server` on the university database with `create_model` returning a `FakeModel` scripted for: an answered run, a retried run, a clarification followed by a choice and by a typed reply, an empty result, a mismatch, a twice-failed run, a step that raises, and an answer whose text is `<img src=x onerror=alert(1)><script>alert(2)</script>` with a row value containing the same. Post a setup request first (the setup screen comes in Task 3). Open the page, exercise each, and confirm: the layout matches the design side by side; every status draws its chip; the trace panel shows each step as the table above says; the hostile text appears as text and nothing runs. Describe what you saw in the report, with the script.

- [ ] **Step 6: Commit**

```bash
git add agent/ui.html tests/test_ui_page.py
git commit -m "feat: the page shows real runs and their traces"
```

---

### Task 3: The start screen, and the README

**Files:**
- Modify: `agent/ui.html`, `tests/test_ui_page.py`, `README.md`

**Interfaces:**
- Consumes: `/api/state`, `/api/ollama`, `/api/setup`.

The start screen follows the owner's second design file, `docs/design/start-screen.html` (placed there by the orchestrator before this task). Keep its look exactly: the page centred on the screen, the 380px card, the large orb, the heading "Which AI should answer?", the three-way `.switch` (Claude, OpenAI, Ollama, in that order), the `.field` / `.label` / `.note` styles, the `.models` list with its tick icon, and the dark `.start` button. Bring its CSS into `agent/ui.html` beside the chat styles; where a class name exists in both designs with different rules (`.card`, `.orb`, `h1`), scope the start screen's rules under a wrapper (for example `.start-view .card`) so neither design changes. The centring applies only while the start screen is shown.

What changes from the static design, and nothing else:

- **Providers.** "Claude" sends provider id `anthropic`; "OpenAI" sends `openai`; "Ollama" sends `ollama`. The one pressed first is `state.default_provider` when set, otherwise Claude.
- **Model name for Claude and OpenAI.** The design has no place for it, and no model name is hard-coded in this project, so add one `.field` above the key field in the same style: label "Model", a text input, pre-filled with `state.default_model` when the pressed provider is `state.default_provider`, otherwise empty with the placeholder "Model name". (Orchestrator's addition; the owner is told.)
- **API key.** The design's masked input, `type="password" autocomplete="off"`. The note under it becomes "Kept in memory until you stop the app. Never saved." because that is what the server does; the design's wording ("until you close this page") would not be true. When `key_in_environment` is true for the pressed provider, the input is replaced by a `.note` line: "Found in your environment. Nothing to type."
- **Ollama (owner: adjust for the Ollama option).** Pressing Ollama hides the model-name and key fields and shows the design's `.models` list, filled from `GET /api/ollama`: one button per installed model with the design's tick, the first one pressed. While the request is out: a `.note` "Looking for Ollama…". When `running` is false: no list, a `.note` "Ollama is not running on this computer. Start it and choose Ollama again." and Start is disabled. When it runs with no models: a `.note` "Ollama has no models yet. Pull one, then choose Ollama again." and Start is disabled. Pressing Ollama again asks the server again. A `.note` under the list: "Runs on this computer. No key needed."
- **Start.** The design's `<a class="start">` becomes a `<button type="button" class="start">` with the same look (add `border:0;width:100%` so the button matches the link); disabled state at 45% opacity with `cursor:default`. It posts `{"provider", "model", "key"?}` to `/api/setup`. Start is disabled until a model is named (or picked) and, for a provider that needs a key with none in the environment, a key is typed. On `ok`: clear the key input, re-read `/api/state` and `/api/runs`, show the chat. On an error: the server's `error` text under the button, in the chat design's `.err` colours.
- **When it shows.** When `state.ready` is false. When ready, the composer's model tag is a button ("`<Claude|OpenAI|Ollama> · <model>`"); clicking it shows the start screen again, with a small "Back" text button under Start that returns to the chat without changing anything.
- The key is held only in the input until it is posted: never in a JavaScript variable that outlives the request, never in `localStorage`, `sessionStorage`, a cookie or the URL.

README: add a section "Web interface" after "Quick start" (how to start it for each database, with `--examples`; what the setup screen asks and where the key lives; that every answer shown is a saved run and its trace panel is that run's record; clarification by choice or typed words); add `server.py`, `ui.html`, the two question files and `docs/design/` to "Layout"; add a "Design decisions" row for the standard-library server; replace the Known limits line that says the web interface is planned but not built with its real limits: one person on one computer, the answer appears when the run finishes, the page itself has no automated browser tests. Change nothing else in the README.

- [ ] **Step 1: Write the failing tests**, added to `tests/test_ui_page.py`

```python
def test_setup_screen_is_in_the_page():
    for needed in ("/api/setup", "/api/ollama", 'type="password"', "Which AI should answer?", "Found in your environment",
                   "Ollama is not running on this computer", "Never saved", "Looking for Ollama"):
        assert needed in PAGE

def test_start_screen_keeps_its_design():
    design = (ROOT / "docs" / "design" / "start-screen.html").read_text()
    for token in (".switch", ".models", ".start", "max-width:380px", "width:64px;height:64px", "border-radius:16px", "#E3ECFA"):
        assert token in design and token in PAGE

def test_key_is_not_stored_by_the_page():
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "?key=", "&key="):
        assert forbidden not in PAGE
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `.venv/bin/pytest tests/test_ui_page.py -q`
Expected: the two new tests fail.

- [ ] **Step 3: Implement the setup screen in `agent/ui.html`.**

- [ ] **Step 4: Check it in a browser against a scripted server** (throwaway script, `create_model` returning a `FakeModel`, environment cleared): open `docs/design/start-screen.html` beside it and compare the look; the three provider states; a key typed into the masked field; the environment-key state (start the script with a fake `ANTHROPIC_API_KEY`); Ollama running with models and not running (replace `agent.server.fetch_ollama_tags` in the script); a setup error; the model tag reopening setup. Confirm in the browser's network panel or the server's log that the key appears only in the body of the one setup request. Describe what you saw in the report.

- [ ] **Step 5: Update `README.md`** as described.

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, no warnings.

- [ ] **Step 7: Commit**

```bash
git add agent/ui.html tests/test_ui_page.py README.md
git commit -m "feat: model setup screen; README covers the web interface"
```

---

## Milestone close

Run by the orchestrator.

1. **The real thing, in a browser:** start `python -m agent.server --examples db/university_questions.txt` with the model from `.env` (setup is skipped, as designed) and ask the four example questions; then start it with the environment's provider settings hidden, go through the setup screen with a typed key, and ask one question; then the library database with `--db library.db --examples db/library_questions.txt`. Screenshots of the chat with a trace open, a clarification with its choices, and the setup screen go to the owner.
2. **Key check:** search the saved traces, the server's output and the page's responses for the key.
3. **Spec review** against the M5 items in the spec; **code review** for bugs (server input handling, the key's path, every place the page inserts text); **complexity review**. Each once, on the whole milestone diff.
4. Update the spec's M5 section to what was built.
