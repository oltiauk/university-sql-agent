"""Run the example questions and save their traces, an index and a README.

    python examples/make_examples.py          scripted examples only (fake model, no key)
    python examples/make_examples.py --live   also the live ones, with the model from the environment
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))    # so running the file directly finds agent/, db/ and tests/

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from agent.graph import build_graph
from agent.trace import run_traced
from db.build import build
from tests.fakes import FakeModel    # ponytail: depends on a test helper; move it if the tests ever ship separately

SCRIPTED_LABEL = "Scripted: produced with a fake model, because a real model does not fail on demand."
CLASS_GRADES = """SELECT e.grade AS grade FROM enrollments e JOIN offerings o ON o.id = e.offering_id
JOIN courses c ON c.id = o.course_id WHERE c.title = 'Databases' AND o.semester = '2025-Fall'"""
AVG = "What is the average grade in Databases in Fall 2025?"
COUNT = "How many students are there?"
KEPT = "20261126-105423-317698"    # a real run that gave a wrong answer; kept as recorded, never re-run
KEPT_NOTE = """## A wrong answer that looked right

Kept as recorded from a real run. It is not regenerated.

The query narrows the rows to one student first and then averages over what is left, so the "class average" is
that student's own grade: 95.0 instead of 78.0. The SQL is valid, the result has the expected shape, and the run
ends as `answered`. Nothing at run time objects. The known-answer test caught it, because it expects 78.0 in the
average column, and the trace shows where it went wrong: the `generate_sql` step.

It happened about once in 20 runs of this question. A rule was then added to the SQL prompt (a figure for a whole
group is computed over every row of the group, not over rows already narrowed to one item). In 20 runs before the
rule 19 were correct; in 20 runs after it all 20 were. That is a lower rate on a small sample, not a guarantee.
"""

EXAMPLES = [
    dict(id="scripted-retry", title="A query that fails and is corrected", db="university.db", question=COUNT,
         replies=[{"sql": "SELECT COUNT(*) AS n FROM student"},
                  {"sql": "SELECT COUNT(*) AS student_count FROM students", "expects_single_value": True},
                  "There are 8 students."]),
    dict(id="scripted-failed", title="A query that fails twice", db="university.db", question=COUNT,
         replies=[{"sql": "SELECT COUNT(*) AS n FROM student"}, {"sql": "SELECT COUNT(*) AS n FROM pupils"}]),
    dict(id="scripted-mismatch", title="One value asked for, five returned", db="university.db", question=AVG,
         replies=[{"sql": CLASS_GRADES, "expects_single_value": True}]),
    dict(id="aggregate", title="Aggregation", db="university.db", question=AVG),
    dict(id="join-above-average", title="Join across four tables, with a subquery", db="university.db",
         question="Which students scored above the class average in Databases in Fall 2025?"),
    dict(id="filter-semester", title="Filter by semester", db="university.db",
         question="How many enrolments are there in Spring 2026?"),
    dict(id="filter-teacher", title="Filter by teacher", db="university.db",
         question="What is the average grade in the classes taught by Besnik Krasniqi?"),
    dict(id="filter-student", title="Filter by student", db="university.db",
         question="Which courses did Arta take, and what grade did she get in each?"),
    dict(id="clarification", title="An unclear question", db="university.db",
         question="How many students passed Databases in Fall 2025?"),
    dict(id="no-value", title="Nothing to average", db="university.db",
         question="What is the average grade in Databases in Fall 2030?"),
    dict(id="library", title="A different database, same agent", db="library.db",
         question="Which books are currently on loan?"),
]

INTRO = f"""# Examples

Example questions put to the agent, each with the SQL that ran, the answer and a saved trace of the run.

Each trace is a JSON file under `traces/`, named by its run id. Click the link in an example to read it on
GitHub; no key and nothing installed is needed.

Print one locally by its run id:

    python -m agent.cli --trace <run_id> --traces examples/traces

Ask a question of your own, then open your own trace with the run id the command prints back:

    python -m agent.cli "<question>"
    python -m agent.cli --trace <the run id it printed>

Examples marked "Scripted" were produced with a fake model that replays fixed replies, because a real model
does not fail on demand. The rest were run with a real model.

Regenerate them (from the repository root): `python examples/make_examples.py` for the scripted ones,
`python examples/make_examples.py --live` for all of them (needs the model settings in `.env`). A run replaces
the trace of each example it runs.
"""


def real_model():
    load_dotenv()
    return init_chat_model(os.environ["LLM_MODEL"], model_provider=os.environ["LLM_PROVIDER"]), \
        os.environ["LLM_PROVIDER"], os.environ["LLM_MODEL"]


def readme(index, traces):
    parts = [INTRO]
    for ex in EXAMPLES:
        e = index.get(ex["id"])
        if not e:
            continue
        parts.append(f"## {e['title']}\n")
        if e["scripted"]:
            parts.append(SCRIPTED_LABEL + "\n")
        parts.append(f"Command: `{e['command']}`\n\nQuestion: {e['question']}\n\nStatus: {e['status']}\n\n"
                     f"Answer: {e['answer']}\n")
        if e["sql"]:
            parts.append(f"SQL that ran:\n\n```sql\n{e['sql']}\n```\n")
        parts.append(f"Run id: `{e['run_id']}`, trace: [traces/{e['run_id']}.json](traces/{e['run_id']}.json)\n")
    kept = traces / f"{KEPT}.json"
    if kept.exists():
        r = json.loads(kept.read_text())
        sql = next(s["output"]["sql"] for s in r["steps"] if s["node"] == "generate_sql")
        parts.append(KEPT_NOTE + f"\nQuestion: {r['question']}\n\nStatus: {r['status']}\n\nAnswer: {r['answer']}\n\n"
                     f"SQL that ran:\n\n```sql\n{sql}\n```\n\nRun id: `{KEPT}`, trace: [traces/{KEPT}.json](traces/{KEPT}.json)\n")
    return "\n".join(parts)


def make(live: bool, model=None, out_dir="examples") -> list[dict]:
    out = Path(out_dir).resolve()
    traces = out / "traces"
    index_path = out / "examples.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    for db, schema, seed in (("university.db", "schema.sql", "seed.sql"), ("library.db", "library_schema.sql", "library_seed.sql")):
        build(ROOT / "db" / schema, ROOT / "db" / seed, ROOT / db)
    chosen = [ex for ex in EXAMPLES if live or "replies" in ex]
    live_model = (model, "injected", "injected") if model or not live else real_model()
    entries = []
    cwd = os.getcwd()
    os.chdir(ROOT)    # the traces record the relative database name, so it must resolve from here
    try:
        for ex in chosen:
            scripted = "replies" in ex
            m, provider, name = (FakeModel(ex["replies"]), "scripted", "fake") if scripted else live_model
            record = run_traced(build_graph(m, ex["db"]), ex["question"], database=ex["db"],
                                provider=provider, model=name, traces_dir=str(traces))
            if record["status"] == "error":
                (traces / f"{record['run_id']}.json").unlink()
                raise SystemExit(f"example {ex['id']} stopped: {record['answer']}")
            old = index.get(ex["id"])
            if old:
                (traces / f"{old['run_id']}.json").unlink(missing_ok=True)
            sqls = [s["output"]["sql"] for s in record["steps"] if s["node"] == "generate_sql" and s["output"].get("sql")]
            command = ("python examples/make_examples.py" if scripted else
                       f'python -m agent.cli --db {ex["db"]} "{ex["question"]}"')
            entry = index[ex["id"]] = dict(id=ex["id"], run_id=record["run_id"], title=ex["title"], database=ex["db"],
                                                  command=command, question=ex["question"], status=record["status"],
                                                  answer=record["answer"], sql=sqls[-1] if sqls else None, scripted=scripted)
            entries.append(entry)
            index_path.write_text(json.dumps(index, indent=2) + "\n")
    finally:
        os.chdir(cwd)
        out.mkdir(parents=True, exist_ok=True)
        (out / "README.md").write_text(readme(index, traces))
    return entries


if __name__ == "__main__":
    make(live="--live" in sys.argv[1:], out_dir=ROOT / "examples")
