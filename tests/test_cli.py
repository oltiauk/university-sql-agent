import re

import pytest

from agent.cli import main
from tests.fakes import FakeModel


def run(capsys, argv, model=None, ask=None):
    code = main(argv, model=model, ask=ask or answers())
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

@pytest.mark.parametrize("bad", ["../outside", "/etc/hosts"])
def test_trace_rejects_paths(tmp_path, capsys, bad):
    code, _, err = run(capsys, ["--trace", bad, "--traces", str(tmp_path)])
    assert code == 2 and "No trace found" in err and "Traceback" not in err

def test_trace_corrupt_file(tmp_path, capsys):
    (tmp_path / "20260101-000000-abcdef.json").write_text("{not json")
    code, _, err = run(capsys, ["--trace", "20260101-000000-abcdef", "--traces", str(tmp_path)])
    assert code == 2 and "Traceback" not in err

def test_model_creation_error(db_path, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("agent.cli.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("LLM_PROVIDER", "openai"); monkeypatch.setenv("LLM_MODEL", "some-model")
    def boom(*a, **k):
        raise ValueError("secret detail sk-XYZ")
    monkeypatch.setattr("agent.cli.init_chat_model", boom)
    code, _, err = run(capsys, ["q", "--db", db_path, "--traces", str(tmp_path)])
    assert code == 2 and "ValueError" in err and "sk-XYZ" not in err and "Traceback" not in err


COUNT = "SELECT COUNT(*) AS n FROM students"
CLARIFY = {"clarification": "What counts as passing?", "choices": ["How many scored at least 50?", "How many scored at least 60?"]}

def answers(*lines):
    it = iter(lines)
    def ask(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return ask

def test_clarification_is_shown_with_numbered_choices(db_path, tmp_path, capsys):
    model = FakeModel([CLARIFY])
    code, out, _ = run(capsys, ["How many passed?", "--db", db_path, "--traces", str(tmp_path)], model, ask=answers(""))
    assert code == 0 and "What counts as passing?" in out
    assert "1. How many scored at least 50?" in out and "2. How many scored at least 60?" in out
    assert len(model.calls) == 1

def test_picking_a_choice_asks_it_as_is(db_path, tmp_path, capsys):
    model = FakeModel([CLARIFY, {"sql": COUNT}, "There are 8."])
    code, out, _ = run(capsys, ["How many passed?", "--db", db_path, "--traces", str(tmp_path)], model, ask=answers("2"))
    assert code == 0 and "There are 8." in out
    assert "Question: How many scored at least 60?" in model.calls[1][1][1]
    assert len(re.findall(r"run: (\S+)", out)) == 2 and len(list(tmp_path.glob("*.json"))) == 2   # two runs, two traces

def test_typed_reply_is_joined_to_the_original(db_path, tmp_path, capsys):
    model = FakeModel([CLARIFY, {"sql": COUNT}, "There are 8."])
    code, out, _ = run(capsys, ["How many passed?", "--db", db_path, "--traces", str(tmp_path)], model, ask=answers("at least 75"))
    assert code == 0 and "There are 8." in out
    assert "Question: How many passed? (at least 75)" in model.calls[1][1][1]

def test_follow_up_edge_inputs(db_path, tmp_path, capsys):
    args = ["How many passed?", "--db", db_path, "--traces", str(tmp_path)]
    for reply_lines in ((), ("",)):                       # input closed; Enter
        model = FakeModel([CLARIFY])
        code, _, err = run(capsys, args, model, ask=answers(*reply_lines))
        assert code == 0 and len(model.calls) == 1 and "Traceback" not in err
    model = FakeModel([CLARIFY, {"sql": COUNT}, "8"])
    run(capsys, args, model, ask=answers("7"))            # out of range: treated as typed text
    assert "Question: How many passed? (7)" in model.calls[1][1][1]

def test_repeated_clarification(db_path, tmp_path, capsys):
    model = FakeModel([CLARIFY, CLARIFY, {"sql": COUNT}, "8"])
    code, out, _ = run(capsys, ["How many passed?", "--db", db_path, "--traces", str(tmp_path)], model, ask=answers("x", "1"))
    assert code == 0 and "8" in out and len(model.calls) == 4

@pytest.mark.parametrize("reply,code_expected", [
    ({"sql": "SELECT name FROM students WHERE name = 'Zed'"}, 0),               # empty
    ({"sql": "SELECT name FROM students", "expects_single_value": True}, 1),    # mismatch
    ({"sql": "SELECT NULL AS x", "expects_single_value": True}, 0),             # no value
])
def test_exit_codes(db_path, tmp_path, capsys, reply, code_expected):
    code, _, _ = run(capsys, ["q", "--db", db_path, "--traces", str(tmp_path)], FakeModel([reply]))
    assert code == code_expected

@pytest.mark.parametrize("replies", [
    [{"sql": "SELECT nope FROM students"}, {"sql": "SELECT nope2 FROM students"}],   # failed twice
    [RuntimeError("x")],                                                             # model raises
])
def test_exit_code_one_on_failure(db_path, tmp_path, capsys, replies):
    code, _, _ = run(capsys, ["q", "--db", db_path, "--traces", str(tmp_path)], FakeModel(replies))
    assert code == 1

def test_follow_up_odd_numbers(db_path, tmp_path, capsys):
    args = ["How many passed?", "--db", db_path, "--traces", str(tmp_path)]
    for reply in ("\u00b2", "9" * 5000, "02"):
        model = FakeModel([CLARIFY, {"sql": COUNT}, "8"])
        run(capsys, args, model, ask=answers(reply))
        assert "Question: How many passed? (" in model.calls[1][1][1]
    model = FakeModel([CLARIFY, {"sql": COUNT}, "8"])
    run(capsys, args, model, ask=answers(" 2 "))
    assert "Question: How many scored at least 60?" in model.calls[1][1][1]
