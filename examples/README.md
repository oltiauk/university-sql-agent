# Examples

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

## A query that fails and is corrected

Scripted: produced with a fake model, because a real model does not fail on demand.

Command: `python examples/make_examples.py`

Question: How many students are there?

Status: answered

Answer: There are 8 students.

SQL that ran:

```sql
SELECT COUNT(*) AS student_count FROM students
```

Run id: `20261126-060920-dd48ff`, trace: [traces/20261126-060920-dd48ff.json](traces/20261126-060920-dd48ff.json)

## A query that fails twice

Scripted: produced with a fake model, because a real model does not fail on demand.

Command: `python examples/make_examples.py`

Question: How many students are there?

Status: failed

Answer: The question could not be answered. Last error: no such table: pupils

SQL that ran:

```sql
SELECT COUNT(*) AS n FROM pupils
```

Run id: `20261126-060920-2c4bca`, trace: [traces/20261126-060920-2c4bca.json](traces/20261126-060920-2c4bca.json)

## One value asked for, five returned

Scripted: produced with a fake model, because a real model does not fail on demand.

Command: `python examples/make_examples.py`

Question: What is the average grade in Databases in Fall 2025?

Status: mismatch

Answer: The question asks for a single value, but the query returned 5 rows.

SQL that ran:

```sql
SELECT e.grade AS grade FROM enrollments e JOIN offerings o ON o.id = e.offering_id
JOIN courses c ON c.id = o.course_id WHERE c.title = 'Databases' AND o.semester = '2025-Fall'
```

Run id: `20261126-060920-5c1420`, trace: [traces/20261126-060920-5c1420.json](traces/20261126-060920-5c1420.json)
