import sqlite3
from contextlib import closing

import pytest

from agent.graph import build_graph, combine
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
    assert not final.get("error")

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

def test_empty_answer_is_failed(db_path):
    final = build_graph(FakeModel([{"sql": "SELECT 1"}, "  "]), db_path).invoke({"question": "q"})
    assert final["status"] == "failed"
    assert final["answer"].startswith("The question could not be answered.")

def test_long_values_are_cut_in_the_answer_prompt(tmp_path):
    path = tmp_path / "long.db"
    with closing(sqlite3.connect(path)) as rw:
        rw.execute("CREATE TABLE t (v TEXT)")
        rw.execute("INSERT INTO t VALUES (?)", ("x" * 10_000,))
        rw.commit()
    model = FakeModel([{"sql": "SELECT v FROM t"}, "ok"])
    final = build_graph(model, str(path)).invoke({"question": "q"})
    assert len(text_of(model.calls[1])) < 2000
    assert len(final["rows"][0][0]) == 10_000

GOOD = "SELECT COUNT(*) AS n FROM students"
BAD = "SELECT nope FROM students"

def test_retry_then_success(db_path):
    model = FakeModel([{"sql": BAD}, {"sql": GOOD}, "There are 8."])
    final = build_graph(model, db_path).invoke({"question": "How many?"})
    assert (final["status"], final["answer"], final["attempts"]) == ("answered", "There are 8.", 2)
    assert not final.get("error")
    retry_prompt = text_of(model.calls[1])
    assert BAD in retry_prompt and "nope" in retry_prompt.split(BAD, 1)[1]   # previous SQL, then its error
    assert "How many?" in retry_prompt and "CREATE TABLE" in retry_prompt

def test_first_attempt_prompt_has_no_retry_text(db_path):
    model = FakeModel([{"sql": GOOD}, "8"])
    build_graph(model, db_path).invoke({"question": "How many?"})
    assert "no such column" not in text_of(model.calls[0])

def test_two_failures_end_failed(db_path):
    model = FakeModel([{"sql": BAD}, {"sql": "SELECT nada FROM students"}])
    final = build_graph(model, db_path).invoke({"question": "q"})
    assert final["status"] == "failed" and final["attempts"] == 2
    assert final["answer"] == "The question could not be answered. Last error: " + final["error"]
    assert "nada" in final["error"]
    assert len(model.calls) == 2                     # no third attempt, no answer call

def test_guard_rejection_counts_as_attempt(db_path):
    model = FakeModel([{"sql": BAD}, {"sql": "DELETE FROM students"}])
    final = build_graph(model, db_path).invoke({"question": "q"})
    assert final["status"] == "failed" and len(model.calls) == 2

def test_unclear_question_asks_back(db_path):
    choices = ["How many scored at least 50?", "How many scored at least 60?"]
    model = FakeModel([{"clarification": "What counts as passing?", "choices": choices}])
    final = build_graph(model, db_path).invoke({"question": "How many passed?"})
    assert final["status"] == "clarification"
    assert final["answer"] == final["clarification"] == "What counts as passing?"
    assert final["choices"] == choices
    assert "sql" not in final and "rows" not in final and len(model.calls) == 1   # no SQL ran

def test_empty_reply_is_failed(db_path):
    final = build_graph(FakeModel([{}]), db_path).invoke({"question": "q"})
    assert final["status"] == "failed" and "neither SQL nor a question" in final["answer"]

def test_reply_edge_cases(db_path):
    both = FakeModel([{"sql": GOOD, "clarification": "What counts as passing?", "choices": ["a", "b"]}])
    final = build_graph(both, db_path).invoke({"question": "q"})
    assert final["status"] == "clarification" and final["answer"] == "What counts as passing?"
    assert "sql" not in final and "rows" not in final and len(both.calls) == 1   # the SQL is dropped, nothing ran
    bare = FakeModel([{"clarification": "Which one?"}])
    final = build_graph(bare, db_path).invoke({"question": "q"})
    assert (final["status"], final["choices"]) == ("clarification", [])

def test_combine():
    assert combine("How many passed?", "at least 60") == "How many passed? (at least 60)"

def test_single_value_flag_is_kept(db_path):
    model = FakeModel([{"sql": GOOD, "expects_single_value": True}, "8"])
    assert build_graph(model, db_path).invoke({"question": "q"})["expects_single_value"] is True

CLASS_GRADES = """SELECT e.grade AS grade FROM enrollments e JOIN offerings o ON o.id = e.offering_id
JOIN courses c ON c.id = o.course_id WHERE c.title = 'Databases' AND o.semester = '2025-Fall'"""

def run(db_path, reply, *more):
    model = FakeModel([reply, *more])
    return build_graph(model, db_path).invoke({"question": "q"}), model

def test_no_rows_is_nothing_matched(db_path):
    final, model = run(db_path, {"sql": "SELECT name FROM students WHERE name = 'Zed'"})
    assert (final["status"], final["answer"]) == ("empty", "Nothing matched.")
    assert len(model.calls) == 1                     # answer step never called

def test_single_value_expected_but_five_rows(db_path):
    final, model = run(db_path, {"sql": CLASS_GRADES, "expects_single_value": True})
    assert final["status"] == "mismatch"
    assert final["answer"] == "The question asks for a single value, but the query returned 5 rows."
    assert len(final["rows"]) == 5 and len(model.calls) == 1   # rows kept for the trace; model not asked to pick

def test_single_value_expected_but_two_columns(db_path):
    final, _ = run(db_path, {"sql": "SELECT 1 AS a, 2 AS b", "expects_single_value": True})
    assert final["answer"] == "The question asks for a single value, but the query returned 2 columns."

def test_null_average_is_no_value(db_path):
    sql = "SELECT AVG(e.grade) AS average_grade FROM enrollments e WHERE e.offering_id = 6"
    final, model = run(db_path, {"sql": sql, "expects_single_value": True})
    assert (final["status"], final["answer"]) == ("no_value", "No value is available for average_grade.")
    assert len(model.calls) == 1

def test_single_null_is_no_value(db_path):
    final, _ = run(db_path, {"sql": "SELECT NULL AS x", "expects_single_value": True})
    assert final["status"] == "no_value"

def test_all_null_row_without_flag_is_no_value(db_path):
    final, _ = run(db_path, {"sql": "SELECT NULL AS a, NULL AS b"})
    assert final["answer"] == "No value is available for a, b."

def test_zero_is_answered(db_path):
    sql = "SELECT COUNT(*) AS n FROM students WHERE name = 'Zed'"
    final, model = run(db_path, {"sql": sql, "expects_single_value": True}, "There are 0.")
    assert (final["status"], final["answer"]) == ("answered", "There are 0.")
    assert "0" in text_of(model.calls[1])

def test_list_result_without_flag_is_answered(db_path):
    final, _ = run(db_path, {"sql": CLASS_GRADES}, "five values")
    assert final["status"] == "answered"

def test_partly_null_row_is_answered(db_path):
    final, _ = run(db_path, {"sql": "SELECT 1 AS a, NULL AS b"}, "a is 1")
    assert final["status"] == "answered"

def test_check_result_node_exists(db_path):
    assert "check_result" in set(build_graph(FakeModel([]), db_path).get_graph().nodes)

def test_truncated_single_value_message(tmp_path):
    path = tmp_path / "many.db"
    with closing(sqlite3.connect(path)) as rw:
        rw.execute("CREATE TABLE n (i INT)")
        rw.executemany("INSERT INTO n VALUES (?)", [(i,) for i in range(150)])
        rw.commit()
    model = FakeModel([{"sql": "SELECT i FROM n", "expects_single_value": True}])
    final = build_graph(model, str(path)).invoke({"question": "one n"})
    assert final["status"] == "mismatch"
    assert final["answer"] == "The question asks for a single value, but the query returned more than 100 rows."


HIST = [{"question": "How many scored above 70 in X?", "answer": "3.", "sql": "SELECT COUNT(*) AS n FROM t WHERE v > 70"},
        {"question": "Which unit is biggest?", "answer": "Unit A.", "sql": None}]

def test_history_reaches_the_sql_prompt_in_order(db_path):
    model = FakeModel([{"sql": "SELECT 1 AS one", "understood_question": "Which ones scored above 70 in X?"}, "ok"])
    final = build_graph(model, db_path).invoke({"question": "which ones?", "history": HIST})
    sent = text_of(model.calls[0])
    assert "Earlier in this conversation, oldest first:" in sent
    assert sent.index("How many scored above 70 in X?") < sent.index("SELECT COUNT(*) AS n FROM t WHERE v > 70") < sent.index("3.") < sent.index("Which unit is biggest?") < sent.index("Question: which ones?")
    assert final["understood"] == "Which ones scored above 70 in X?"
    assert "Question: Which ones scored above 70 in X?" in text_of(model.calls[1])      # the answer step gets the full question

def test_no_history_no_block(db_path):
    for hist in ({}, {"history": []}):
        model = FakeModel([{"sql": "SELECT 1 AS one"}, "ok"])
        final = build_graph(model, db_path).invoke({"question": "How many?", **hist})
        assert "Earlier in this conversation" not in text_of(model.calls[0])
        assert final["understood"] == "How many?"                                          # falls back to the question

def test_clarification_keeps_the_understood_question(db_path):
    model = FakeModel([{"clarification": "Which cutoff?", "choices": ["A?", "B?"], "understood_question": "How many passed X?"}])
    assert build_graph(model, db_path).invoke({"question": "how many passed"})["understood"] == "How many passed X?"
