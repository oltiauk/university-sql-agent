"""Opt-in tests against a real model: RUN_LIVE=1 .venv/bin/pytest tests/test_live.py"""
import os
import re

import pytest

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from agent.graph import build_graph, combine
from agent.server import history_from
from agent.trace import format_trace, run_traced
from tests.known_answer import check_answer, check_labelled, check_rows
from tests.test_known_answer import EXPECTED, GA, N

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="live tests run only with RUN_LIVE=1")


def run(question, db_path, tmp_path):
    load_dotenv()
    model = init_chat_model(os.environ["LLM_MODEL"], model_provider=os.environ["LLM_PROVIDER"])
    return run_traced(build_graph(model, db_path), question, database=db_path, provider=os.environ["LLM_PROVIDER"],
                      model=os.environ["LLM_MODEL"], traces_dir=str(tmp_path / "traces"))


def chat(turns, db_path, tmp_path):
    """play the turns one after another as one chat; a turn is a question or an (original, reply) pair"""
    records = []
    for turn in turns:
        question = combine(*turn) if isinstance(turn, tuple) else turn
        load_dotenv()
        model = init_chat_model(os.environ["LLM_MODEL"], model_provider=os.environ["LLM_PROVIDER"])
        records.append(run_traced(build_graph(model, db_path), question, database=db_path, provider=os.environ["LLM_PROVIDER"],
                                  model=os.environ["LLM_MODEL"], traces_dir=str(tmp_path / "traces"),
                                  history=history_from(records), chat="a" * 12))
    return records


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
           "execute_sql ran" if any(s["node"] == "execute_sql" for s in rec["steps"]) else None,
           None if all(re.search(r"\d", c) for c in choices) else f"a choice has no concrete cutoff: {choices}")
    picked = run(choices[0], db_path, tmp_path)       # a picked choice is answered, not asked about again
    verify(picked, "answered", check_answer(picked["answer"], [], []))


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
           check_answer(rec["answer"], ["Dracula", "Dune", "Emma"], ["Ulysses", "Beloved", "Walden", "Persuasion"]))


def test_missing_data_is_said_not_invented(db_path, tmp_path):
    rec = run("What is each teacher's email address?", db_path, tmp_path)
    choices = rec["steps"][-1]["output"].get("choices", [])
    invented = [c for c in choices if "@" in c or "format" in c.lower() or "example" in c.lower()]
    verify(rec, "clarification",
           None if "email" in rec["answer"].lower() else "the reply does not say what is missing",
           f"a choice invents a rule: {invented}" if invented else None,
           "execute_sql ran" if any(s["node"] == "execute_sql" for s in rec["steps"]) else None)
    if choices:      # an offered question must be answerable from what exists: asking it gives the names
        follow = run(choices[0], db_path, tmp_path)
        verify(follow, "answered", check_answer(follow["answer"], ["Lira Hoxha", "Besnik Krasniqi", "Mira Berisha"]))


def test_averages_are_rounded_to_two_places(db_path, tmp_path):
    rec = run("What is the average grade in the classes taught by Besnik Krasniqi?", db_path, tmp_path)
    verify(rec, "answered",
           rows_problem(rec, [{"value": 91.3}], {"value": ("",)}),
           None if "91.33" in rec["answer"] and "91.333" not in rec["answer"] else f"not rounded to two places: {rec['answer']}")
    value = next(s["output"]["rows"][0][0] for s in rec["steps"] if s["node"] == "execute_sql")
    assert abs(value - 91.3333333333) < 1e-6, f"the trace should keep the full value, got {value}"


def test_precision_asked_for_is_used(db_path, tmp_path):
    rec = run("What is the average grade in the classes taught by Besnik Krasniqi, to 4 decimal places?", db_path, tmp_path)
    verify(rec, "answered", None if "91.3333" in rec["answer"] else f"asked for 4 places: {rec['answer']}")


def test_short_numbers_are_left_alone(db_path, tmp_path):
    rec = run("What is the average grade in the Algorithms class in Fall 2025?", db_path, tmp_path)   # 93.5
    verify(rec, "answered", None if "93.5" in rec["answer"] and "93.50" not in rec["answer"] else f"93.5 was changed: {rec['answer']}")


def test_the_owners_flow(db_path, tmp_path):
    recs = chat(["How many students passed Databases in Fall 2025?",
                 ("How many students passed Databases in Fall 2025?", "more than 70"),
                 "which ones?"], db_path, tmp_path)
    assert recs[0]["status"] == "clarification", format_trace(recs[0])
    verify(recs[1], "answered", rows_problem(recs[1], [{"value": 3}], {"value": ("",)}), check_answer(recs[1]["answer"], [3]))
    last = recs[2]
    verify(last, "answered", rows_problem(last, EXPECTED, N), check_answer(last["answer"], ["Arta", "Blerim", "Dren"], ["Era", "Fatos"]),
           None if all(w in last["understood"].lower() for w in ("70", "databases", "2025")) else f"understood: {last['understood']}")


def test_follow_up_changes_one_detail(db_path, tmp_path):
    recs = chat(["What is the average grade in Databases in Fall 2025?", "And in Spring 2026?"], db_path, tmp_path)
    verify(recs[1], "answered", rows_problem(recs[1], [{"value": 91.3}], {"value": ("",)}), check_answer(recs[1]["answer"], [91.33]))


def test_a_complete_question_ignores_the_earlier_turns(db_path, tmp_path):
    recs = chat(["How many students scored more than 70 in Databases in Fall 2025?", "How many teachers are there?"], db_path, tmp_path)
    verify(recs[1], "answered", rows_problem(recs[1], [{"value": 3}], {"value": ("",)}), check_answer(recs[1]["answer"], [3]))


def test_a_new_chat_has_forgotten_the_cutoff(db_path, tmp_path):
    chat(["How many students scored more than 70 in Databases in Fall 2025?"], db_path, tmp_path)
    fresh = run("which ones?", db_path, tmp_path)          # no chat, no history: the old cutoff must be gone
    sqls = " ".join(s["output"].get("sql", "") for s in fresh["steps"] if s["node"] == "generate_sql")
    assert fresh["history"] == [] and "70" not in sqls and "70" not in fresh["understood"], format_trace(fresh)
