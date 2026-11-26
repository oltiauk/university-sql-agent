"""Opt-in tests against a real model: RUN_LIVE=1 .venv/bin/pytest tests/test_live.py"""
import os

import pytest

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from agent.graph import build_graph
from agent.trace import format_trace, run_traced
from tests.known_answer import check_answer, check_labelled, check_rows
from tests.test_known_answer import EXPECTED, GA, N

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="live tests run only with RUN_LIVE=1")


def run(question, db_path, tmp_path):
    load_dotenv()
    model = init_chat_model(os.environ["LLM_MODEL"], model_provider=os.environ["LLM_PROVIDER"])
    return run_traced(build_graph(model, db_path), question, database=db_path, provider=os.environ["LLM_PROVIDER"],
                      model=os.environ["LLM_MODEL"], traces_dir=str(tmp_path / "traces"))


def rows_problem(rec, expected, names):
    out = next((s["output"] for s in reversed(rec["steps"]) if s["node"] == "execute_sql" and "rows" in s["output"]), None)
    return check_rows(out["columns"], out["rows"], expected, names) if out else "no successful execute_sql step"


def verify(rec, status, *problems):
    problem = next((p for p in (None if rec["status"] == status else f"status {rec['status']}", *problems) if p), None)
    assert problem is None, f"{problem}\n{format_trace(rec)}"


def test_above_class_average(db_path, tmp_path):
    rec = run("Which students scored above the class average in Databases in Fall 2025?", db_path, tmp_path)
    verify(rec, "answered", rows_problem(rec, EXPECTED, N),
           check_answer(rec["answer"], ["Arta", "Blerim", "Dren"], ["Era", "Fatos"]))


def test_grade_and_class_average(db_path, tmp_path):
    rec = run("What is Arta's grade in Databases in Fall 2025, and what is the class average? "
              "Answer with two labels: grade and class average.", db_path, tmp_path)
    verify(rec, "answered",
           rows_problem(rec, [{"grade": 95, "average": 78.0}], GA),
           check_labelled(rec["answer"], {"grade": 95, "class average": 78}))


def test_who_teaches(db_path, tmp_path):
    rec = run("Who teaches Databases in Fall 2025?", db_path, tmp_path)
    verify(rec, "answered",
           rows_problem(rec, [{"teacher": "Lira Hoxha"}], {"teacher": ("name", "teacher")}),
           check_answer(rec["answer"], ["Lira Hoxha"]))


def test_distinct_people(db_path, tmp_path):
    rec = run("How many different students does Besnik Krasniqi teach?", db_path, tmp_path)
    verify(rec, "answered",
           rows_problem(rec, [{"value": 5}], {"value": ("",)}), check_answer(rec["answer"], [5]))


def test_passed_asks_back(db_path, tmp_path):
    rec = run("How many students passed Databases in Fall 2025?", db_path, tmp_path)
    choices = rec["steps"][-1]["output"].get("choices", [])
    verify(rec, "clarification",
           None if 2 <= len(choices) <= 3 else f"{len(choices)} choices",
           "execute_sql ran" if any(s["node"] == "execute_sql" for s in rec["steps"]) else None)


def test_choices_keep_conditions(db_path, tmp_path):
    rec = run("Who scored at least 90 in Databases?", db_path, tmp_path)
    if rec["status"] == "clarification":
        choices = rec["steps"][-1]["output"].get("choices", [])
        verify(rec, "clarification", None if choices and all("90" in c for c in choices) else f"a choice lost the 90: {choices}")
    else:
        verify(rec, "answered",
               rows_problem(rec, [{"student": "Arta"}, {"student": "Gent"}, {"student": "Hana"}], N))


def test_library_on_loan(library_path, tmp_path):
    rec = run("Which books are currently on loan?", library_path, tmp_path)
    verify(rec, "answered",
           rows_problem(rec, [{"book": "Dracula"}, {"book": "Dune"}, {"book": "Emma"}], {"book": ("title", "book")}),
           check_answer(rec["answer"], ["Dracula", "Dune", "Emma"], ["Ulysses", "Beloved", "Walden"]))
