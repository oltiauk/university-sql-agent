# University SQL agent

## What it is

You ask a question in English about a university database. A chat model writes one SQL query, the query runs read-only on a SQLite file, and the model words an answer from the rows that came back. Every run is saved as a trace: a JSON file that shows each step from the question to the answer. It is built with LangGraph (a library for writing an agent as a graph of steps) and Python's built-in `sqlite3`.

## Quick start

These commands are for a macOS or Linux shell, run from the repository root. The code was tested on Python 3.15.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in three values:

- `LLM_PROVIDER`: `openai`, `anthropic` or `ollama`.
- `LLM_MODEL`: the model name. No model name is written into the code; you must set one. The saved examples were produced with provider `openai` and model `gpt-6-luna`.
- The provider's key variable: `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Ollama runs locally and needs no key.

`.env` is listed in `.gitignore`, so the key is not committed.

Build the database, ask a question, then open the trace of that run:

```bash
python db/build.py
python -m agent.cli "Which students scored above the class average in Databases in Fall 2025?"
python -m agent.cli --trace <the run id it printed>
```

`db/build.py` creates `university.db` from `db/schema.sql` and `db/seed.sql`. The question command prints the answer, a blank line and `run: <run id>`. It does not print the SQL or the rows; those are in the trace.

Command options:

| Command | What it does |
|---|---|
| `python -m agent.cli "<question>"` | Asks a question of `university.db` in the current directory. |
| `--db PATH` | Uses another SQLite file. |
| `--traces DIR` | Saves traces to, or reads them from, `DIR` instead of `traces/`. |
| `python -m agent.cli --trace <run id>` | Prints a saved run step by step. |

Exit codes: 0 for a normal end (including "Nothing matched.", "No value is available" and a question back), 1 when the last run ended `failed`, `mismatch` or `error`, 2 for a usage problem, a missing database file, missing model settings, a model that could not be created, or an unknown run id.

## How it works

The graph has five nodes (steps). Each node reads the shared state and adds to it. All five live in `agent/graph.py`; the database helpers they call are in `agent/db.py` and the two prompts are in `agent/prompts.py`.

```
load_schema -> generate_sql -+- question back ----------------> END  clarification
                             +- neither SQL nor a question ---> END  failed
                             +- SQL -> execute_sql -+- error, first attempt --> generate_sql (one retry)
                                                    +- error, second attempt -> END  failed
                                                    +- rows -> check_result -+- no rows ------------> END  empty
                                                                             +- not one value ------> END  mismatch
                                                                             +- one row, all NULL --> END  no_value
                                                                             +- otherwise -> answer -> END  answered
```

- **load_schema** opens the database read-only and reads every table and view: its `CREATE` statement followed by its first 3 rows. This text is all the agent knows about the database.
- **generate_sql** makes one model call with the schema text and the question. The model replies in a fixed structure (structured output): either one SQL query plus a flag `expects_single_value` (true when the question asks for exactly one value, such as one count), or a question back with 2 to 3 suggested questions. On a retry the call also carries the previous SQL and its error.
- **execute_sql** checks the SQL and runs it. A rejected query and a database error are treated the same way: they are recorded as the error and count as a failed attempt.
- **check_result** looks at the rows in plain code, with no model call, and ends the run with a fixed message when they cannot support an answer (see the table below). When nothing is wrong it records `checked: True`, so the trace shows the check ran.
- **answer** makes one model call with the question, the SQL, the column names and the rows, and asks for one or two sentences that use only those rows. An empty reply ends the run as `failed`.

"Multi-step reasoning" in this design means two things: the visible path through the graph (write, check and run, retry once on failure, check the result, answer), and subqueries inside the one SQL statement, such as comparing each grade with a class average. The agent runs one query per question. That was the owner's choice, to keep each run small and easy to follow; it does not plan several queries.

Each question is its own run. Nothing is remembered between questions.

**What `answered` means.** Status `answered` means the query ran and an answer was worded from its rows. It does not mean the answer was verified. The query can be valid and still not match what you meant, and the sentence can misstate the rows. The trace is where you compare the SQL, the rows and the sentence.

## Problem handling

| Situation | What the agent does | Status |
|---|---|---|
| The SQL fails: the database rejects it, the check refuses it, or it runs longer than 5 seconds | Sends the error and the failed SQL back to `generate_sql` for one corrected query | Whatever the corrected query leads to |
| The SQL fails twice | Stops. There is never a third attempt. Answer: "The question could not be answered. Last error: ..." | `failed` |
| The model returns neither SQL nor a question | Stops with the same "could not be answered" message | `failed` |
| The question is unclear (the answer depends on something neither the question nor the database states, such as a pass mark) | Asks a question back with 2 to 3 complete questions to pick from. Runs no SQL. If the model's reply contains both a question and SQL, the question wins and the SQL is dropped | `clarification` |
| The query returns no rows | Answer: "Nothing matched." The answer step is not called | `empty` |
| The model said one value was expected, but the result is not exactly one row with one column | Answer, for example: "The question asks for a single value, but the query returned 5 rows." The model is not asked to pick one and there is no retry | `mismatch` |
| One row comes back and every value in it is NULL, such as an average over nothing | Answer: "No value is available for average_grade." (it names the columns) | `no_value` |
| A count of zero | Treated as a value and answered like any other | `answered` |
| More than 100 rows | Keeps the first 100 and tells the answer step the list is incomplete | `answered` |
| The answer step returns empty text | Stops with the "could not be answered" message | `failed` |
| A step breaks: the provider call raises, or you press Ctrl-C | The trace is still saved, with a step for the node that broke. Answer: "Run stopped at generate_sql: RuntimeError." Only the error's class name is kept, because a provider's error text can carry request details | `error` |

In the command line, a question back prints the question and the numbered choices and waits for one line. A number sends that choice as a new question. Any other text is added to the original question in brackets, as in `How many students passed? (at least 60)`, and sent as a new question. Enter stops. Either way the follow-up is a new, ordinary run with its own trace.

The scripted examples of a retry, a double failure and a mismatch come from a fake model, because a real model does not fail on demand. See [examples/README.md](examples/README.md).

## Works on any database

The agent learns the database at run time from whichever SQLite file `--db` names. For each table and view it reads the `CREATE` statement and the first 3 rows, with each value cut at 100 characters. The sample rows show the model how values are written, for example that a semester is stored as `'2025-Fall'`, which a table definition alone does not show.

Nothing in `agent/` is specific to the university database. `tests/test_vocabulary.py` fails if any file in `agent/` contains a word from the university schema, and `tests/test_library.py` checks that the prompts mention neither database.

To see it, build the second database (a small library with books, members and loans) and ask it a question with only `--db` changed:

```bash
python db/build.py db/library_schema.sql db/library_seed.sql library.db
python -m agent.cli "Which books are currently on loan?" --db library.db
```

Comments written inside a `CREATE TABLE` statement travel with the schema text, because SQLite stores the statement as written. `db/library_schema.sql` uses this: the comment on `returned_on` says it is NULL while the book is still out, and the model sees that comment. Comments written above the statement, as in `db/schema.sql`, are not stored and do not reach the model.

## Tracing

Every run writes one JSON file to `traces/<run id>.json` (the folder is git-ignored). The run id is the start time in UTC plus six random hex characters, for example `20261126-060948-2af9a3`. A trace holds:

- the run id, start time, question, database path, provider and model name, final status and answer;
- one entry per node in the order it ran, with the time, the duration in milliseconds and what that node added to the state: the schema text, the SQL, the error, the column names and rows, the clarification and its choices, the answer.

A retried run shows `generate_sql` and `execute_sql` twice, with the error between them. The API key is never in a trace: it is passed only to the model constructor and never enters the graph state, and `tests/test_key_safety.py` checks this.

The first thing to try is your own run. Ask a question, note the run id it prints, and open it:

```bash
python -m agent.cli "How many enrolments are there in Spring 2026?"
python -m agent.cli --trace <the run id it printed>
```

You can also read the saved example traces on GitHub without a key or anything installed. [examples/README.md](examples/README.md) lists eleven runs, each with a link to its trace: eight from a real model and three scripted with a fake model. To print one locally:

```bash
python -m agent.cli --trace 20261126-060948-2af9a3 --traces examples/traces
```

This is that scripted retry run (fake model). The `load_schema` step, which prints the full schema text with its sample rows, is trimmed here for length:

```
Run 20261126-060948-2af9a3
Started:  2026-11-26T06:09:48.846690+00:00
Database: university.db
Model:    scripted / fake
Status:   answered
Question: How many students are there?

[load_schema] 1 ms
  schema:
    (trimmed: five CREATE TABLE statements, each with 3 sample rows)

[generate_sql] 0 ms
  sql: SELECT COUNT(*) AS n FROM student
  expects_single_value: False
  attempts: 1

[execute_sql] 0 ms
  error: no such table: student

[generate_sql] 0 ms
  sql: SELECT COUNT(*) AS student_count FROM students
  expects_single_value: True
  attempts: 2

[execute_sql] 0 ms
  columns: ['student_count']
  rows:
    [8]
  truncated: False
  error: 

[check_result] 0 ms
  checked: True

[answer] 0 ms
  answer: There are 8 students.
  status: answered
```

The tracing is our own code (`agent/trace.py`, about 60 lines) rather than LangSmith, LangChain's hosted tracing service. It works offline and needs no account, which matters when demonstrating it, and because only our code writes the trace, we decide exactly what goes into it, which keeps the rule that no key reaches a trace under our control. It records each node's output from LangGraph's own update stream, so the trace follows the path the graph actually took.

## Tests

```bash
pytest
```

The default run makes no network calls and needs no key. At the time of writing it reports 124 passed and 7 skipped (the live tests). The tests use a fake model (`tests/fakes.py`) that replays scripted replies and records every message it was sent.

| File | What it covers |
|---|---|
| `tests/test_seed.py` | The seed data: the Databases 2025-Fall class, the above-average query, that a plausible wrong query gives a different result, totals and constraints. |
| `tests/test_db.py` | The read-only connection, the SQL check, the schema reader, the 100-row cap and the 5-second limit. |
| `tests/test_graph.py` | The graph with a scripted model: the question and schema reach the model, the SQL and rows reach the answer step, the retry carries the previous SQL and error, two failures stop, clarification, and each result check. |
| `tests/test_trace.py` | What a trace contains, that it loads back, and that it is saved when a step raises or the run is interrupted. |
| `tests/test_cli.py` | The command line: printed output, exit codes, follow-up after a question back, bad run ids. |
| `tests/test_key_safety.py` | A recognisable fake key appears in no trace, printed output or log, even when a provider error contains it. |
| `tests/test_known_answer.py` | The known-answer helper (`tests/known_answer.py`) and three demonstrations: wrong SQL, an answer that drops a name, and an answer with two numbers swapped are each caught. |
| `tests/test_library.py` | The second database: the same graph and command line answer with only `--db` changed. |
| `tests/test_examples.py` | The example generator, run into a temporary folder. |
| `tests/test_vocabulary.py` | No file in `agent/` contains a word from the university schema. |

The offline tests check what the SQL step sends to the model and how it reads the reply. They cannot check that a real model writes correct SQL. The live tests do that: seven questions put to a real model. Five are checked against the known answer by the returned rows and by the values in the answer text, not by the SQL text; one of these runs on the library database. The other two check the clarification rules: "How many students passed Databases in Fall 2025?" must come back as a question, and for "Who scored at least 90 in Databases?" every suggested question must keep the 90 (a direct answer with the right rows is also accepted). When one fails, its message includes the full trace. They need the provider, model and key in `.env`, they make real model calls, and they run only when you ask:

```bash
RUN_LIVE=1 pytest tests/test_live.py
```

## Design decisions

| Decision | What was chosen | Why |
|---|---|---|
| Queries per question | One query, with one retry if it fails | Smallest design that is easy to explain and trace. Multi-step work happens in the graph path and in subqueries. |
| Catching valid but wrong SQL | The trace plus known-answer tests, not a second model checking the first | Deterministic and costs no extra model call. A checking model can also be wrong. |
| Unclear questions | Ask back and end the run | Never presents a guess as a fact, and needs no saved run state. The person's reply starts a new run. |
| Result checks | Plain code in `check_result` | Fixed, accurate messages; no model is asked to judge or to pick a row. |
| What the model sees of the database | `CREATE` statements plus 3 sample rows per table | Without the sample rows the model guesses value formats (`'Fall 2025'` instead of `'2025-Fall'`), and a filter that matches nothing reads as a true "nothing". |
| Safety of the query | Read-only at two layers plus a time limit: the SQL check accepts only one statement starting with `SELECT` or `WITH`, and the connection is opened read-only; a query is stopped after 5 seconds | A write hidden behind `WITH` passes the check and is stopped by the read-only connection. The time limit stops a query that would never finish. |
| Model provider | Any of three, chosen in `.env`, created through LangChain's `init_chat_model` | The graph takes a model object and never sees a provider name or key, so switching provider changes no agent code. |
| Tracing | Own JSON files, not LangSmith | Works offline, needs no account, and keeps the no-key rule in our code. |

## Known limits

These are stated plainly because each one can give a wrong answer while every check passes.

- Status `answered` means the query ran and an answer was worded from its rows. It does not mean the query matched what the person meant. The tests catch specific known mistakes on known data; they cannot prove that every question is translated correctly.
- A filter on a value written differently from the data, for example a semester format that is not among the sample rows, can produce a count of zero that looks like a fact. The trace shows the filter that was used; nothing detects the mismatch automatically.
- The final sentence is written by a model and can misstate the rows. The live tests check the sentence for every expected value on the known questions. For any other question, the trace is where you compare the rows with the sentence.
- The schema reader fails on a database that has a table from a SQLite extension that is not loaded, or text that is not valid UTF-8. The run then ends as `error` at `load_schema`. This was found in a code review and not fixed.
- Passing the live questions on one model says nothing certain about other models, other phrasings or repeated runs.
- Text stored in the database is sent to the model, in the sample rows and in the result rows. A database containing hostile text could steer the model. Read-only access protects the data, not the truth of the answer. This is not defended against here; see [docs/production.md](docs/production.md).
- One query per question: a question that needs the result of one query to write the next is answered only if it fits in a single statement with subqueries.
- The SQL check rejects any `;` inside the query, even inside a quoted string.
- SQLite only, and no memory between questions.
- A web interface (choosing a provider, a clickable list of traces) is planned but not built.

## Layout

```
agent/
  cli.py            command line: ask a question, follow up a question back, print a trace
  db.py             open read-only, read the schema with sample rows, check the SQL, run it with row cap and time limit
  graph.py          the state, the five nodes and their edges; build_graph(model, db_path)
  prompts.py        the two system prompts: write the SQL, word the answer
  trace.py          record a run from the graph's update stream, save it, load it, print it
db/
  schema.sql        university schema: teachers, students, courses, offerings, enrollments
  seed.sql          university seed data
  library_schema.sql  second database: books, members, loans
  library_seed.sql  library seed data
  build.py          builds a SQLite file from a schema file and a seed file
tests/
  conftest.py       builds a fresh copy of each database for the tests that use one
  fakes.py          the scripted fake model
  known_answer.py   test-only checks of rows and answer text against known values
  test_*.py         the test files listed under Tests
examples/
  make_examples.py  runs the example questions and writes examples.json, README.md and traces/
  examples.json     index of the example runs
  README.md         each example: question, SQL, answer, status, link to its trace
  traces/           the saved trace of each example
docs/
  production.md     what production would need
  superpowers/      the design spec and the milestone plans
  grills/           the decision record from the design session
.env.example        the model settings to copy into .env
requirements.txt    Python packages
pyproject.toml      pytest settings
```

The examples can be regenerated from the repository root. `python examples/make_examples.py` reruns the three scripted examples with the fake model and needs no key. `python examples/make_examples.py --live` reruns all eleven with the model from `.env`. Each run replaces the trace of every example it reruns, so the run ids change.

## More

- [examples/README.md](examples/README.md): eleven example questions with their SQL, answers and traces.
- [docs/production.md](docs/production.md): what bringing this to production would need.
