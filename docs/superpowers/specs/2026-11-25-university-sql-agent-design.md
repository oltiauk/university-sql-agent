# University DB + LangGraph QA agent: design

Source assignment: "Homework Assignment: University Database + LangGraph QA Agent" (PDF).
Decision record: `docs/grills/2026-11-25-sql-agent-spec/state.json`.

Status: approved by the owner on 2026-11-26.

Items marked **(call)** were decided by the orchestrator, not by the owner, and are open to correction.

## Intent

A person asks a question in English. A LangGraph agent writes one SQL query, runs only that query read-only, and answers from the rows that came back. Every run leaves a record showing what happened. The agent learns the schema from whatever database it is connected to, so the same prompts and graph work on a different database.

It is a homework submission followed by an interview: the owner must present it, walk through traces, and defend every choice. Small and explainable beats capable.

## Goals

- Meet every requirement and deliverable in the PDF.
- Any answer can be traced back: question → graph steps → SQL → rows → answer.
- Swap the database file and nothing in the agent code or prompts changes.

## Non-goals

- More than one SQL query per question.
- A model that checks the first model's SQL (may be added later if tests show a pattern).
- Pausing and resuming a run.
- Remembering earlier questions in a conversation. Each question is its own run.
- Databases other than SQLite.
- LangSmith. **(call)** Tracing is our own.

## Anti-goals

- A wrong answer presented as a right one because nothing failed.
- An API key appearing in any trace, log, file or commit.
- University-specific words in agent code or prompts.
- Code the owner cannot explain line by line.

## Approaches considered

| Decision | Options | Picked | Why |
|---|---|---|---|
| Queries per question | One query with retry / capped loop of queries / plan then run | One query with retry | Owner's choice: smallest, easiest to explain. "Multi-step reasoning" is met by the graph path (write, guard, run, retry, answer) and by subqueries inside the one query. |
| Wrong but valid SQL | Trace + known-answer tests / model check node / both | Trace + known-answer tests | Deterministic, no extra model call. |
| Model provider | Claude / OpenAI / Ollama | All three, chosen at start | Owner wants it model-agnostic. |
| Unclear question | Ask back and end / assume and say so / pause and resume | Ask back and end | Never presents a guess as fact; no saved run state. |
| Tracing | Own / LangSmith | Own **(call)** | Works offline in the interview, keeps the key rule under our control. |

## PDF requirements and where each is met

| PDF section | Met by |
|---|---|
| 1. Database design (teachers, students, courses; taught in a semester; enrolment; grades; names) | M1 |
| 2. LangGraph agent: English in, SQL, execute, readable answer; aggregations, joins, filters by semester, course, teacher or student; multi-step reasoning | M2, M3 |
| 3. DB-agnostic design | Schema read at run time, vocabulary test (M2.5), second database (M4) |
| 4. Tracing: user input → nodes → SQL → results → answer | M2 |
| 5. Detect and handle incorrect SQL, empty or inconsistent results, ambiguous questions; demonstrate debugging | M3. "Inconsistent results" is handled at run time by the result checks (`mismatch`, `no_value`), and in tests by the known-answer check |
| 6. Unit tests: DB queries and joins, SQL generation, end-to-end agent | Tests section |
| 7. Clean, modular code with clear responsibilities | Layout |
| 8. Production considerations: reliability, scalability, monitoring and tracing, security, deployment | `docs/production.md` (M4) |
| Deliverables: schema and seed, app code, tests, design docs, example queries and outputs, execution traces, public GitHub repo | M1 to M4 |

The PDF does not require a UI, a second database, or a choice of providers. Those are the owner's additions.

## Stack

Python 3.11+, stdlib `sqlite3`, `langgraph`, `langchain` (one chat-model interface across providers via `init_chat_model`), provider packages `langchain-anthropic`, `langchain-openai`, `langchain-ollama`, `pytest`. Nothing else without a reason. **(call)**

## Layout **(call)**

```
db/schema.sql        university schema
db/seed.sql          seed data
db/build.py          builds university.db from the two files
agent/db.py          open read-only, read schema, guard, run query
agent/prompts.py     the two prompt templates
agent/graph.py       state, nodes, edges, build_graph(model, db_path)
agent/trace.py       record a run, save it, print it
agent/cli.py         ask a question, show a trace
tests/
examples/            example questions, outputs, saved traces
docs/production.md
```

## Database

Five tables **(call)**:

- `teachers(id, name NOT NULL)`
- `students(id, name NOT NULL)`
- `courses(id, code, title)`
- `offerings(id, course_id, teacher_id, semester)`: a course taught by a teacher in a semester, e.g. `'2025-Fall'`
- `enrollments(student_id, offering_id, grade)`: primary key on the pair; `grade` is 0 to 100, NULL until graded

Foreign keys declared. Seed data is small enough to read in one screen but supports joins, aggregates and filters by semester, course, teacher and student: about 3 teachers, 8 students, 4 courses, 6 offerings over 2 semesters.

The seed must contain this class as one offering (Databases, 2025-Fall):

| Student | Grade |
|---|---|
| Arta | 95 |
| Blerim | 88 |
| Dren | 82 |
| Era | 70 |
| Fatos | 55 |

Average 78.0. "Which students scored above the class average in Databases in Fall 2025?" must return exactly Arta, Blerim, Dren. Student names in the seed are unique.

The seed must also make plausible wrong queries give a different result from the right one **(call, from the independent review)**:

- Another offering has grades high enough that the average over all enrolments is above 82. A query that compares against the overall average instead of the class average then returns only Arta and Blerim, and the known-answer test fails.
- At least one student is enrolled in two offerings, so counting enrolments and counting students give different numbers.

## DB layer (`agent/db.py`)

- Opens the file read-only (`file:<path>?mode=ro`, with the path escaped so characters such as `?` and `#` cannot drop the read-only setting), so writes fail at the database even if the guard is bypassed. A write hidden behind `WITH` passes the guard and is stopped here.
- `get_schema(conn)`: returns, for each table and view, its `CREATE` statement from `sqlite_master` followed by its first 3 rows, each value cut at 100 characters. **(call)** The sample rows show the model how values are written (for example `'2025-Fall'`), which the table definitions alone do not. Without them a filter on `'Fall 2025'` returns nothing and the run reports no records, which is a wrong answer that looks right. This text, read from the connected database, is the only thing the agent knows about it.
- Guard: exactly one statement, starting with `SELECT` or `WITH`. Anything else is rejected with a clear message before it reaches the database.
- `run_query(conn, sql)`: returns column names and rows, capped at 100 rows with a `truncated` flag. **(call)** A query is stopped after 5 seconds and reported as a database error, so a query that never finishes cannot hang the app. **(call, from the M1 code review)**

## Graph

State: `question, schema, sql, expects_single_value, columns, rows, truncated, error, attempts, clarification, choices, checked, answer, status`.

```
load_schema → generate_sql ─┬─ question back ────────→ END   (status: clarification)
                            ├─ neither SQL nor question → END (status: failed)
                            └─ sql → execute_sql ─┬─ error, attempts < 2 → generate_sql
                                                  ├─ error, attempts = 2 → END (status: failed)
                                                  └─ ok → check_result ─┬─ no rows ──────────→ END (status: empty)
                                                                        ├─ wrong shape ──────→ END (status: mismatch)
                                                                        ├─ one row, all NULL → END (status: no_value)
                                                                        └─ otherwise → answer → END (status: answered)
```

Five nodes: `load_schema`, `generate_sql`, `execute_sql`, `check_result`, `answer`. The result checks are their own node **(call)** so that a stopped run shows as its own step in the trace; when nothing is wrong that step records `checked: True`.

- **generate_sql**: one model call with the schema and question. Structured output: either `sql` plus `expects_single_value` (true when the question asks for one value, such as one overall average or one count), or `clarification` plus 2 to 3 `choices`. Each choice is a complete question that can be asked as-is. On a retry the previous SQL and its error are included.
  Decided by the owner: a question back always wins. If the reply contains a clarification, the run ends as `clarification` even when the reply also contains SQL; that SQL is dropped and never runs, and nothing executes until the person answers. A reply with neither SQL nor a question ends as `failed`. **(call)**
  These rules are part of this prompt **(call, from the independent review)**:
  - Ask for clarification when the answer depends on something neither the question nor the database states, such as a pass mark, what "current" means, or which of two people with the same name.
  - Every choice keeps all conditions of the original question and fills in only the missing detail.
  - Do counting, averaging, minimum and maximum inside the SQL, and add no `LIMIT` the question did not ask for.
  - Give every output column a plain descriptive name.
  Added by the owner after trying the web interface (2026-11-26):
  - When a question asks for information the database does not hold (email addresses when only names are stored), the agent says what is not stored and what is, and offers only questions the existing columns can answer. It runs no SQL and never makes the information up. This uses the clarification exit; there is no new status.
  - When the missing detail is one the person decides (a passing cutoff), each choice states one concrete value, so a picked choice is answered at once.
  - A choice never supplies facts the database lacks.
  - The answer rounds averages and other non-whole numbers to at most two decimal places unless the question asks for more.
- **execute_sql**: guard, then run. A guard rejection and a database error are treated the same: both set `error` and count as a failed attempt.
- **Retry**: exactly one. Two attempts in total, never a third.
- **failed**: the answer is "The question could not be answered. Last error: <error>". No invented answer. Also used when the model returns neither SQL nor a question, or an empty answer.
- **Result checks**, decided by the owner. They run in this order after a successful query, are plain code with no model call, and each ends the run with a fixed, accurate message:
  - **empty**: no rows. Message: "Nothing matched."
  - **mismatch**: `expects_single_value` is true but the result is not exactly one row with one column. Message, for example: "The question asks for a single value, but the query returned 5 rows." The model is not asked to pick one. No retry. The trace records what was expected, what came back, and the rows.
  - **no_value**: one row whose values are all NULL, such as an average over nothing. Message: "No value is available for <column name>.", for example "No value is available for average_grade."
  - A zero is a value. It goes to the answer step like any other and is shown as zero. No retry: the owner's rule is one retry when the SQL fails, and an empty result is not a failure.
- **answer**: one model call with the question, the SQL and the rows. It is told to use only those rows, and to say so when the result was truncated.

`build_graph(model, db_path)` takes the model as an argument. The graph never sees a provider name or a key.

### Following up on a clarification **(call)**

Each question is its own run with no memory, so the caller (the CLI now, the UI later) builds the follow-up question:

- A chosen choice is sent as-is, because each choice is already a complete question.
- Typed text is joined to the original question by one small helper, `original + " (" + reply + ")"`, and sent as a new question.

Either way it is a new, ordinary run with its own trace, and the trace shows the full question that was asked. The graph gains no state for this. In the CLI, a clarification prints the question and numbered choices and reads one line: a number picks a choice, anything else is treated as typed text.

## Model selection

- CLI: provider, model name and key come from environment variables (`LLM_PROVIDER`, `LLM_MODEL`, and the provider's own key variable). `.env` is git-ignored; `.env.example` is committed. `LLM_MODEL` is required; no model name is hard-coded in the code. **(call)**
- All three providers are wired through the same call. One provider, the one whose key the owner supplies for the build, is verified live. The other two are verified only if a key or a running Ollama is available at the time. **(call)**
- Structured output uses LangChain's `with_structured_output`. If a model cannot produce it, the run ends as `error` (see Tracing). No fallback parser. **(call)**
- UI (milestone 5): provider picker and key entry, described there.
- The key is passed only to the model constructor. It is never placed in graph state, so it cannot reach a trace.

## Tracing

Our own, captured from the graph's update stream so each record follows the real node path.

One JSON file per run in `traces/<run_id>.json` (git-ignored; chosen examples are copied to `examples/traces/`):

```
run_id, started_at, question, database, provider, model, status, answer,
steps: [ { node, at, duration_ms, output } ]
```

`output` is what that node added to the state: the SQL, the error, the columns and rows, the clarification, the answer. A retried run shows `generate_sql` and `execute_sql` twice, with the error between them.

When a step itself breaks, for example the provider call fails or the run is interrupted, the trace is still saved. Decided by the owner: it shows which step failed and a short safe message. The record gets a step for that node, status `error`, and the answer "Run stopped at <node>: <error type>." Only the error's type is kept; the provider's own error text is never stored or printed, because it can carry request details.

CLI:

- `python -m agent.cli "question"` prints the answer and the run id. `--db <path>` selects the database. The answer is the model's sentence, as decided by the owner; the SQL and rows are in the trace, not printed beside the answer.
- `python -m agent.cli --trace <run_id>` prints that run step by step.
- Exit codes **(call)**: 0 normally, including "Nothing matched", "No value is available" and a clarification; 1 when the last run ended `failed`, `mismatch` or `error`; 2 for a usage problem, a missing database, a model that could not be created, or an unknown run id.

## Tests

No network in the default test run.

- **Database**: the above-average query returns the three students; joins and aggregates over the seed give known values; the guard rejects `INSERT`, `DROP` and two statements in one string; a write through the read-only connection fails; the schema reader returns all five tables.
- **SQL generation, offline** **(call, from the second independent review)**: the fake model records what it was sent. Tests assert that the question and the connected database's schema text reach the model, that on a retry the previous SQL and its error reach it too, and that both kinds of reply (SQL, or clarification with choices) are read correctly. This is the unit test for "SQL generation from natural language" that runs without a key; the live tests below measure whether the SQL is right.
- **Agent with a scripted fake model**: happy path; SQL fails then succeeds (trace shows two attempts); SQL fails twice (status failed, no third attempt); unclear question (clarification with choices, no SQL run); a returned choice asked as a new question reaches an answer; a typed follow-up reaches an answer; empty result ("Nothing matched."); a single value expected but five rows returned (status `mismatch`, the answer step is never called, the trace shows expected one value and got five rows); a NULL average (status `no_value`, the message names the column); a count of zero (status `answered`, the answer shows 0); truncated result.
- **Known-answer check**: a helper in `tests/`, not shipped code. Decided by the owner: it checks two things, each value against the right field, and the answer people actually see.
  - **Rows, by field.** Expected rows are written with named fields, for example `{student: "Arta", grade: 95, average: 78.0}`. Each field lists the words a matching column name may contain (for `average`: avg, average, mean). **(call)** The check passes when there is one assignment of fields to result columns, the same for every row, under which the column names fit and every expected row matches exactly one result row. Row counts must be equal; row order and extra columns are ignored; numbers are compared rounded to 1 decimal. A grade and an average that come back swapped, or under the wrong column name, fail.
  - **The visible answer.** The final answer text must contain every expected value, and none of the values listed as must-not-appear (for the above-average question: Era and Fatos). Three students qualify and the answer names two: the test fails. **(call)** Numbers match with or without a trailing `.0`. This checks that each value is present in the sentence, not which number is attached to which thing. One test checks that too, for one question only (below); there is no general system for it.
  - On a mismatch it says what was wrong, for example "expected 3 rows, got 1" or "answer does not mention Dren". On a mismatch it reports, for example, "expected 3 rows, got 1".
- **Wrong-answer demonstration**: the fake model returns the correct SQL but the answer "Arta and Blerim scored above the class average." The test asserts the check reports "answer does not mention Dren".
- **Swapped-answer demonstration**: the fake model returns the correct SQL but the answer "Grade: 78. Class average: 95." The test asserts the labelled check fails.
- **Wrong-SQL demonstration**: the fake model returns `ORDER BY grade DESC LIMIT 1` for the above-average question. The test asserts the known-answer check reports "expected 3 rows, got 1" and that the trace's `generate_sql` step contains the faulty SQL.
- **Key safety**: run with a recognisable fake key; assert it appears in no trace file and no log output.
- **SQL generation (live, opt-in)** **(call)**: about 6 known-answer questions, each with expected rows by field and expected values in the answer, run against a real model. The comparison is on the returned rows and the answer text, not the SQL text. One question asks for two numbers at once and, decided by the owner, asks for a labelled answer: "What is Arta's grade in Databases in Fall 2025, and what is the class average? Answer with two labels: grade and class average." The rows must give grade 95 and average 78.0, each in its own field. The answer text must have 95 directly after the label "grade" and 78 directly after the label "class average" (only punctuation or spaces between label and number, case ignored). "Grade: 78, class average: 95" fails. This labelled check exists for this one question only. Skipped automatically when no provider is configured. This is the test that catches a prompt regression. Two of the questions test the clarification rules: "How many students passed Databases in Fall 2025?" must come back as a clarification, and for "Who scored at least 90 in Databases?" every returned choice must still contain the 90. For that second question an answer across both semesters (Arta, Gent, Hana) is also accepted, because not asking back is a reasonable reading. **(call)**
- **Second database**: see milestone 4.

## Milestones

Each milestone ends with a spec review against its acceptance criteria and a code review. Milestones are built in order.

### M1: Database

Acceptance criteria:

1. `python db/build.py` creates `university.db` from `db/schema.sql` and `db/seed.sql`.
2. The five tables exist with the relationships above; every student and teacher has a name.
3. The Databases 2025-Fall class is seeded exactly as in the table above.
4. `get_schema` returns the `CREATE TABLE` text for all five tables.
5. The guard and the read-only connection behave as specified.
6. Database tests pass.

### M2: Agent with tracing

Acceptance criteria:

1. `python -m agent.cli "<question>"` returns an answer from a real model for a join, an aggregate and a filtered question.
2. The graph is built with LangGraph and contains `load_schema`, `generate_sql`, `execute_sql`, `answer`.
3. Every run writes a trace file with the fields above; `--trace <run_id>` prints it.
4. The trace shows question, each node in order, the SQL, the rows and the answer.
5. `agent/` contains none of the words student, teacher, course, enrollment, grade, semester (checked by a test).
6. Happy-path agent test passes with the fake model.

### M3: Problem handling

Acceptance criteria:

1. Failing SQL is retried once with the error fed back; the trace shows both attempts.
2. Two failures end with status `failed` and an honest message.
3. An unclear question ends with status `clarification`, a question back, 2 to 3 choices, and no SQL run.
4. A chosen choice and a typed follow-up each run as a normal question.
5. An empty result ends with status `empty` and "Nothing matched."; a single value expected but another shape returned ends with status `mismatch` and the reason in the trace; a one-row all-NULL result ends with status `no_value`; a zero is answered as zero.
6. The wrong-SQL demonstration test and the key-safety test pass.
7. Live known-answer tests pass against at least one provider and skip cleanly without one.

### M4: Proof and paperwork

Acceptance criteria:

1. A second, smaller database with an unrelated schema (a library: books, members, loans) **(call)** is built from its own schema and seed files.
2. The same graph and prompts answer a question on it with only `--db` changed; a fake-model test and one live test cover this.
3. `examples/` holds at least 6 example questions with outputs, covering aggregation, multi-table join, each filter type, a retried run, a clarification, a failed run and a mismatch run, each with its saved trace. Examples come from a real model where the model produces them naturally. The retried, failed and mismatch runs are produced with the scripted fake model, because a real model rarely fails on demand, and are labelled "scripted" in `examples/`. **(call)**
4. `README.md` covers setup, how to run, the architecture, and the design decisions with reasons.
5. `docs/production.md` covers reliability, scalability, monitoring and tracing, security, deployment.
6. The repository is published publicly, after the owner confirms.

### M5: Web interface (built)

Plan: `docs/superpowers/plans/2026-11-26-m5-web-interface.md`. Designs by the owner: `docs/design/sql-agent-ui.html` (chat and trace panel) and `docs/design/start-screen.html` (start screen).

- `python -m agent.server [--db FILE] [--traces DIR] [--examples FILE] [--port N]`: a local server on Python's standard library, bound to 127.0.0.1, serving one page and JSON endpoints (state, Ollama models, setup, ask, runs).
- Every answer shown is a saved trace record from a real run; the trace panel draws that record's steps: SQL, rows, errors, retries. The last 20 saved runs for the database are listed on load.
- Start screen: choose Claude, OpenAI or Ollama; name the model, or pick from the models Ollama has installed; type the key in a masked field. The key is kept in the server's memory until the app stops and never written to disk, a trace, a log, a response or browser storage. The screen is skipped when the environment already has provider, model and key.
- A clarification shows its choices as buttons with a free-text box beside them. A clicked choice is asked as-is; typed words are joined to the original question. Nothing runs until the person answers; clicks on buttons drawn less than 400 ms ago, and the second click of a double-click, are ignored.
- Refuses requests from other sites: Host header check, JSON-only POSTs, no framing, a 1 MB body cap, a 2,000-character question cap.
- Tested: the server by automated tests, including that a typed key never comes back from any endpoint. The page by source-level checks and by hand in a browser; it has no automated browser tests.

## Known limits

An independent review asked whether every check here can pass while a user still gets a wrong answer. It can. These limits are stated in the README rather than hidden:

- Status `answered` means the query ran and an answer was worded from its rows. It does not mean the query matched what the person meant. The tests catch specific known mistakes on known data; they cannot prove every question is translated correctly.
- A filter on a value written differently from the data (for example a semester format not among the sample rows) can produce a count of zero that looks like a fact. The trace shows the filter that was used; nothing detects the mismatch automatically.
- The final sentence is written by a model and can misstate the rows. The live tests check the sentence for every expected value on the known questions; for any other question the trace is where the rows and the sentence can be compared.
- The schema reader fails on a database with a table from a missing SQLite extension, or with text that is not valid UTF-8. Found in the M1 code review and not fixed.
- Passing the live questions on one model says nothing certain about other models, other phrasings or repeated runs.
- Text stored in the database is sent to the model as part of the schema sample and the result rows. A database containing hostile text could steer the model. Read-only access protects the data, not the truth of the answer. Covered in `docs/production.md` under security; not defended against here.

## Left to the builder

File-internal structure, function names, exact prompt wording, exact seed rows beyond the fixed class, test file organisation, the print format of `--trace`.
