from contextlib import closing

from agent.cli import main
from agent.db import connect, get_schema, run_query
from agent.graph import build_graph
from agent.prompts import ANSWER_SYSTEM, GENERATE_SQL_SYSTEM
from tests.fakes import FakeModel

ON_LOAN = """SELECT b.title AS book_title FROM books b
JOIN loans l ON l.book_id = b.id WHERE l.returned_on IS NULL ORDER BY b.title"""
QUESTION = "Which books are currently on loan?"

def test_three_tables(library_path):
    with closing(connect(library_path)) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"books", "members", "loans"}

def test_on_loan_differs_from_ever_loaned(library_path):
    with closing(connect(library_path)) as conn:
        assert run_query(conn, ON_LOAN)[1] == [("Dracula",), ("Dune",), ("Emma",)]
        ever = run_query(conn, "SELECT COUNT(DISTINCT book_id) AS n FROM loans")[1]
    assert ever == [(6,)]

def test_sample_rows_show_an_open_loan(library_path):
    with closing(connect(library_path)) as conn:
        schema = get_schema(conn)
    loans_part = schema.split("CREATE TABLE loans")[1]
    assert "None" in loans_part                     # an open loan is among the three sample rows
    assert "NULL while the book is still out" in loans_part    # the schema's own comment travels with it

def test_same_graph_answers_on_the_library(library_path):
    model = FakeModel([{"sql": ON_LOAN}, "Dracula, Dune and Emma are on loan."])
    final = build_graph(model, library_path).invoke({"question": QUESTION})
    assert final["status"] == "answered" and final["rows"] == [("Dracula",), ("Dune",), ("Emma",)]
    sent = "\n".join(text for _, text in model.calls[0])
    assert "CREATE TABLE loans" in sent and QUESTION in sent
    assert "students" not in sent and "enrollments" not in sent       # nothing of the other database

def test_prompts_do_not_mention_either_database():
    text = (GENERATE_SQL_SYSTEM + ANSWER_SYSTEM).lower()
    for word in ("book", "loan", "member", "library", "student", "teacher", "course", "grade", "semester"):
        assert word not in text

def test_command_line_needs_only_db(library_path, tmp_path, capsys):
    model = FakeModel([{"sql": ON_LOAN}, "Dracula, Dune and Emma are on loan."])
    code = main([QUESTION, "--db", library_path, "--traces", str(tmp_path / "t")], model=model)
    assert code == 0 and "Dracula, Dune and Emma" in capsys.readouterr().out

def test_result_checks_work_on_the_library(library_path):
    model = FakeModel([{"sql": "SELECT title FROM books WHERE title = 'Nope'"}])
    assert build_graph(model, library_path).invoke({"question": "q"})["answer"] == "Nothing matched."
