import logging

from agent.cli import main
from tests.fakes import FakeModel

KEY = "sk-test-KEYSAFETY-0123456789"

def test_key_never_reaches_traces_or_output(db_path, tmp_path, capsys, caplog, monkeypatch):
    monkeypatch.setattr("agent.cli.load_dotenv", lambda *a, **k: None)
    for name, value in (("LLM_PROVIDER", "openai"), ("LLM_MODEL", "some-model"), ("OPENAI_API_KEY", KEY)):
        monkeypatch.setenv(name, value)
    scripts = [
        [{"sql": "SELECT COUNT(*) AS n FROM students"}, "There are 8."],                 # answered
        [{"sql": "SELECT nope FROM students"}, {"sql": "SELECT nada FROM students"}],    # failed
        [RuntimeError(f"401 invalid key {KEY}")],                                        # provider error carrying the key
    ]
    traces = tmp_path / "traces"
    with caplog.at_level(logging.DEBUG):
        for replies in scripts:
            monkeypatch.setattr("agent.cli.init_chat_model", lambda *a, **k: FakeModel(replies))
            main(["q", "--db", db_path, "--traces", str(traces)])
    out = capsys.readouterr()
    files = list(traces.glob("*.json"))
    assert len(files) == 3
    for text in [out.out, out.err, caplog.text, *(f.read_text() for f in files)]:
        assert KEY not in text
