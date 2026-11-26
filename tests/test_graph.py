import sqlite3
from contextlib import closing

import pytest

from agent.graph import build_graph
from tests.fakes import FakeModel

ABOVE_AVG = """SELECT s.name FROM enrollments e
JOIN students s ON s.id = e.student_id
JOIN offerings o ON o.id = e.offering_id
JOIN courses c ON c.id = o.course_id
WHERE c.title = 'Databases' AND o.semester = '2025-Fall'
AND e.grade > (SELECT AVG(e2.grade) FROM enrollments e2 WHERE e2.offering_id = o.id)
ORDER BY s.name"""
QUESTION = "Which students scored above the class average in Databases in Fall 2025?"

def text_of(messages):
    return "\n".join(content for _, content in messages)

def test_happy_path(db_path):
    model = FakeModel([{"sql": ABOVE_AVG}, "Arta, Blerim and Dren."])
    final = build_graph(model, db_path).invoke({"question": QUESTION})
    assert final["sql"] == ABOVE_AVG
    assert final["columns"] == ["name"]
    assert final["rows"] == [("Arta",), ("Blerim",), ("Dren",)]
    assert final["truncated"] is False
    assert final["answer"] == "Arta, Blerim and Dren."
    assert final["status"] == "answered"
    assert "error" not in final

def test_node_names(db_path):
    nodes = set(build_graph(FakeModel([]), db_path).get_graph().nodes)
    assert {"load_schema", "generate_sql", "execute_sql", "answer"} <= nodes

def test_question_and_schema_reach_the_model(db_path):
    model = FakeModel([{"sql": "SELECT 1"}, "one"])
    build_graph(model, db_path).invoke({"question": QUESTION})
    sent = text_of(model.calls[0])
    assert QUESTION in sent
    assert "CREATE TABLE offerings" in sent and "2025-Fall" in sent   # schema text with sample rows

def test_sql_and_rows_reach_the_answer_step(db_path):
    model = FakeModel([{"sql": ABOVE_AVG}, "ok"])
    build_graph(model, db_path).invoke({"question": QUESTION})
    sent = text_of(model.calls[1])
    assert QUESTION in sent and ABOVE_AVG in sent
    assert "Arta" in sent and "Blerim" in sent and "Dren" in sent and "Era" not in sent

@pytest.mark.parametrize("bad_sql", ["SELECT nope FROM students", "DELETE FROM students"])
def test_failing_sql_ends_failed_without_answer_call(db_path, bad_sql):
    model = FakeModel([{"sql": bad_sql}])
    final = build_graph(model, db_path).invoke({"question": "q"})
    assert final["status"] == "failed"
    assert final["error"]
    assert final["answer"].startswith("The question could not be answered.") and final["error"] in final["answer"]
    assert len(model.calls) == 1          # the answer step was never called

def test_truncated_result_is_flagged_to_the_answer_step(tmp_path):
    path = tmp_path / "many.db"
    with closing(sqlite3.connect(path)) as rw:
        rw.execute("CREATE TABLE n (i INT)")
        rw.executemany("INSERT INTO n VALUES (?)", [(i,) for i in range(150)])
        rw.commit()
    model = FakeModel([{"sql": "SELECT i FROM n"}, "first 100"])
    final = build_graph(model, str(path)).invoke({"question": "list n"})
    assert final["truncated"] is True and len(final["rows"]) == 100
    assert "first 100 rows" in text_of(model.calls[1])

def test_braces_in_question(db_path):
    model = FakeModel([{"sql": "SELECT 1"}, "one"])
    final = build_graph(model, db_path).invoke({"question": "what is {x} and {0}?"})
    assert final["status"] == "answered" and "{x}" in text_of(model.calls[0])
