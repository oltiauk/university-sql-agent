import re

import pytest

from agent.cli import main
from tests.fakes import FakeModel


def run(capsys, argv, model=None):
    code = main(argv, model=model)
    out = capsys.readouterr()
    return code, out.out, out.err

def test_ask_prints_answer_and_run_id(db_path, tmp_path, capsys):
    traces = str(tmp_path / "traces")
    code, out, _ = run(capsys, ["How many rows?", "--db", db_path, "--traces", traces],
                       FakeModel([{"sql": "SELECT COUNT(*) AS n FROM students"}, "There are 8."]))
    assert code == 0 and "There are 8." in out
    run_id = re.search(r"run: (\S+)", out).group(1)
    assert (tmp_path / "traces" / f"{run_id}.json").exists()
    assert "SELECT" not in out            # the answer only; SQL and rows live in the trace

def test_show_trace(db_path, tmp_path, capsys):
    traces = str(tmp_path / "traces")
    _, out, _ = run(capsys, ["q", "--db", db_path, "--traces", traces],
                    FakeModel([{"sql": "SELECT COUNT(*) AS n FROM students"}, "There are 8."]))
    run_id = re.search(r"run: (\S+)", out).group(1)
    code, out, _ = run(capsys, ["--trace", run_id, "--traces", traces])
    assert code == 0 and "generate_sql" in out and "SELECT COUNT(*) AS n FROM students" in out

def test_trace_unknown_run(tmp_path, capsys):
    code, _, err = run(capsys, ["--trace", "nope", "--traces", str(tmp_path)])
    assert code == 2 and "nope" in err and "Traceback" not in err

def test_missing_database(tmp_path, capsys):
    code, _, err = run(capsys, ["q", "--db", str(tmp_path / "none.db"), "--traces", str(tmp_path)], FakeModel([]))
    assert code == 2 and "none.db" in err

@pytest.mark.parametrize("missing", ["LLM_PROVIDER", "LLM_MODEL"])
def test_missing_env(db_path, tmp_path, capsys, monkeypatch, missing):
    monkeypatch.setattr("agent.cli.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("LLM_PROVIDER", "openai"); monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.delenv(missing)
    code, _, err = run(capsys, ["q", "--db", db_path, "--traces", str(tmp_path)])
    assert code == 2 and missing in err and "Traceback" not in err

def test_no_arguments(capsys):
    code, _, err = run(capsys, [])
    assert code == 2 and "question" in err.lower()
