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
- `LLM_MODEL`: the model name. No model name is written into the code; you must set one. The eight live examples in `examples/` were produced with provider `openai` and model `gpt-6-luna`; the three scripted ones use a fake model.
- The provider's key variable: `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Ollama runs locally and needs no key.

`.env` is listed in `.gitignore`, so the key is not committed.

Build the database, ask a question, then open the trace of that run:

```bash
python db/build.py
python -m agent.cli "Which students scored above the class average in Databases in Fall 2025?"
python -m agent.cli --trace <the run id it printed>
```

`db/build.py` creates `university.db` from `db/schema.sql` and `db/seed.sql`. A question prints the answer and `run: <run id>`; the SQL and rows are in the trace. By default the question goes to `university.db` in the current directory and traces go to `traces/`; `--db PATH` and `--traces DIR` change these.

Exit codes: 0 for a normal end, 1 when the run ended `failed`, `mismatch` or `error`, 2 for a usage or setup problem.

## Web interface

The same agent can be used from a page in the browser. Start the local server, then open the address it prints (`http://127.0.0.1:8000/` by default; `--port` changes it):

```bash
python -m agent.server --examples db/university_questions.txt
python -m agent.server --db library.db --examples db/library_questions.txt
```

`--examples` names a text file with one question per line; each one becomes a button above the question box. `--db` and `--traces` work as in the command line. The server listens on 127.0.0.1 only.

If `.env` names a provider, a model and (for OpenAI or Anthropic) a key, the page opens straight on the chat. Otherwise it opens on a setup screen that asks which AI should answer: Claude, OpenAI or Ollama. For Claude and OpenAI it asks for the model name and the API key; the key field is replaced by a note when the provider's key variable is already set in the environment. For Ollama it lists the models installed on this computer and needs no key. A typed key is sent once to the local server and kept in its memory until you stop it. It is not written to `.env`, a trace or the browser's storage. The model name under the question box opens the setup screen again.

Every answer on the page is a saved run, and its trace panel shows that run's trace file. On opening, the page shows the last 20 runs for the database. A question back shows its choices as buttons: clicking one asks it as a new question, and typed words are added to the original in brackets, as in the command line.

## How it works

The graph has five nodes (steps). Each node reads the shared state and adds to it. All five live in `agent/graph.py`; the database helpers they call are in `agent/db.py` and the two prompts are in `agent/prompts.py`. This path, plus subqueries inside the one SQL statement, is what "multi-step reasoning" means here; the agent runs one query per question.

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

- **load_schema** reads every table and view: its `CREATE` statement and its first 3 rows. This text is all the agent knows about the database.
- **generate_sql** makes one model call with the schema and the question. The reply has a fixed structure (structured output): one SQL query plus a flag `expects_single_value` (true when the question asks for exactly one value, such as one count), or a question back with 2 to 3 suggested questions. A retry also carries the previous SQL and its error.
- **execute_sql** checks the SQL and runs it. A rejected query and a database error both count as a failed attempt.
- **check_result** looks at the rows in plain code and ends the run with a fixed message when they cannot support an answer. Otherwise it records `checked: True`, so the trace shows the check ran.
- **answer** makes one model call with the question, SQL, columns and rows, asking for one or two sentences that use only those rows.

## Problem handling

| Situation | What the agent does | Status |
|---|---|---|
| The SQL fails: the database rejects it, the check refuses it, or it runs longer than 5 seconds | Sends the error and the failed SQL back for one corrected query | Whatever the corrected query leads to |
| The SQL fails twice, the model returns neither SQL nor a question, or the answer step returns empty text | Stops; never a third SQL attempt. "The question could not be answered. Last error: ..." | `failed` |
| The question is unclear (the answer depends on something neither the question nor the database states, such as a pass mark) | Asks back with 2 to 3 complete questions to pick from and runs no SQL. If the reply has both a question and SQL, the question wins and the SQL is dropped | `clarification` |
| The question asks for something the database does not hold (email addresses when only names are stored) | Says what is not stored and what is, offers questions the existing columns can answer, and runs no SQL | `clarification` |
| No rows | "Nothing matched." The answer step is not called | `empty` |
| One value was expected, but the result is not one row with one column | For example "The question asks for a single value, but the query returned 5 rows." No retry; the model is not asked to pick | `mismatch` |
| One row, every value NULL (an average over nothing) | "No value is available for average_grade." (names the columns) | `no_value` |
| A count of zero | Answered like any other value | `answered` |
| More than 100 rows | Keeps the first 100 and tells the answer step the list is incomplete | `answered` |
| A step breaks: the provider call raises, or you press Ctrl-C | Saves the trace with a step for the node that broke, for example "Run stopped at generate_sql: RuntimeError." Only the error's class name is kept, because provider error text can carry request details | `error` |

In the command line, a question back prints the numbered choices and waits for one line: a number sends that choice as a new question, other text is added to the original in brackets (`How many students passed? (at least 60)`), Enter stops. The follow-up is a new run with its own trace; nothing else is remembered between questions.

## Works on any database

The agent learns the database at run time from whichever SQLite file `--db` names (each sample value is cut at 100 characters). Apart from the default `--db` file name (`university.db`, in `agent/cli.py`), nothing in `agent/` is specific to the university database. `tests/test_vocabulary.py` fails if any file in `agent/` contains one of a list of words from the university schema (see the test), and `tests/test_library.py` checks that the prompts mention neither database.

To see it, build the second database (a small library with books, members and loans) and ask it a question with only `--db` changed:

```bash
python db/build.py db/library_schema.sql db/library_seed.sql library.db
python -m agent.cli "Which books are currently on loan?" --db library.db
```

Comments written inside a `CREATE TABLE` statement travel with the schema text, because SQLite stores the statement as written; `db/library_schema.sql` uses this to tell the model that `returned_on` is NULL while a book is still out.

## Tracing

Every run writes one JSON file to `traces/<run id>.json` (git-ignored). The run id is the start time in UTC plus six random hex characters. A trace holds the question, database path, provider and model name, final status and answer, and one entry per node in the order it ran, with its duration and what it added to the state: the schema text, the SQL, the error, the columns and rows, the clarification and choices, the answer. The API key never appears in a trace: our code never reads it (the provider package reads it from the environment), so it never enters the graph state, and `tests/test_key_safety.py` checks this.

To open a trace of your own, use the last two commands in Quick start. The saved example traces can also be read on GitHub without a key or anything installed: [examples/README.md](examples/README.md) lists twelve runs: eight from a real model, three scripted, and one real wrong answer kept as recorded. The scripted ones use a fake model because a real model does not fail on demand. To print one locally:

```bash
python -m agent.cli --trace 20261126-104348-7ede58 --traces examples/traces
```

This is part of that scripted retry run (fake model). Most of the header, the `load_schema` step and the last three steps are trimmed for length:

```
Status:   answered
Question: How many students are there?

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
```

## Tests

```bash
pytest
```

The offline tests use a scripted fake model (`tests/fakes.py`) that replays fixed replies and records every message it was sent, so they need no key and no network. They cover the seed data, the read-only connection and SQL check, the graph's paths and result checks, the trace, the command line, key safety, the known-answer helpers with demonstrations of wrong runs being caught, the second database and the example generator. A clean run shows every test passing and the live tests skipped.

The live tests are opt-in: eleven questions put to a real model. Eight are judged by the returned rows (compared field by field) or the answer text, not by the SQL text; three of those check how numbers are shown (a long decimal rounded to two places while the trace keeps the full value, a requested precision used, a short decimal left alone). Three check asking back: an unclear question, a question whose conditions must survive into the choices, and a question about information the database does not hold. They need the provider, model and key in `.env` and make real model calls:

```bash
RUN_LIVE=1 pytest tests/test_live.py
```

## Design decisions

| Decision | What was chosen | Why |
|---|---|---|
| Queries per question | One query, with one retry if it fails | The smallest design that is easy to explain and trace. Multi-step work happens in the graph path and in subqueries. |
| Catching valid but wrong SQL | The trace plus known-answer tests, not a second model checking the first | Deterministic and costs no extra model call. A checking model can also be wrong. |
| Unclear questions | Ask back and end the run | Never presents a guess as a fact, and needs no saved run state. The person's reply starts a new run. |
| Result checks | Plain code in `check_result` | Fixed, accurate messages; no model is asked to judge or to pick a row. |
| What the model sees of the database | `CREATE` statements plus 3 sample rows per table | Without the sample rows the model guesses value formats (`'Fall 2025'` instead of `'2025-Fall'`), and a filter that matches nothing reads as a true "nothing". |
| Safety of the query | Read-only at two layers: the SQL check accepts one statement starting with `SELECT` or `WITH`, and the connection is opened read-only. A 5-second limit per query | A write hidden behind `WITH` passes the check and is stopped by the connection. The limit stops a query that would never finish. |
| Model provider | Any of three, chosen in `.env`, created through LangChain's `init_chat_model` | The graph takes a model object and never sees a provider name or key, so switching provider changes no agent code. |
| Tracing | Own JSON files (`agent/trace.py`), not LangSmith, LangChain's hosted tracing service | Works offline with no account, which matters when demonstrating it. Only our code writes the trace, so the no-key rule stays under our control. It records LangGraph's update stream, so it follows the path the graph actually took. |
| Web server | Python's built-in `http.server` and one HTML file with plain JavaScript, no web framework and no build step | One person on one computer needs no more. It adds no package, and the page is a single file that can be read in full. |

## Known limits

These are stated plainly because each one can give a wrong answer while every check passes.

- Status `answered` means the query ran and an answer was worded from its rows. It does not mean the query matched what the person meant. The tests catch specific known mistakes on known data; they cannot prove that every question is translated correctly. A real case is kept in the examples: asked for one student's grade and the class average, a live run averaged over rows already narrowed to that student and answered 95.0 for both, with status `answered`. The known-answer test caught it and the trace shows the faulty SQL ([examples/README.md](examples/README.md), "A wrong answer that looked right"). A prompt rule added afterwards made 20 of 20 repeat runs correct, against 19 of 20 before; that lowers the rate on a small sample and does not remove the risk.
- A filter on a value written differently from the data, for example a semester format that is not among the sample rows, can produce a count of zero that looks like a fact. The trace shows the filter that was used; nothing detects the mismatch automatically.
- The final sentence is written by a model and can misstate the rows. The live tests check the sentence for every expected value on the known questions. For any other question, the trace is where you compare the rows with the sentence.
- When the agent asks back, the suggested questions are written by the model. The prompt tells it to offer concrete values only for a detail the person decides (a cutoff, a period) and never to supply facts the database lacks, and a live test checks this on one question: asked for each teacher's email address, with no email column, it must say the addresses are not stored and offer only what is. Before that rule, a live run offered "…if the format is first.last@example.com?" and choosing it returned addresses built from the invented rule. A prompt rule lowers that risk; it does not remove it, so read a suggested question before choosing it.
- The start screen lists every model Ollama has installed, including ones that cannot chat (an embedding model, for example). Choosing one ends the first question with "Run stopped at generate_sql: …".
- The schema reader fails on a database that has a table from a SQLite extension that is not loaded, or text that is not valid UTF-8. The run then ends as `error` at `load_schema`. This was found in a code review and not fixed.
- Passing the live questions on one model says nothing certain about other models, other phrasings or repeated runs.
- Text stored in the database is sent to the model, in the sample rows and in the result rows. A database containing hostile text could steer the model. Read-only access protects the data, not the truth of the answer. This is not defended against here; see [docs/production.md](docs/production.md).
- One query per question: a question that needs the result of one query to write the next is answered only if it fits in a single statement with subqueries.
- The SQL check rejects any `;` inside the query, even inside a quoted string.
- SQLite only, and no memory between questions.
- The web interface is for one person on one computer: there is no login, and the chosen model is shared by every page open on the server. An answer appears only when its run has finished. The page itself has no automated browser tests; the tests check the server and the text of the page.

## Layout

```
agent/
  cli.py            ask a question, follow up a question back, print a trace
  db.py             open read-only, read the schema, check the SQL, run it with row cap and time limit
  graph.py          the state, the five nodes and their edges
  prompts.py        the two system prompts
  server.py         local web server: the page, setup, questions, saved runs
  trace.py          record, save, load and print a run
  ui.html           the page: setup screen, chat and trace panel
db/
  schema.sql        university schema
  seed.sql          university seed data
  library_schema.sql  library schema: books, members, loans
  library_seed.sql  library seed data
  build.py          builds a SQLite file from a schema and a seed file
  university_questions.txt  example questions for the web interface
  library_questions.txt     example questions for the library database
tests/
  conftest.py       fresh databases for the tests
  fakes.py          the scripted fake model
  known_answer.py   test-only checks of rows and answer text against known values
  test_*.py         offline tests; test_live.py holds the live ones
examples/
  make_examples.py  runs the example questions, writes the index, README and traces
  examples.json     index of the example runs
  README.md         each example with its SQL, answer, status and trace link
  traces/           the saved traces
docs/
  production.md     what production would need
  design/           the two static page designs the web interface follows
  superpowers/      design spec and milestone plans
  grills/           decision record from the design session
.env.example        model settings to copy into .env
requirements.txt    Python packages
pyproject.toml      pytest settings
```
