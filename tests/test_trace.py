import json
import sqlite3
from contextlib import closing

from agent.graph import build_graph
from agent.trace import format_trace, load_trace, run_traced
from tests.fakes import FakeModel
from tests.test_graph import ABOVE_AVG, QUESTION


def traced(model, db_path, tmp_path, question=QUESTION):
    return run_traced(build_graph(model, db_path), question, database=db_path, provider="fake",
                      model="fake-1", traces_dir=str(tmp_path / "traces"))

def test_trace_records_the_full_path(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": ABOVE_AVG}, "Arta, Blerim and Dren."]), db_path, tmp_path)
    assert set(rec) == {"run_id", "started_at", "question", "database", "provider", "model", "status", "answer", "steps"}
    assert (rec["question"], rec["database"], rec["provider"], rec["model"]) == (QUESTION, db_path, "fake", "fake-1")
    assert (rec["status"], rec["answer"]) == ("answered", "Arta, Blerim and Dren.")
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql", "execute_sql", "answer"]
    for step in rec["steps"]:
        assert set(step) == {"node", "at", "duration_ms", "output"} and step["duration_ms"] >= 0
    assert "CREATE TABLE" in rec["steps"][0]["output"]["schema"]
    assert rec["steps"][1]["output"] == {"sql": ABOVE_AVG, "expects_single_value": False, "attempts": 1}
    assert rec["steps"][2]["output"]["columns"] == ["name"]
    assert rec["steps"][3]["output"]["answer"] == "Arta, Blerim and Dren."

def test_trace_is_saved_and_loads_back(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": ABOVE_AVG}, "ok"]), db_path, tmp_path)
    saved = load_trace(rec["run_id"], str(tmp_path / "traces"))
    assert (tmp_path / "traces" / f"{rec['run_id']}.json").exists()
    assert saved["steps"][2]["output"]["rows"] == [["Arta"], ["Blerim"], ["Dren"]]
    assert saved == json.loads(json.dumps(rec, default=repr))

def test_failed_run_trace(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": "SELECT nope FROM students"}, {"sql": "SELECT nope FROM students"}]), db_path, tmp_path, "q")
    assert rec["status"] == "failed"
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql", "execute_sql", "generate_sql", "execute_sql"]
    assert "nope" in rec["steps"][2]["output"]["error"]

def test_trace_saved_when_model_raises(db_path, tmp_path):
    rec = traced(FakeModel([RuntimeError("401 bad key sk-SECRET123 at https://api.example/v1")]), db_path, tmp_path, "q")
    assert rec["status"] == "error"
    assert rec["answer"] == "Run stopped at generate_sql: RuntimeError."
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql"]      # the failed step is shown
    assert rec["steps"][-1]["output"] == {"error": "RuntimeError"}
    saved_text = (tmp_path / "traces" / f"{rec['run_id']}.json").read_text()
    assert "sk-SECRET123" not in saved_text and "api.example" not in saved_text    # provider text is not stored
    assert "generate_sql" in format_trace(rec) and "RuntimeError" in format_trace(rec)

def test_trace_names_the_answer_step_when_it_fails(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": "SELECT 1"}, RuntimeError("boom")]), db_path, tmp_path, "q")
    assert rec["answer"] == "Run stopped at answer: RuntimeError."
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql", "execute_sql", "answer"]

def test_trace_handles_bytes(tmp_path):
    path = tmp_path / "b.db"
    with closing(sqlite3.connect(path)) as rw:
        rw.execute("CREATE TABLE t (b BLOB)"); rw.execute("INSERT INTO t VALUES (X'00FF')"); rw.commit()
    rec = traced(FakeModel([{"sql": "SELECT b FROM t"}, "a blob"]), str(path), tmp_path, "q")
    assert rec["status"] == "answered"
    assert load_trace(rec["run_id"], str(tmp_path / "traces"))["steps"][2]["output"]["rows"]

def test_run_ids_unique(db_path, tmp_path):
    ids = {traced(FakeModel([{"sql": "SELECT 1"}, "one"]), db_path, tmp_path, "q")["run_id"] for _ in range(5)}
    assert len(ids) == 5

def test_format_trace_shows_every_stage(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": ABOVE_AVG}, "Arta, Blerim and Dren."]), db_path, tmp_path)
    text = format_trace(rec)
    for expected in (rec["run_id"], QUESTION, "load_schema", "generate_sql", "execute_sql", "answer",
                     "c.title = 'Databases'", "Blerim", "Arta, Blerim and Dren.", "answered"):
        assert expected in text
    assert text.index("generate_sql") < text.index("execute_sql") < text.index("Arta, Blerim and Dren.")

def test_trace_saved_on_keyboard_interrupt(db_path, tmp_path):
    rec = traced(FakeModel([KeyboardInterrupt()]), db_path, tmp_path)
    assert rec["status"] == "error"
    assert rec["answer"] == "Run stopped at generate_sql: KeyboardInterrupt."
    assert (tmp_path / "traces" / f"{rec['run_id']}.json").exists()

def test_retry_trace_shows_both_attempts(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": "SELECT nope FROM students"}, {"sql": "SELECT 1 AS one"}, "one"]), db_path, tmp_path, "q")
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql", "execute_sql", "generate_sql", "execute_sql", "answer"]
    assert "nope" in rec["steps"][2]["output"]["error"]
    assert rec["steps"][3]["output"]["sql"] == "SELECT 1 AS one" and rec["status"] == "answered"

def test_clarification_trace(db_path, tmp_path):
    rec = traced(FakeModel([{"clarification": "Which one?", "choices": ["A?", "B?"]}]), db_path, tmp_path, "q")
    assert rec["status"] == "clarification" and rec["answer"] == "Which one?"
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql"]
    assert rec["steps"][1]["output"]["choices"] == ["A?", "B?"]
