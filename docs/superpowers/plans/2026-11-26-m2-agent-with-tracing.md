# M2 Agent with Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ask a question on the command line, get an answer from a LangGraph agent that writes and runs one SQL query, and find a saved record of every step of that run.

**Architecture:** `agent/graph.py` builds a four-node LangGraph graph from a model object and a database path; it knows nothing about providers, keys or the university. `agent/trace.py` runs the graph through its update stream and saves one JSON record per run. `agent/cli.py` is the only place that reads the environment and creates the model.

**Tech Stack:** Python 3.11+, `langgraph` 1.x, `langchain` 1.x (`init_chat_model`), `langchain-openai`, `langchain-anthropic`, `langchain-ollama`, `python-dotenv`, `pytest`. Stdlib `sqlite3` through the existing `agent/db.py`.

**Spec:** `docs/superpowers/specs/2026-11-25-university-sql-agent-design.md` (sections Graph, Model selection, Tracing, Tests, M2)

**Scope note:** this milestone is the happy path plus one honest failure exit. Retry, clarification, `expects_single_value`, and the `empty` / `mismatch` / `no_value` result checks are M3 and must not be built here.

## Global Constraints

- `agent/` must not contain the words student, teacher, course, enrollment, grade, semester (any case), in code, comments, prompts or strings. Task 1 adds the test that enforces it.
- `build_graph(model, db_path)` takes the model as an argument. Nothing in `agent/graph.py`, `agent/prompts.py` or `agent/trace.py` reads the environment, names a provider, or handles a key.
- The API key is never placed in graph state, a trace, a log line or printed output. No code reads or prints `.env`; only `load_dotenv()` in `agent/cli.py` touches it.
- No model name is hard-coded in code. `LLM_PROVIDER` and `LLM_MODEL` are required environment variables.
- Graph node names are exactly `load_schema`, `generate_sql`, `execute_sql`, `answer`.
- Trace record fields are exactly: `run_id, started_at, question, database, provider, model, status, answer, steps`; each step is `{node, at, duration_ms, output}`.
- The default test run uses no network and no real model.
- `pyproject.toml` already sets pytest `filterwarnings = ["error"]`: close every sqlite connection; test output must be clean.
- Existing `agent/db.py` API, do not change it: `connect(db_path) -> Connection`, `get_schema(conn) -> str`, `run_query(conn, sql) -> (columns, rows, truncated)`; `run_query` raises `ValueError` (guard) or `sqlite3.Error`.
- Mark any deliberate shortcut with a `# ponytail:` comment naming its ceiling.
- No file under `docs/` or `db/` is changed by this plan.

## Review Focus

1. The model call itself fails (network, bad key, model cannot produce structured output): the run must still leave a trace with status `error` that names the step that failed and carries a short safe message. The provider's own error text is never stored or printed, because it can contain request details. (Task 2, `test_trace_saved_when_model_raises`)
2. A result value that JSON cannot encode (bytes from a BLOB column): saving the trace must not crash. (Task 2, `test_trace_handles_bytes`)
3. Two runs started in the same second must not overwrite each other's trace. (Task 2, `test_run_ids_unique`)
4. `LLM_PROVIDER` or `LLM_MODEL` missing: a one-line message naming the missing variable, exit code 2, no traceback. (Task 3, `test_missing_env`)
5. `--trace` with a run id that does not exist: a one-line message, exit code 2. (Task 3, `test_trace_unknown_run`)
6. A schema or question containing `{` and `}`: prompts are built by concatenation, never `str.format` on text that contains user or database content. (Task 1, `test_braces_in_question`)

---

### Task 1: Prompts and graph

**Files:**
- Modify: `requirements.txt` (add `langgraph`, `langchain`, `langchain-openai`, `langchain-anthropic`, `langchain-ollama`, `python-dotenv`, one per line, no version pins)
- Create: `agent/prompts.py`, `agent/graph.py`
- Test: `tests/fakes.py`, `tests/test_graph.py`, `tests/test_vocabulary.py`

**Interfaces:**
- Consumes: `agent.db.connect`, `get_schema`, `run_query`; fixture `db_path`.
- Produces in `agent/prompts.py`: two string constants, `GENERATE_SQL_SYSTEM` and `ANSWER_SYSTEM`.
- Produces in `agent/graph.py`:
  - `class State(TypedDict, total=False)` with keys `question: str, schema: str, sql: str, columns: list[str], rows: list[tuple], truncated: bool, error: str, answer: str, status: str`
  - `class SqlReply(pydantic.BaseModel)` with one field `sql: str`
  - `build_graph(model, db_path: str)` returning the compiled graph. Invoked with `{"question": "..."}`.
- Produces in `tests/fakes.py`: `FakeModel(replies)`, used by every later test file.

- [ ] **Step 1: Install dependencies**

Edit `requirements.txt`, then run: `.venv/bin/pip install -q -r requirements.txt`
Expected: installs without error. `.venv/bin/python -c "import langgraph, langchain"` prints nothing.

- [ ] **Step 2: Write the fake model** in `tests/fakes.py`

```python
from types import SimpleNamespace


class FakeModel:
    """Scripted stand-in for a chat model. `replies` are consumed in call order:
    a dict for a structured call, a str for a plain call, an Exception to raise."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []          # the message list of every call, in order

    def _next(self, messages):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def with_structured_output(self, schema):
        return SimpleNamespace(invoke=lambda messages: schema(**self._next(messages)))

    def invoke(self, messages):
        return SimpleNamespace(text=self._next(messages))
```

- [ ] **Step 3: Write the failing tests**

`tests/test_graph.py`:

```python
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
```

`tests/test_vocabulary.py`:

```python
BANNED = ("student", "teacher", "course", "enrollment", "grade", "semester")

def test_agent_code_has_no_schema_words():
    for path in (ROOT / "agent").glob("*.py"):
        text = path.read_text().lower()
        for word in BANNED:
            assert word not in text, f"{path.name} contains {word!r}"
```

(`ROOT` from `db.build`.)

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_graph.py tests/test_vocabulary.py -q`
Expected: `test_graph.py` fails on importing `agent.graph`; `test_vocabulary.py` passes.

- [ ] **Step 5: Write `agent/prompts.py`**

Two constants. Each must state, in plain sentences:

`GENERATE_SQL_SYSTEM`:
- You write one SQLite query that answers the user's question.
- You are given the database's table definitions, each followed by a few sample rows. Use only tables and columns that appear there. The sample rows show how values are written; match that format in filters.
- Return exactly one `SELECT` statement (it may start with `WITH`). No other statement type, no comments, no markdown.
- Do counting, averaging, minimum and maximum inside the SQL, and add no `LIMIT` the question did not ask for.
- Give every output column a plain descriptive name.

`ANSWER_SYSTEM`:
- You answer the user's question in one or two plain sentences.
- Use only the rows provided. Do not add facts, names or numbers that are not in them.
- Mention every row that answers the question; do not drop any.
- If told the result shows only the first 100 rows, say that the list is incomplete.

Wording is the builder's; the content above is required. No example may use the banned words.

- [ ] **Step 6: Implement `agent/graph.py`**

Four node functions defined inside `build_graph` so they close over `model` and `db_path`:

- `load_schema`: opens a connection (`with closing(connect(db_path))`), returns `{"schema": get_schema(conn)}`.
- `generate_sql`: `model.with_structured_output(SqlReply).invoke([("system", GENERATE_SQL_SYSTEM), ("user", "Schema:\n" + schema + "\n\nQuestion: " + question)])`; returns `{"sql": reply.sql}`.
- `execute_sql`: opens a connection, calls `run_query`. On success returns `{"columns", "rows", "truncated"}`. On `ValueError` or `sqlite3.Error` returns `{"error": str(e), "status": "failed", "answer": "The question could not be answered. Last error: " + str(e)}`.
- `answer`: `model.invoke([("system", ANSWER_SYSTEM), ("user", ...)])` where the user text is built by concatenation from the question, the SQL, the column names and the rows, plus the line `The result shows only the first 100 rows.` when `truncated`. Returns `{"answer": reply.text, "status": "answered"}`.

Edges: `START → load_schema → generate_sql → execute_sql`; conditional from `execute_sql`: to `END` when `"error"` is in the state, otherwise to `answer`; `answer → END`.

Each node opens and closes its own connection so the state stays plain data that can be saved as JSON. `# ponytail:` note that: one connection per node; share one if run time ever matters.

- [ ] **Step 7: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass (35 existing + 9 new), no warnings.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt agent/prompts.py agent/graph.py tests/fakes.py tests/test_graph.py tests/test_vocabulary.py
git commit -m "feat: LangGraph agent: schema, SQL, execute, answer"
```

---

### Task 2: Tracing

**Files:**
- Create: `agent/trace.py`
- Test: `tests/test_trace.py`

**Interfaces:**
- Consumes: `build_graph` and `FakeModel` from Task 1.
- Produces in `agent/trace.py`:
  - `run_traced(graph, question: str, *, database: str, provider: str, model: str, traces_dir: str = "traces") -> dict`: runs the graph, saves the record to `<traces_dir>/<run_id>.json`, returns the record.
  - `load_trace(run_id: str, traces_dir: str = "traces") -> dict`: raises `FileNotFoundError` when there is no such run.
  - `format_trace(record: dict) -> str`: the record as readable text, one block per step.

- [ ] **Step 1: Write the failing tests** in `tests/test_trace.py`

Reuse `ABOVE_AVG` and `QUESTION` by importing them from `tests.test_graph` (add an empty `tests/__init__.py` if the import needs it), or move both into `tests/fakes.py` and import from there in both files. A helper `traced(model, db_path, tmp_path, question=QUESTION)` calls `run_traced(build_graph(model, db_path), question, database=db_path, provider="fake", model="fake-1", traces_dir=str(tmp_path / "traces"))`.

```python
def test_trace_records_the_full_path(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": ABOVE_AVG}, "Arta, Blerim and Dren."]), db_path, tmp_path)
    assert set(rec) == {"run_id", "started_at", "question", "database", "provider", "model", "status", "answer", "steps"}
    assert (rec["question"], rec["database"], rec["provider"], rec["model"]) == (QUESTION, db_path, "fake", "fake-1")
    assert (rec["status"], rec["answer"]) == ("answered", "Arta, Blerim and Dren.")
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql", "execute_sql", "answer"]
    for step in rec["steps"]:
        assert set(step) == {"node", "at", "duration_ms", "output"} and step["duration_ms"] >= 0
    assert "CREATE TABLE" in rec["steps"][0]["output"]["schema"]
    assert rec["steps"][1]["output"] == {"sql": ABOVE_AVG}
    assert rec["steps"][2]["output"]["columns"] == ["name"]
    assert rec["steps"][3]["output"]["answer"] == "Arta, Blerim and Dren."

def test_trace_is_saved_and_loads_back(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": ABOVE_AVG}, "ok"]), db_path, tmp_path)
    saved = load_trace(rec["run_id"], str(tmp_path / "traces"))
    assert (tmp_path / "traces" / f"{rec['run_id']}.json").exists()
    assert saved["steps"][2]["output"]["rows"] == [["Arta"], ["Blerim"], ["Dren"]]
    assert saved == json.loads(json.dumps(rec, default=repr))

def test_failed_run_trace(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": "SELECT nope FROM students"}]), db_path, tmp_path, "q")
    assert rec["status"] == "failed"
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql", "execute_sql"]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_trace.py -q`
Expected: FAIL, `agent.trace` cannot be imported.

- [ ] **Step 3: Implement `agent/trace.py`**

- `run_traced`: `run_id` is a UTC timestamp plus 6 hex characters from `uuid.uuid4()`, e.g. `20261126-073201-a1b2c3`. Iterate `for mode, chunk in graph.stream({"question": question}, stream_mode=["updates", "tasks"])`. A `"tasks"` chunk without a `"result"` key means a node is starting: remember `chunk["name"]` as the current node. Ignore `"tasks"` chunks that have a `"result"` key. An `"updates"` chunk is `{node_name: update_dict}`. For each update, append a step with `at` (ISO UTC time the update arrived), `duration_ms` (time since the previous update, or since the start for the first), and `output` (the update). `status` and `answer` come from the last update that set them.
- If the stream raises any `Exception`, keep the steps collected so far, append one more step for the current node with `output = {"error": type(e).__name__}`, and set `status = "error"` and `answer = "Run stopped at <node>: <ExceptionClassName>."`. Decided by the owner: show which step failed, with a short safe message. Never store or print `str(e)`: a provider's error text can carry request details. Do not re-raise: the record is the result.
- Save with `json.dump(record, f, indent=2, default=repr)` after creating `traces_dir`. Return the record as built in memory.
- `format_trace`: a header (run id, started, database, provider and model, status, question), then one block per step: node name, duration, and each key of its output. Print the SQL on its own lines, and rows one per line. Exact layout is the builder's.
- Nothing here reads the environment.

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, no warnings.

- [ ] **Step 5: Commit**

```bash
git add agent/trace.py tests/
git commit -m "feat: per-run trace records from the graph's update stream"
```

---

### Task 3: Command line

**Files:**
- Create: `agent/cli.py`, `.env.example`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_graph`, `run_traced`, `load_trace`, `format_trace`, `FakeModel`.
- Produces: `agent.cli.main(argv: list[str] | None = None, model=None) -> int`. `model` exists so tests can pass a fake; when it is `None` the model is built from the environment. Returns the exit code; `if __name__ == "__main__": sys.exit(main())`.
- Command line: `python -m agent.cli "<question>" [--db PATH] [--traces DIR]` and `python -m agent.cli --trace RUN_ID [--traces DIR]`. Defaults: `--db university.db`, `--traces traces`.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli.py`

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: FAIL, `agent.cli` cannot be imported.

- [ ] **Step 3: Implement `agent/cli.py`**

- `argparse` with an optional positional `question`, `--db`, `--traces`, `--trace`. Use `parser.parse_args(argv)`; catch `SystemExit` from argparse is not needed, but `main` must return 2 (with a message on stderr containing the word "question") when neither a question nor `--trace` is given.
- `--trace`: `print(format_trace(load_trace(run_id, traces)))`; on `FileNotFoundError` print `No trace found for run <id>` to stderr, return 2.
- Asking: check the database file exists first (message naming the path, return 2). If `model is None`: call `load_dotenv()`, read `LLM_PROVIDER` and `LLM_MODEL` from `os.environ`; if either is missing print `Set <NAME> in the environment or in .env (see .env.example)` to stderr and return 2; otherwise `model = init_chat_model(os.environ["LLM_MODEL"], model_provider=os.environ["LLM_PROVIDER"])` (`from langchain.chat_models import init_chat_model`). With an injected model use `provider = model_name = "injected"`.
- `record = run_traced(build_graph(model, db), question, database=db, provider=..., model=..., traces_dir=traces)`; print `record["answer"]`, a blank line, then `run: <run_id>`. Return 0 when status is `answered`, 1 otherwise. For status `error` the printed answer is the record's short message, which already names the failed step.
- Import `load_dotenv` at module top as `from dotenv import load_dotenv` (the test replaces `agent.cli.load_dotenv`).

- [ ] **Step 4: Write `.env.example`**

```
# Which model writes the SQL and words the answer.
# LLM_PROVIDER: openai | anthropic | ollama
LLM_PROVIDER=openai
LLM_MODEL=
OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
```

Do not read, print, copy or modify `.env`.

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, no warnings.

Run: `grep -riE "student|teacher|course|enrollment|grade|semester" agent/`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add agent/cli.py .env.example tests/test_cli.py
git commit -m "feat: command line to ask a question and show a run's trace"
```

---

## Milestone close

Run by the orchestrator, not by a task implementer.

1. **Live check (acceptance criterion 1).** Build `university.db`, then with the real model from `.env` ask:
   - join: "Who teaches Databases in Fall 2025?" (expected: Lira Hoxha)
   - aggregate: "What is the average grade in Databases in Fall 2025?" (expected: 78)
   - filter: "How many enrolments are there in Spring 2026?" (expected: 7)

   For each, record the answer and print its trace with `--trace`. Decided by the owner: if one comes back wrong, show the owner the trace first, so they can see where it went wrong. Do not switch models, change prompts or retry before that.
2. **Spec review:** the six M2 acceptance criteria, each met or not met with evidence.
3. **Code review:** bugs.
4. **Complexity review:** `ponytail:ponytail-review` on the milestone diff.

Reviews run once, on the whole milestone diff. Findings and decisions are reported to the owner before M3 is planned.
