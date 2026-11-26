# M3 Problem Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The agent retries failing SQL once, asks back when a question is unclear, stops with an accurate message when a result is empty, the wrong shape or has no value, and the tests check the answer people actually see.

**Architecture:** The graph gains one loop (failed SQL back to `generate_sql`, once), one early exit (clarification) and one new node, `check_result`, that inspects a successful result with plain code before the answer step. The model's structured reply grows from one field to four. The command line reads one line to follow up a clarification. Test-only helpers compare rows by field and check the answer text.

**Tech Stack:** unchanged from M2. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-25-university-sql-agent-design.md` (sections Graph, Following up on a clarification, Tests, M3)

## Global Constraints

- `agent/` must not contain the words student, teacher, course, enrollment, grade, semester (any case). `tests/test_vocabulary.py` enforces it.
- Exactly one retry: at most two SQL attempts per run, never a third.
- A result check ends the run with plain code and a fixed message. The model is never asked to pick, summarise or explain a result that failed a check.
- Fixed messages, verbatim:
  - empty: `Nothing matched.`
  - mismatch, row count is not 1: `The question asks for a single value, but the query returned <N> rows.`
  - mismatch, one row but several columns: `The question asks for a single value, but the query returned <M> columns.`
  - no value: `No value is available for <column names joined by ", ">.`
  - failed: `The question could not be answered. Last error: <error>`
- Statuses: `answered`, `clarification`, `failed`, `empty`, `mismatch`, `no_value`, and `error` (set by the trace when a step breaks).
- A zero is a value: it goes to the answer step.
- Each question is its own run with no memory. A follow-up is a new run with its own trace.
- The API key never reaches graph state, a trace, a log line or printed output. Tests and implementers never read `.env`.
- The default test run uses no network and no real model. Live tests run only when `RUN_LIVE=1`.
- pytest treats warnings as errors; test output must be clean.
- `agent/db.py` and `agent/trace.py` are not changed by this plan.
- Mark any deliberate shortcut with a `# ponytail:` comment naming its ceiling.
- No file under `docs/` or `db/` is changed by this plan.

## Review Focus

1. The model returns neither SQL nor a clarification (both empty): the run ends `failed` with a clear message, not a crash or an empty answer. (Task 1, `test_empty_reply_is_failed`)
2. The model returns a clarification with no choices, or with SQL as well: the question wins and the SQL is discarded without running; a clarification without choices is still shown. (Task 1, `test_reply_edge_cases`)
3. The retry's second query is rejected by the guard rather than the database: it still counts as the second failure and ends `failed`. (Task 1, `test_guard_rejection_counts_as_attempt`)
4. A single value expected and the result is one row with one NULL: this is `no_value`, not `mismatch` and not an answer of "None". (Task 2, `test_single_null_is_no_value`)
5. At a clarification the person presses Enter, types a number outside the list, or input is closed (piped): no crash; empty or closed input ends the session, an out-of-range number is treated as typed text. (Task 3, `test_follow_up_edge_inputs`)
6. A numeric answer written as a word ("five") would slip past a digit check: the answer prompt requires digits. (Task 1 prompt rule; Task 4 `test_check_answer_numbers`)

---

### Task 1: Retry and clarification

**Files:**
- Modify: `agent/graph.py`, `agent/prompts.py`, `tests/fakes.py`, `tests/test_graph.py`, `tests/test_trace.py`

**Interfaces:**
- Produces in `agent/graph.py`:
  - `SqlReply` with four required fields, no defaults: `sql: str`, `expects_single_value: bool`, `clarification: str`, `choices: list[str]`. Each has a pydantic `Field(description=...)`. Empty string / empty list mean "not used". (All-required with no nullable fields was checked against the real model.)
  - `State` gains `expects_single_value: bool`, `attempts: int`, `clarification: str`, `choices: list[str]`.
  - `combine(original: str, reply: str) -> str` returning `original + " (" + reply + ")"`. Module-level function; Task 3 uses it.
  - `build_graph(model, db_path)` signature unchanged.
- Produces in `tests/fakes.py`: `FakeModel` fills missing structured fields with `{"sql": "", "expects_single_value": False, "clarification": "", "choices": []}`, so existing replies such as `{"sql": "..."}` keep working.

Behaviour:

- **generate_sql**: decided by the owner, a question back always wins. When `reply.clarification.strip()` is non-empty it returns `{"clarification", "choices", "status": "clarification", "answer": reply.clarification}` and nothing else, even if the reply also contains SQL: that SQL is not put in the state and never runs. The run waits for the person's answer before anything is executed. Otherwise, when `reply.sql.strip()` is non-empty: `{"sql", "expects_single_value", "attempts": previous + 1}`. Otherwise: `{"error": "the model returned neither SQL nor a question", "status": "failed", "answer": "The question could not be answered. Last error: the model returned neither SQL nor a question"}`.
- On a retry the user message also carries, after the question: the previous SQL and its error, introduced by a sentence saying the previous query failed and must be corrected.
- **execute_sql** on failure returns `{"error": <message>}`; when `attempts` is 2 it also sets `status: "failed"` and the failed answer. On success it must leave no truthy `error` in the state.
- Edges: after `generate_sql`, go to `END` when `status` is set, otherwise `execute_sql`. After `execute_sql`: truthy `error` and `attempts < 2` → `generate_sql`; truthy `error` otherwise → `END`; no error → `answer` (Task 2 inserts `check_result` here).

Prompt additions (required content; wording is the builder's; no banned words):

`GENERATE_SQL_SYSTEM` also says:
- Set `expects_single_value` to true when the question asks for exactly one value, such as one overall average or one count; false for lists, tables or several values.
- If the answer depends on something neither the question nor the database states (for example a threshold that defines success, what "current" or "latest" refers to, or which of two people with the same name), do not guess: leave `sql` empty, put one short question in `clarification`, and give 2 to 3 `choices`. Never return both a query and a question.
- Each choice is a complete question that could be asked as-is. It keeps every condition of the original question and fills in only the missing detail.
- If a previous query and its error are given, write a corrected query.

`ANSWER_SYSTEM` also says: write numbers as digits, not words.

- [ ] **Step 1: Update `FakeModel`** as described under Interfaces. Run `.venv/bin/pytest -q`; expected: 66 passed (nothing else changed yet).

- [ ] **Step 2: Write the failing tests** in `tests/test_graph.py`

Two existing tests describe M2 behaviour that changes; replace them:
- `test_failing_sql_ends_failed_without_answer_call` becomes `test_two_failures_end_failed` below.
- `test_happy_path` asserts `"error" not in final`; that may become `not final.get("error")` if a successful run now carries an empty `error`. No other existing assertion is loosened.
- In `tests/test_trace.py`, `test_failed_run_trace` must script two failing replies and expect nodes `["load_schema", "generate_sql", "execute_sql", "generate_sql", "execute_sql"]`.

```python
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
```

Add to `tests/test_trace.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_graph.py tests/test_trace.py -q`
Expected: the new tests fail (no retry, no clarification, `combine` missing).

- [ ] **Step 4: Implement** the reply shape, `combine`, the two nodes' new behaviour, the edges and the prompt additions.

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, no warnings.

- [ ] **Step 6: Commit**

```bash
git add agent/graph.py agent/prompts.py tests/
git commit -m "feat: one retry on failing SQL, clarification exit for unclear questions"
```

---

### Task 2: Result checks

**Files:**
- Modify: `agent/graph.py`, `tests/test_graph.py`, `tests/test_trace.py`

**Interfaces:**
- Consumes: Task 1's graph and `State`.
- Produces: a fifth node named exactly `check_result`, between `execute_sql` and `answer`. Edges: `execute_sql` with no error → `check_result`; `check_result` → `END` when it set a `status`, otherwise → `answer`.

`check_result(state)` is plain code with no model call. It applies the first rule that matches and otherwise returns `{}`:

1. `rows` is empty → `{"status": "empty", "answer": "Nothing matched."}`
2. `expects_single_value` is true and the result is not exactly one row with one column → `{"status": "mismatch", "answer": ...}` using the row-count message when the row count is not 1, otherwise the column-count message.
3. Exactly one row and every value in it is `None` → `{"status": "no_value", "answer": "No value is available for " + ", ".join(columns) + "."}`

- [ ] **Step 1: Write the failing tests** in `tests/test_graph.py`

```python
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
```

Add to `tests/test_trace.py`:

```python
def test_mismatch_trace_shows_why(db_path, tmp_path):
    sql = "SELECT name FROM students"
    rec = traced(FakeModel([{"sql": sql, "expects_single_value": True}]), db_path, tmp_path, "q")
    assert rec["status"] == "mismatch" and "returned 8 rows" in rec["answer"]
    assert [s["node"] for s in rec["steps"]] == ["load_schema", "generate_sql", "execute_sql", "check_result"]
    assert rec["steps"][1]["output"]["expects_single_value"] is True       # what was expected
    assert len(rec["steps"][2]["output"]["rows"]) == 8                      # what came back
    assert rec["steps"][3]["output"]["status"] == "mismatch"
    text = format_trace(rec)
    assert "expects_single_value: True" in text and "returned 8 rows" in text
```

The existing happy-path trace tests list four nodes; they must now expect `check_result` between `execute_sql` and `answer`. Update those lists; when `check_result` returns `{}` the graph may emit an update whose value is `None` or `{}` for it. If the stream yields `None` for an empty update, `agent/trace.py` would fail on `.get`; in that case return `{"checked": True}` from `check_result` instead of `{}` rather than changing `agent/trace.py`, and say so in the report.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_graph.py tests/test_trace.py -q`
Expected: the new tests fail.

- [ ] **Step 3: Implement `check_result` and its edges.**

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, no warnings.

- [ ] **Step 5: Commit**

```bash
git add agent/graph.py tests/
git commit -m "feat: result checks: nothing matched, single value expected, no value"
```

---

### Task 3: Follow-up at the command line, and key safety

**Files:**
- Modify: `agent/cli.py`, `tests/test_cli.py`
- Test: `tests/test_key_safety.py`

**Interfaces:**
- Consumes: `combine` from Task 1; record fields `status`, `answer`, `steps` from the trace. The choices of a clarification are in the record's last step: `record["steps"][-1]["output"]["choices"]`.
- Produces: `main(argv=None, model=None, ask=input) -> int`. `ask` is called with a prompt string and returns one line; tests pass a fake.

Behaviour when a run ends with status `clarification`:
- Print the clarification, then the choices numbered from 1, then `run: <id>`.
- Call `ask("Pick a number or type your answer (Enter to stop): ")`.
- `EOFError` or an empty line: stop.
- A number from 1 to the number of choices: the next question is that choice, as-is.
- Anything else (including an out-of-range number): the next question is `combine(original question, reply)`, where the original question is the one typed on the command line.
- Run the next question as a new traced run and print its answer and run id. If that run is again a clarification, repeat.

Exit code: 1 when the last run's status is `failed`, `mismatch` or `error`; otherwise 0.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` (reuse its `run` helper, extended to pass `ask`):

```python
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
])
def test_exit_codes(db_path, tmp_path, capsys, reply, code_expected):
    code, _, _ = run(capsys, ["q", "--db", db_path, "--traces", str(tmp_path)], FakeModel([reply]))
    assert code == code_expected
```

(`model.calls[1][1][1]` is the user message text of the second model call: call index 1, message index 1, the text of the `(role, text)` pair.)

`tests/test_key_safety.py`:

```python
KEY = "sk-test-KEYSAFETY-0123456789"

def test_key_never_reaches_traces_or_output(db_path, tmp_path, capsys, caplog, monkeypatch):
    monkeypatch.setattr("agent.cli.load_dotenv", lambda *a, **k: None)
    for name, value in (("LLM_PROVIDER", "openai"), ("LLM_MODEL", "some-model"), ("OPENAI_API_KEY", KEY)):
        monkeypatch.setenv(name, value)
    scripts = [
        [{"sql": "SELECT COUNT(*) AS n FROM students"}, "There are 8."],                 # answered
        [{"sql": "SELECT nope FROM students"}, {"sql": "SELECT nada FROM students"}],    # failed
        [RuntimeError(f"401 invalid key {KEY}")],                                        # provider error carrying the key
    ]
    traces = tmp_path / "traces"
    with caplog.at_level(logging.DEBUG):
        for replies in scripts:
            monkeypatch.setattr("agent.cli.init_chat_model", lambda *a, **k: FakeModel(replies))
            main(["q", "--db", db_path, "--traces", str(traces)])
    out = capsys.readouterr()
    files = list(traces.glob("*.json"))
    assert len(files) == 3
    for text in [out.out, out.err, caplog.text, *(f.read_text() for f in files)]:
        assert KEY not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_key_safety.py -q`
Expected: the follow-up tests fail (`ask` is not a parameter). `test_key_safety.py` may already pass; if it does, confirm it can fail by temporarily printing `os.environ["OPENAI_API_KEY"]` in `main`, then remove that line. Record this in the report.

- [ ] **Step 3: Implement** the follow-up loop and the exit-code rule in `agent/cli.py`.

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, no warnings.

Run: `grep -riE "student|teacher|course|enrollment|grade|semester" agent/`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add agent/cli.py tests/test_cli.py tests/test_key_safety.py
git commit -m "feat: follow up a clarification at the command line; key safety test"
```

---

### Task 4: Known-answer checks and live tests

**Files:**
- Create: `tests/known_answer.py`, `tests/test_known_answer.py`, `tests/test_live.py`

**Interfaces:**
- Consumes: `build_graph`, `run_traced`, `FakeModel`, fixture `db_path`, `ABOVE_AVG` and `QUESTION` (wherever Task-M2 put them).
- Produces in `tests/known_answer.py`, test-only helpers. Each returns `None` when the check passes, otherwise a one-line message:
  - `check_rows(columns: list[str], rows: list, expected: list[dict], names: dict[str, tuple[str, ...]]) -> str | None`
  - `check_answer(answer: str, must: list, must_not: list = ()) -> str | None`
  - `check_labelled(answer: str, labels: dict[str, float]) -> str | None`

Rules:

`check_rows`: if `len(rows) != len(expected)` → `"expected <E> rows, got <G>"`. Otherwise look for one assignment of each expected field to a distinct result column, the same for every row, such that (a) the column's name, lower-cased, contains at least one of that field's words in `names` (the empty string matches any column), and (b) the result rows, projected to those columns, equal the expected rows as a multiset. Numbers are compared after `round(value, 1)`; other values with `==`. Row order and extra columns are ignored. No such assignment → `"rows do not match the expected values by field"`.

`check_answer`: every value in `must` has to appear in the answer and none in `must_not`. A string appears when it is a substring. A number appears when it stands as its own number in the text, with or without a trailing `.0` (78 matches "78" and "78.0", not "178" or "78.5"). Messages: `"answer does not mention <value>"`, `"answer mentions <value>"`.

`check_labelled`: for each label, the answer (case ignored) must contain the label followed only by characters that are not letters or digits, then the number (same number rule). Message: `"answer does not show <number> after '<label>'"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_known_answer.py`

```python
N = {"student": ("name", "student")}
GA = {"grade": ("grade",), "average": ("avg", "average", "mean")}

def test_check_rows_counts():
    assert check_rows(["name"], [("Arta",)], [{"student": "Arta"}, {"student": "Blerim"}, {"student": "Dren"}], N) == "expected 3 rows, got 1"

def test_check_rows_order_and_extra_columns_ignored():
    rows = [("Dren", 82.0, 1), ("Arta", 95.0, 1), ("Blerim", 88.0, 1)]
    assert check_rows(["student_name", "grade", "x"], rows, [{"student": "Arta"}, {"student": "Blerim"}, {"student": "Dren"}], N) is None

def test_check_rows_by_field():
    expected = [{"grade": 95, "average": 78.0}]
    assert check_rows(["grade", "class_average"], [(95.0, 78.0)], expected, GA) is None
    assert check_rows(["arta_grade", "average_grade"], [(95.0, 78.0)], expected, GA) is None
    assert check_rows(["class_average", "grade"], [(78.0, 95.0)], expected, GA) is None        # column order is free
    assert check_rows(["grade", "class_average"], [(78.0, 95.0)], expected, GA) is not None    # values swapped
    assert check_rows(["a", "b"], [(95.0, 78.0)], expected, GA) is not None                    # names do not fit

def test_check_rows_each_result_row_used_once():
    expected = [{"student": "Arta"}, {"student": "Arta"}]
    assert check_rows(["name"], [("Arta",), ("Blerim",)], expected, N) is not None

def test_check_rows_rounding_and_any_column():
    assert check_rows(["n"], [(91.3333,)], [{"value": 91.3}], {"value": ("",)}) is None

def test_check_answer_names():
    assert check_answer("Arta, Blerim and Dren scored above average.", ["Arta", "Blerim", "Dren"], ["Era", "Fatos"]) is None
    assert check_answer("Arta and Blerim scored above average.", ["Arta", "Blerim", "Dren"]) == "answer does not mention Dren"
    assert check_answer("Arta, Blerim, Dren and Era.", ["Arta"], ["Era"]) == "answer mentions Era"

def test_check_answer_numbers():
    assert check_answer("The average is 78.0.", [78]) is None
    assert check_answer("The average is 78.", [78.0]) is None
    assert check_answer("There are 178 rows.", [78]) is not None
    assert check_answer("It is 78.5.", [78]) is not None
    assert check_answer("There are five.", [5]) is not None

def test_check_labelled():
    labels = {"grade": 95, "class average": 78}
    assert check_labelled("Grade: 95. Class average: 78.0.", labels) is None
    assert check_labelled("**Grade:** 95\n**Class average:** 78", labels) is None
    assert check_labelled("Grade: 78. Class average: 95.", labels) == "answer does not show 95 after 'grade'"
    assert check_labelled("Arta got 95 and the average was 78.", labels) is not None

# Demonstrations: a scripted wrong run, and the check that catches it.
EXPECTED = [{"student": "Arta"}, {"student": "Blerim"}, {"student": "Dren"}]

def test_wrong_sql_is_caught_and_traceable(db_path, tmp_path):
    wrong = ABOVE_AVG.split("AND e.grade >")[0] + "ORDER BY e.grade DESC LIMIT 1"
    rec = traced(FakeModel([{"sql": wrong}, "Arta scored above the class average."]), db_path, tmp_path)
    out = rec["steps"][2]["output"]
    assert check_rows(out["columns"], out["rows"], EXPECTED, N) == "expected 3 rows, got 1"
    assert "LIMIT 1" in rec["steps"][1]["output"]["sql"]          # the trace shows where it went wrong

def test_wrong_answer_is_caught(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": ABOVE_AVG}, "Arta and Blerim scored above the class average."]), db_path, tmp_path)
    out = rec["steps"][2]["output"]
    assert check_rows(out["columns"], out["rows"], EXPECTED, N) is None       # the rows are right
    assert check_answer(rec["answer"], ["Arta", "Blerim", "Dren"], ["Era", "Fatos"]) == "answer does not mention Dren"

def test_swapped_answer_is_caught(db_path, tmp_path):
    sql = """SELECT e.grade AS grade, (SELECT AVG(e2.grade) FROM enrollments e2 WHERE e2.offering_id = e.offering_id) AS class_average
             FROM enrollments e WHERE e.student_id = 1 AND e.offering_id = 1"""
    rec = traced(FakeModel([{"sql": sql}, "Grade: 78. Class average: 95."]), db_path, tmp_path)
    out = rec["steps"][2]["output"]
    assert check_rows(out["columns"], out["rows"], [{"grade": 95, "average": 78.0}], GA) is None
    assert check_labelled(rec["answer"], {"grade": 95, "class average": 78}) == "answer does not show 95 after 'grade'"
```

(`traced` is the helper from `tests/test_trace.py`; import it or move it to `tests/fakes.py`. Step indexes assume `load_schema, generate_sql, execute_sql, check_result, answer`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_known_answer.py -q`
Expected: FAIL, `tests.known_answer` cannot be imported.

- [ ] **Step 3: Implement the three helpers** in `tests/known_answer.py`. `itertools.permutations` over column indexes for the assignment; `collections.Counter` for the multiset comparison.

- [ ] **Step 4: Write `tests/test_live.py`**

Module-level skip unless `os.environ.get("RUN_LIVE") == "1"`. When live, call `load_dotenv()` and build the model with `init_chat_model(os.environ["LLM_MODEL"], model_provider=os.environ["LLM_PROVIDER"])`. Each test runs one question through `run_traced` (traces into `tmp_path`) and on failure includes the run's `format_trace` output in the assertion message so the failure shows where it went wrong.

| Test | Question | Expected |
|---|---|---|
| `test_above_class_average` | Which students scored above the class average in Databases in Fall 2025? | status `answered`; rows `EXPECTED` with `N`; answer has Arta, Blerim, Dren and not Era, Fatos |
| `test_grade_and_class_average` | What is Arta's grade in Databases in Fall 2025, and what is the class average? Answer with two labels: grade and class average. | `answered`; rows `[{"grade": 95, "average": 78.0}]` with `GA`; `check_labelled(answer, {"grade": 95, "class average": 78})` |
| `test_who_teaches` | Who teaches Databases in Fall 2025? | `answered`; rows `[{"teacher": "Lira Hoxha"}]` with `{"teacher": ("name", "teacher")}`; answer has "Lira Hoxha" |
| `test_distinct_people` | How many different students does Besnik Krasniqi teach? | `answered`; rows `[{"value": 5}]` with `{"value": ("",)}`; answer has 5 |
| `test_passed_asks_back` | How many students passed Databases in Fall 2025? | status `clarification`; 2 or 3 choices; no step named `execute_sql` |
| `test_choices_keep_conditions` | Who scored at least 90 in Databases? | either status `clarification` and every choice contains "90", or status `answered` with rows Arta, Gent, Hana (both readings are acceptable) |

Do not run the live tests yourself: they call a paid model, and the orchestrator runs them at the milestone close. Verify only that the default run reports them as skipped.

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass, 6 skipped, no warnings.

- [ ] **Step 6: Commit**

```bash
git add tests/known_answer.py tests/test_known_answer.py tests/test_live.py
git commit -m "test: known-answer checks by field and on the visible answer; opt-in live tests"
```

---

## Milestone close

Run by the orchestrator.

1. **Live tests:** `RUN_LIVE=1 .venv/bin/pytest tests/test_live.py -q` with the model from `.env`. Owner's rule: if one fails, show the owner that run's trace before changing the model, the prompts or the test.
2. **Spec review:** the seven M3 acceptance criteria, each met or not met with evidence.
3. **Code review:** bugs.
4. **Complexity review:** `ponytail:ponytail-review` on the milestone diff.

Reviews run once, on the whole milestone diff. The spec's graph diagram is updated by the orchestrator to show `check_result` as its own node.
