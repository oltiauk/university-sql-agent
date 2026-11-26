# M4 Proof and Paperwork Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show that the unchanged agent answers questions on a different database with only `--db` changed, and deliver the examples, saved traces and documents the assignment asks for.

**Architecture:** A second, smaller database (a library) is added as two SQL files and built with the existing `db/build.py`. No file under `agent/` changes in this milestone: that is the proof. A small script produces the example runs and their traces. Two documents explain the design and what production would need.

**Tech Stack:** unchanged. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-25-university-sql-agent-design.md` (sections Known limits, M4; and "PDF requirements and where each is met")

## Global Constraints

- **Nothing under `agent/` is modified in this milestone.** The graph and the prompts stay byte-for-byte as they are. If a task seems to need a change there, stop and report.
- The library demo is: build `library.db`, then `python -m agent.cli "Which books are currently on loan?" --db library.db`. The only difference from the university run is `--db`.
- Expected answer to that question, from the seed: Dracula, Dune, Emma (three books; six different books have been loaned at some time).
- The default test run uses no network and no real model. Live tests run only with `RUN_LIVE=1`. Implementers never run live tests, never run the real model, and never read `.env`.
- Example runs that a real model does not produce on demand (a retried run, a failed run, a mismatch run) are produced with the scripted fake model and labelled "scripted" wherever they appear.
- Documents state limits honestly. Nothing in the README or `docs/production.md` may claim that an answer is verified correct; status `answered` means the query ran and an answer was worded from its rows.
- No API key, no absolute path from this machine, and no personal detail appears in any committed file, including saved traces.
- pytest treats warnings as errors; test output must be clean.
- Publishing the repository is not part of any task. It is done by the owner's explicit decision at the milestone close.

## Review Focus

1. The library seed's first three loan rows must include one that is still out, because the agent only sees three sample rows per table and learns from them that `returned_on` is NULL for a book on loan. (Task 1, `test_sample_rows_show_an_open_loan`)
2. A query that forgets "currently" returns six books instead of three: the test data must tell the two apart. (Task 1, `test_on_loan_differs_from_ever_loaned`)
3. `.gitignore` has `traces/`, which also ignores `examples/traces/`: saved example traces would silently not be committed. (Task 2, Step 1)
4. A saved trace records the `database` path it was run with: example traces must be produced with relative paths so no machine-specific path is committed. (Task 2, `test_examples_have_no_absolute_paths`)
5. Running the examples script twice must replace the examples, not pile up duplicates. (Task 2, `test_scripted_examples_are_reproducible`)

---

### Task 1: The library database

**Files:**
- Create: `db/library_schema.sql`, `db/library_seed.sql`
- Modify: `tests/conftest.py` (one fixture), `tests/test_live.py` (one test)
- Test: `tests/test_library.py`

**Interfaces:**
- Produces: `python db/build.py db/library_schema.sql db/library_seed.sql library.db` builds the library database (`db/build.py` already takes three arguments).
- Produces: pytest fixture `library_path` (a freshly built library database in `tmp_path`, as `str`), built the same way the existing `db_path` fixture builds the university one.

- [ ] **Step 1: Write the failing tests** in `tests/test_library.py`

```python
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
```

Add to `tests/test_live.py`, in the style of its existing tests:

| Test | Question | Expected |
|---|---|---|
| `test_library_on_loan` | Which books are currently on loan? (run against the `library_path` database) | status `answered`; rows `[{"book": "Dracula"}, {"book": "Dune"}, {"book": "Emma"}]` with names `{"book": ("title", "book")}`; the answer mentions Dracula, Dune and Emma and does not mention Ulysses, Beloved or Walden |

If the live helper in `tests/test_live.py` is hard-wired to the `db_path` fixture, give it a database-path parameter rather than copying it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_library.py -q`
Expected: FAIL, fixture `library_path` not found.

- [ ] **Step 3: Write `db/library_schema.sql`**, exactly:

```sql
CREATE TABLE books (
    id     INTEGER PRIMARY KEY,
    title  TEXT NOT NULL,
    author TEXT NOT NULL
);

CREATE TABLE members (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE loans (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    member_id   INTEGER NOT NULL REFERENCES members(id),
    loaned_on   TEXT NOT NULL,   -- date, YYYY-MM-DD
    returned_on TEXT             -- date, NULL while the book is still out
);
```

The two comments sit inside the `CREATE TABLE` statement on purpose: SQLite keeps them, so the agent reads them with the schema. This is how a database explains itself without any change to the agent.

- [ ] **Step 4: Write `db/library_seed.sql`** with explicit ids, exactly this data:

Books: 1 Dune (Frank Herbert), 2 Emma (Jane Austen), 3 Ulysses (James Joyce), 4 Beloved (Toni Morrison), 5 Dracula (Bram Stoker), 6 Walden (Henry David Thoreau).

Members: 1 Agon, 2 Besa, 3 Drita, 4 Luan.

| Loan | Book | Member | Loaned on | Returned on |
|---|---|---|---|---|
| 1 | Dune | Agon | 2026-01-05 | 2026-01-20 |
| 2 | Emma | Besa | 2026-02-01 | NULL |
| 3 | Ulysses | Agon | 2026-02-10 | 2026-03-01 |
| 4 | Dune | Drita | 2026-03-02 | NULL |
| 5 | Beloved | Luan | 2026-03-05 | 2026-03-19 |
| 6 | Dracula | Besa | 2026-03-10 | NULL |
| 7 | Emma | Agon | 2026-01-10 | 2026-01-25 |
| 8 | Walden | Drita | 2026-01-15 | 2026-02-01 |

This exact data was run through the real agent before this plan was written: "Which books are currently on loan?" returned Emma, Dune and Dracula with only `--db` changed. Do not alter it.

- [ ] **Step 5: Add the `library_path` fixture** to `tests/conftest.py`.

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, 7 skipped (the live tests), no warnings.

Run: `git status --short agent/`
Expected: no output. Nothing under `agent/` changed.

- [ ] **Step 7: Commit**

```bash
git add db/library_schema.sql db/library_seed.sql tests/
git commit -m "feat: second database (library) answered by the unchanged agent"
```

---

### Task 2: Examples and saved traces

**Files:**
- Create: `examples/make_examples.py`, `tests/test_examples.py`
- Modify: `.gitignore`
- Generated and committed by this task: the three scripted examples under `examples/traces/` and `examples/README.md`. (The live examples are added by the orchestrator at the milestone close, by running the same script with `--live`.)

**Interfaces:**
- Produces: `python examples/make_examples.py` writes the scripted examples; `python examples/make_examples.py --live` also runs the live ones with the model from the environment. Both replace the traces of the examples they ran, keep the others, and rewrite `examples/README.md` from the index.
- Produces: `examples.make_examples.make(live: bool, model=None, out_dir="examples") -> list[dict]`, so tests can run it into a temp directory with a fake model.

Each example is one entry in a list in the script: an id, a title, the database (`university.db` or `library.db`), the question, and for scripted ones the fake model's replies. The script runs each through `build_graph` and `run_traced` with `traces_dir` set to `<out_dir>/traces`, so every trace is saved under its own run id, exactly as any other run. Decided by the owner: example traces are NOT renamed, so looking one up by its run id works (`python -m agent.cli --trace <run_id> --traces examples/traces`).

The script keeps `<out_dir>/examples.json`, a small index mapping each example id to its entry (`run_id`, `title`, `database`, `command`, `question`, `status`, `answer`, `sql`, `scripted`). When an example is run again, the trace file of its previous run id is deleted and the index entry replaced, so re-running never piles up files. Databases are referred to by their relative names; the script builds both into the repo root first if they are missing.

Scripted examples (always produced; fake model; labelled "scripted"):

| id | Title | Database | Question | Scripted replies | Ends as |
|---|---|---|---|---|---|
| `scripted-retry` | A query that fails and is corrected | university | How many students are there? | `{"sql": "SELECT COUNT(*) AS n FROM student"}`, then `{"sql": "SELECT COUNT(*) AS student_count FROM students", "expects_single_value": True}`, then `"There are 8 students."` | answered, two attempts |
| `scripted-failed` | A query that fails twice | university | How many students are there? | `{"sql": "SELECT COUNT(*) AS n FROM student"}`, then `{"sql": "SELECT COUNT(*) AS n FROM pupils"}` | failed |
| `scripted-mismatch` | One value asked for, five returned | university | What is the average grade in Databases in Fall 2025? | one reply: the SQL selecting the five individual grades of that class, with `expects_single_value: True` | mismatch |

Live examples (produced only with `--live`; real model):

| id | Title | Database | Question | Shows |
|---|---|---|---|---|
| `aggregate` | Aggregation | university | What is the average grade in Databases in Fall 2025? | average, filter by course and semester |
| `join-above-average` | Join across four tables, with a subquery | university | Which students scored above the class average in Databases in Fall 2025? | multi-table join, multi-step |
| `filter-semester` | Filter by semester | university | How many enrolments are there in Spring 2026? | count |
| `filter-teacher` | Filter by teacher | university | What is the average grade in the classes taught by Besnik Krasniqi? | join, average |
| `filter-student` | Filter by student | university | Which courses did Arta take, and what grade did she get in each? | list |
| `clarification` | An unclear question | university | How many students passed Databases in Fall 2025? | question back, no SQL run |
| `no-value` | Nothing to average | university | What is the average grade in Databases in Fall 2030? | fixed message, no model wording |
| `library` | A different database, same agent | library | Which books are currently on loan? | only the database changed |

`examples/README.md`, written by the script from the index: a short introduction, then one section per example in the order above.

The introduction says: what the examples are; that each saved trace can be opened on GitHub by its link, with no key and nothing installed; how to print one locally by its run id (`python -m agent.cli --trace <run_id> --traces examples/traces`); how to run a question of your own and open your own trace with the run id the command prints back (`python -m agent.cli "<question>"` then `python -m agent.cli --trace <the run id it printed>`); which examples are scripted and why; and how to regenerate them.

Each section has: title, the command that reproduces it, the question, the status, the answer, the SQL that ran (from the trace's last `generate_sql` step, when there is one), the run id, and a link to the saved trace `traces/<run_id>.json`. Scripted sections carry the line "Scripted: produced with a fake model, because a real model does not fail on demand."

- [ ] **Step 1: Fix `.gitignore`**

Change the line `traces/` to `/traces/` so that only the top-level run folder is ignored and `examples/traces/` can be committed.

Run: `git check-ignore -v examples/traces/x.json; echo "exit $?"`
Expected: no match, `exit 1`.

- [ ] **Step 2: Write the failing tests** in `tests/test_examples.py`

```python
def index(tmp_path):
    return json.loads((tmp_path / "examples.json").read_text())

def test_scripted_examples_are_reproducible(tmp_path):
    first = make(live=False, out_dir=str(tmp_path))
    second = make(live=False, out_dir=str(tmp_path))
    assert [e["id"] for e in first] == ["scripted-retry", "scripted-failed", "scripted-mismatch"]
    assert [e["status"] for e in second] == ["answered", "failed", "mismatch"]
    files = sorted(p.stem for p in (tmp_path / "traces").glob("*.json"))
    assert files == sorted(e["run_id"] for e in second)          # the first run's three traces were replaced
    assert {e["run_id"] for e in first}.isdisjoint(files)

def test_example_traces_open_by_run_id(tmp_path, capsys):
    entries = make(live=False, out_dir=str(tmp_path))
    code = main(["--trace", entries[0]["run_id"], "--traces", str(tmp_path / "traces")])
    assert code == 0 and "generate_sql" in capsys.readouterr().out

def test_scripted_traces_show_what_they_claim(tmp_path):
    make(live=False, out_dir=str(tmp_path))
    load = lambda name: json.loads((tmp_path / "traces" / f"{index(tmp_path)[name]['run_id']}.json").read_text())
    retry = load("scripted-retry")
    assert [s["node"] for s in retry["steps"]].count("generate_sql") == 2 and retry["status"] == "answered"
    assert load("scripted-failed")["answer"].startswith("The question could not be answered.")
    assert load("scripted-mismatch")["answer"] == "The question asks for a single value, but the query returned 5 rows."

def test_readme_lists_every_example_and_labels_scripted_ones(tmp_path):
    make(live=False, out_dir=str(tmp_path))
    text = (tmp_path / "README.md").read_text()
    for entry in index(tmp_path).values():
        assert f"traces/{entry['run_id']}.json" in text and (tmp_path / "traces" / f"{entry['run_id']}.json").exists()
    assert text.count("Scripted: produced with a fake model") == 3
    assert "--trace" in text and "--traces examples/traces" in text      # how to open one by its run id

def test_live_examples_run_with_an_injected_model(tmp_path):
    replies = [{"sql": "SELECT 1 AS one"}, "one"] * 8
    entries = make(live=True, model=FakeModel(replies), out_dir=str(tmp_path))
    assert len(entries) == 11 and len(list((tmp_path / "traces").glob("*.json"))) == 11
    library = index(tmp_path)["library"]
    assert json.loads((tmp_path / "traces" / f"{library['run_id']}.json").read_text())["database"] == "library.db"

def test_examples_have_no_absolute_paths(tmp_path):
    make(live=True, model=FakeModel([{"sql": "SELECT 1 AS one"}, "one"] * 8), out_dir=str(tmp_path))
    for path in list((tmp_path / "traces").glob("*.json")) + [tmp_path / "README.md", tmp_path / "examples.json"]:
        assert "/Users/" not in path.read_text() and str(tmp_path) not in path.read_text()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_examples.py -q`
Expected: FAIL, `examples.make_examples` cannot be imported.

- [ ] **Step 4: Implement `examples/make_examples.py`**

- `make(live, model=None, out_dir="examples")`: for each example to run, pick the model (the scripted replies' `FakeModel`, or for live ones the injected `model`, or when that is `None` the model built from the environment exactly as `agent/cli.py` does), delete the trace of that example's previous run id if the index has one, run it with `traces_dir=<out_dir>/traces`, and store `{id, run_id, title, database, command, question, status, answer, sql, scripted}` in the index. Then save `<out_dir>/examples.json` and write `<out_dir>/README.md` for every example in the index, in the table order above. Return the entries it ran.
- The `FakeModel` comes from `tests/fakes.py`; importing it from there is fine (`# ponytail:` note: the examples script depends on a test helper; move it if the tests ever ship separately).
- `provider` and `model` recorded in scripted traces are `"scripted"` and `"fake"`.
- `if __name__ == "__main__":` parses `--live` and calls `make`.
- Add an empty `examples/__init__.py` only if the import in the tests needs it.

- [ ] **Step 5: Produce the scripted examples**

Run: `.venv/bin/python db/build.py && .venv/bin/python db/build.py db/library_schema.sql db/library_seed.sql library.db && .venv/bin/python examples/make_examples.py`
Expected: `examples/README.md`, `examples/examples.json` and three files under `examples/traces/`, each named by its run id.

Do not run it with `--live`.

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, 7 skipped, no warnings. `git status --short agent/` prints nothing.

- [ ] **Step 7: Commit**

```bash
git add .gitignore examples tests/test_examples.py
git commit -m "feat: example runs with saved traces; scripted retry, failure and mismatch"
```

---

### Task 3: README and production notes

**Files:**
- Modify: `README.md` (currently one line)
- Create: `docs/production.md`

No tests. The check is that every command in the README runs as written and every statement about behaviour is true of the code.

Sources to write from, in this order: the code itself (`agent/`, `db/`, `tests/`), then the spec at `docs/superpowers/specs/2026-11-25-university-sql-agent-design.md` (its "Approaches considered", "Known limits" and "PDF requirements and where each is met" sections carry the decisions and their reasons). Where the spec and the code differ, the code is right; say nothing the code does not do.

`README.md` sections, in this order:

1. **What it is**: three or four sentences. A question in English, one SQL query, an answer from the rows, a trace of every run. Built with LangGraph and SQLite.
2. **Quick start**: create the virtual environment, install `requirements.txt`, copy `.env.example` to `.env` and fill in provider, model and key, build the database, ask a question, show its trace. Every command copy-pasteable.
3. **How it works**: the graph as a small text diagram with the five nodes and the exits (question back, failed, nothing matched, mismatch, no value, answered), then one or two sentences per node. Name the file each lives in.
4. **Problem handling**: a table of situation → what the agent does → status. Bad SQL (one retry), unclear question (asks back, runs nothing; a question wins over SQL), nothing matched, single value expected, no value, a step that breaks (trace still saved, short safe message).
5. **Works on any database**: the agent reads table definitions and three sample rows from whatever file `--db` names; nothing in `agent/` mentions the university (a test enforces it); the library demo with its two commands; the note that comments inside `CREATE TABLE` travel with the schema.
6. **Tracing**: what a trace contains, where it is saved, and the two-step use a reader will try first: run a question, then open your own trace with the run id it printed. Then say that the saved example traces can be opened on GitHub without a key, linking to `examples/README.md`, and include one short real excerpt (print the scripted retry example with `python -m agent.cli --trace <its run id> --traces examples/traces`; its run id is in `examples/examples.json`). Say why tracing is our own rather than LangSmith.
7. **Tests**: how to run them; what the groups cover (database, graph with a scripted model, tracing, command line, key safety, known-answer checks, the second database, examples); how to run the live tests and what they need.
8. **Design decisions**: a table of decision → what was chosen → why, drawn from the spec's "Approaches considered" plus: one query with one retry; trace plus known-answer tests instead of a model checking the model; ask back instead of guessing; result checks in plain code; sample rows in the schema text; read-only at two layers plus a 5-second limit; model-agnostic through `init_chat_model`.
9. **Known limits**: the spec's list, in plain words. This section is required and must not be softened.
10. **Layout**: the directory tree with one line per file.
11. A link to `examples/README.md` and to `docs/production.md`.

`docs/production.md`: what bringing this to production would need, under the five headings the assignment names: **Reliability**, **Scalability**, **Monitoring and tracing**, **Security**, **Deployment**. For each: what the current code already does, what is missing, and what you would add first. Be concrete and tied to this code (for example: traces are local JSON files with full result rows, so production needs a store, retention and redaction; SQLite read-only file access would become a read-only database role and a statement timeout on a server database; text stored in the database reaches the model, so untrusted data is a prompt-injection path; the model call has no timeout or backoff of its own). One to two short paragraphs or a short list per heading. No vendor shopping lists.

Writing rules: plain, direct sentences; no marketing words; no claim of correctness beyond what the tests check; second person for instructions. Keep the README to what a reader needs to run it and to follow the design; it is read by an interviewer who will ask the owner to explain any line of it.

- [ ] **Step 1: Write `README.md`.**
- [ ] **Step 2: Write `docs/production.md`.**
- [ ] **Step 3: Check every command.** In a scratch copy or in place, run each README command that does not need the real model (environment setup may be described rather than re-run; `db/build.py`, the library build, `pytest`, `examples/make_examples.py`, `--trace` on a scripted example must each be run) and confirm the output matches what the README says. List what you ran in the report.
- [ ] **Step 4: Commit**

```bash
git add README.md docs/production.md
git commit -m "docs: README with design decisions and limits; production notes"
```

---

## Milestone close

Run by the orchestrator.

1. **Library demo, live:** build `library.db`, run `python -m agent.cli "Which books are currently on loan?" --db library.db`, and show the owner the answer and the trace.
2. **Live tests:** `RUN_LIVE=1 .venv/bin/pytest tests/test_live.py -q` (seven tests). If one fails, the owner sees its trace before anything is changed.
3. **Live examples:** `python examples/make_examples.py --live`, read every generated example for correctness against the seed, check no key or absolute path is in them, commit.
4. **Confirm the proof:** `git diff main --stat -- agent/` prints nothing.
5. **Spec review** against the six M4 acceptance criteria (criterion 6, publishing, is reported as pending the owner). **Code review** for bugs. **Complexity review.** Each once, on the whole milestone diff. The reviewers also read the README and `docs/production.md` for statements that are not true of the code.
6. **Publishing:** only on the owner's explicit go-ahead at that time. Before it: confirm `.env`, `*.db`, `traces/` and `.superpowers/` are not tracked, and search the whole history for the key.
