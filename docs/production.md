# Production notes

What it would take to run this agent as a production service rather than a command-line homework. Each section says what the code already does, then lists each gap and what to add, in the order you would do it.

## Reliability

**Already there.** A failed query gets one retry with its error fed back; a query stops after 5 seconds and results are capped at 100 rows. Results that cannot support an answer end with a fixed message, and any exception, including Ctrl-C, ends the run as `error` with a saved trace.

- The code sets no timeout or retry policy of its own on the model calls; it relies on the provider package's defaults. Add an explicit timeout and a few retries with backoff, and treat a timeout like any other step failure.
- `answered` is not a verified answer, and the live known-answer tests run only when someone runs them. Grow that question set from real questions whose answers were checked by hand, and run it before every release (see Deployment).

## Scalability

**Already there.** The graph keeps no state between questions, so questions do not depend on each other.

- The schema is read on every question and all of it goes into every SQL prompt. Cache it per database and rebuild it when the schema changes.
- A database with hundreds of tables would exceed the model's context. Choose the relevant tables first (by matching the question against table and column names, or a list kept per database) and send only those.
- The only entry point is the command line, one process per question, on a local SQLite file. Put the graph behind a small service and move to a server database (see Security for its access).

## Monitoring and tracing

**Already there.** Every run writes one JSON file with the question, model, each node's output and duration, the status and the answer; a failed step keeps only its error class name.

- Traces are local files with no retention, and they hold full result rows and sample rows, which on a real database can include personal data. Send them to a central store with access control, a retention period and redaction of sensitive fields.
- Nothing aggregates runs. Track runs by status, retry rate and latency per node, and alert when the share of `failed` or `error` runs rises.
- Token counts and cost are not recorded. Record them per model call.
- A user cannot report a wrong answer. Add a report tied to the run id, so it leads to the trace and can become a new known-answer test.

## Security

**Already there.** Read-only at two layers: the SQL check accepts one `SELECT` or `WITH` statement, and the SQLite file is opened read-only. Our code never reads the API key; the provider package reads it from the environment, so it never enters graph state or a trace.

- A server database has no read-only file mode. Connect through a role that can only read, sees only views made for this purpose without sensitive columns, and has a statement timeout set in the database.
- Anyone who can ask can read every table the connection can read. Add authentication and scope what each user may query and which traces they may read.
- Text stored in the database reaches the model in the sample rows and result rows; if untrusted people can write it, it is a prompt-injection path that can steer the SQL or the sentence. Tell the model that database text is data, not instructions; this reduces the risk but does not remove it, so the trace stays the place to audit a suspicious answer.
- The key lives in a local `.env` file. Move it to a secret manager.
- There is no limit on question length or request rate. Add both, per user.

## Deployment

**Already there.** `requirements.txt` and `.env.example` list the packages and settings, `db/build.py` rebuilds a database from its SQL files, and the offline tests need no network or key.

- `requirements.txt` pins no versions, and `pyproject.toml` turns warnings into test errors, so a new library version can break the tests. Pin exact versions with a lock file.
- There is no continuous integration. Run the offline tests on every change, and the live known-answer tests before each release against the model that will be used.
- There is no deployable unit. Package the service and its pinned dependencies as one image, with the model settings and key supplied by its environment.
