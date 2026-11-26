# Production notes

What it would take to run this agent as a production service rather than a command-line homework. Each section says what the code already does, what is missing, and what to add first.

## Reliability

**Already there.** A failed query gets exactly one retry with its error fed back. A query is stopped after 5 seconds and results are capped at 100 rows (`agent/db.py`). Results that cannot support an answer (no rows, the wrong shape for a single value, an all-NULL row) end the run with a fixed message instead of reaching the model (`check_result` in `agent/graph.py`). Any exception inside the graph, including Ctrl-C, ends the run as `error` with a saved trace (`agent/trace.py`). Exit codes separate a normal end, a failed run and a usage problem.

**Missing.** The code sets no timeout and no retry policy of its own on the model call; it relies on whatever the provider package does by default. If a model cannot return the structured reply, the run ends as `error`, with no fallback. `answered` is not a verified answer: the only checks of correctness are the known-answer tests, which run on a fixed database and, for the live ones, only when someone runs them.

**First.** Set an explicit timeout and a small number of retries with backoff on both model calls, and treat a timeout like any other step failure. Turn the live known-answer tests into a gate that runs against the chosen model before any prompt, model or provider change is released, and grow that question set from real questions whose answers have been checked by hand.

## Scalability

**Already there.** The graph keeps no state between questions, so separate questions do not depend on each other. The two nodes that touch the database (`load_schema` and `execute_sql`) each open their own read-only SQLite connection and close it when done.

**Missing.** The schema text is read from the database on every question, and the whole of it (every table, with sample rows) goes into every SQL prompt. That works for a handful of tables; a database with hundreds would exceed the model's context and raise cost on every call. SQLite on a local file does not serve many users on many machines. There is no service interface: the only entry point is the command line, one process per question.

**First.** Cache the schema text per database and rebuild it when the schema changes. Put the graph behind a small service so one process handles many requests. For a large schema, choose the relevant tables before writing SQL (for example by matching the question against table and column names, or a short list curated per database), and send only those.

## Monitoring and tracing

**Already there.** Every run writes one JSON file with the question, the database, the provider and model name, each node's output in order, per-step durations, the status and the answer (`agent/trace.py`). A failed step is recorded with only its error class name. A test checks that a key never reaches a trace or printed output (`tests/test_key_safety.py`). `--trace <run id>` prints any run.

**Missing.** Traces are local files with no retention: they grow without limit, and nothing aggregates them. They store the full result rows and sample rows, which on a real database can include personal data, so they need the same protection as the data itself. Token counts and cost are not recorded. There is no count of runs by status, no latency figure across runs, and no alert when, for example, the share of `failed` or `error` runs rises. There is no way for a user to mark an answer as wrong.

**First.** Send each trace record to a central store with access control, a retention period and redaction of row values for fields marked sensitive. Record token counts per model call. Track runs by status, retry rate and latency per node, and alert on changes. Add a "this answer is wrong" signal tied to the run id, so a reported answer leads straight to its trace and can become a new known-answer test.

## Security

**Already there.** Read-only at two layers: the SQL check accepts only one statement starting with `SELECT` or `WITH`, and the SQLite file is opened in read-only mode, which stops a write hidden behind `WITH`. The 5-second limit and the 100-row cap bound what one query can cost. The key is read from the environment and passed only to the model constructor; it is never put in the graph state. Provider error text is not stored, since it can carry request details. `--trace` accepts only the run id format, so it cannot be pointed at other files.

**Missing.**
- On a server database, read-only file access has no equivalent. The connection would need a database role that can only read, and a statement timeout set in the database itself.
- Anyone who can ask can read every table the connection can read. There are no users and no per-user limits on which rows or tables they may see.
- Text stored in the database reaches the model, in the sample rows and in the result rows. If that text comes from untrusted people, it is a prompt-injection path: hostile text could steer the SQL that is written or the sentence that is shown. Read-only access protects the data, not the truth of the answer.
- Traces hold result rows; whoever can read the traces can read that data.
- The key lives in a local `.env` file.
- There is no limit on question length or request rate.

**First.** Connect through a dedicated read-only role that sees only views made for this purpose, with sensitive columns left out, and set a statement timeout on that role. Add authentication and scope what each user may query. Tell the model in the prompts that database text is data, not instructions; this reduces the prompt-injection risk but does not remove it, so the trace stays the place to audit a suspicious answer. Store the key in a secret manager, and limit question length and requests per user.

## Deployment

**Already there.** `requirements.txt` lists the packages, `.env.example` lists the settings, and `db/build.py` rebuilds a database from its schema and seed files. The default test run needs no network and no key, so it can run anywhere.

**Missing.** `requirements.txt` pins no versions, so two installs can get different library versions. The test settings turn warnings into errors (`pyproject.toml`), so a new library version that adds a deprecation warning fails the test run. There is no container image, no continuous integration, and no service to deploy; the agent is a command-line program.

**First.** Pin exact versions with a lock file. Run the offline tests on every change in continuous integration, and the live known-answer tests before each release against the model that will be used. Package the service and its pinned dependencies as one image, with the model settings and key supplied by the environment it runs in.
