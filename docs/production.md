# Production notes

The agent runs locally through a command line or a web UI. These notes describe what it would take to serve multiple users in production.

## Reliability

**Already there.** A failed query gets one retry with its error fed back; a query stops after 5 seconds and results are capped at 100 rows. Results that cannot support an answer end with a fixed message, and any exception, including Ctrl-C, ends the run as `error` with a saved trace.

- The code sets no timeout or retry policy of its own on the model calls; it relies on the provider package's defaults. Add an explicit timeout and a few retries with backoff, and treat a timeout like any other step failure.
- `answered` is not a verified answer, and the live known-answer tests run only when someone runs them. Grow that question set from real questions whose answers were checked by hand, and run it before every release (see Deployment).

## Scalability

**Already there.** The graph keeps no state between questions, so questions do not depend on each other.

- The schema is read on every question and all of it goes into every SQL prompt. Cache it per database and rebuild it when the schema changes.
- A database with hundreds of tables would exceed the model's context. Choose the relevant tables first (by matching the question against table and column names, or a list kept per database) and send only those.
- The CLI and the local web server both query a SQLite file. The web server shares one configured model across requests. For a multi-user service, isolate model settings per user and move query workloads to a server database when needed.

## Monitoring and tracing

**Already there.** Every run writes one JSON file with the question, model, each node's output and duration, the status and the answer; a failed step keeps only its error class name.

- Traces are local files with no retention, and they hold full result rows and sample rows, which on a real database can include personal data. Send them to a central store with access control, a retention period and redaction of sensitive fields.
- Nothing aggregates runs. Track runs by status, retry rate and latency per node, and alert when the share of `failed` or `error` runs rises.
- Token counts and cost are not recorded. Record them per model call.
- A user cannot report a wrong answer. Add a report tied to the run id, so it leads to the trace and can become a new known-answer test.

## Security

**Already there.** The SQL check accepts one `SELECT` or `WITH` statement, and the agent opens the SQLite file read-only. The web server listens on the loopback interface. Providers can use an environment key. If a user types a key during web setup, the server passes it to the model client in memory and keeps it out of graph state and traces.

- A server database has no read-only file mode. Connect through a role that can only read, sees only views made for this purpose without sensitive columns, and has a statement timeout set in the database.
- Anyone who can ask can read every table the connection can read. Add authentication and scope what each user may query and which traces they may read.
- Text stored in the database reaches the model in the sample rows and result rows; if untrusted people can write it, it is a prompt-injection path that can steer the SQL or the sentence. Tell the model that database text is data, not instructions; this reduces the risk but does not remove it, so the trace stays the place to audit a suspicious answer.
- Store deployment credentials in a secret manager. Local development supports an environment variable, a `.env` file or a key entered in web setup.
- The web server caps each question at 2,000 characters, including the combined text of a clarification. The CLI has no corresponding cap. Add per-user request rate limits before exposing the service to multiple users.

## Deployment

**Already there.** `requirements.txt` and `.env.example` list the packages and settings, `db/build.py` rebuilds a database from its SQL files, and the offline tests need no network or key.

- `requirements.txt` pins no versions, and `pyproject.toml` turns warnings into test errors, so a new library version can break the tests. Pin exact versions with a lock file.
- There is no continuous integration. Run the offline tests on every change, and the live known-answer tests before each release against the model that will be used.
- There is no deployable unit. Package the service and its pinned dependencies as one image, with the model settings and key supplied by its environment.
