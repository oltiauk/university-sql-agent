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

Run id: `20261126-060948-2af9a3`, trace: [traces/20261126-060948-2af9a3.json](traces/20261126-060948-2af9a3.json)

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

Run id: `20261126-060948-eab3d7`, trace: [traces/20261126-060948-eab3d7.json](traces/20261126-060948-eab3d7.json)

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

Run id: `20261126-060948-c6afbd`, trace: [traces/20261126-060948-c6afbd.json](traces/20261126-060948-c6afbd.json)

## Aggregation

Command: `python -m agent.cli --db university.db "What is the average grade in Databases in Fall 2025?"`

Question: What is the average grade in Databases in Fall 2025?

Status: answered

Answer: The average grade in Databases in Fall 2025 is 78.0.

SQL that ran:

```sql
SELECT AVG(e.grade) AS average_grade
FROM enrollments e
JOIN offerings o ON o.id = e.offering_id
JOIN courses c ON c.id = o.course_id
WHERE c.title = 'Databases'
  AND o.semester = '2025-Fall'
```

Run id: `20261126-060948-699e3c`, trace: [traces/20261126-060948-699e3c.json](traces/20261126-060948-699e3c.json)

## Join across four tables, with a subquery

Command: `python -m agent.cli --db university.db "Which students scored above the class average in Databases in Fall 2025?"`

Question: Which students scored above the class average in Databases in Fall 2025?

Status: answered

Answer: Arta, Blerim, and Dren scored above the class average in Databases in Fall 2025.

SQL that ran:

```sql
SELECT s.name AS student_name
FROM students AS s
JOIN enrollments AS e ON e.student_id = s.id
JOIN offerings AS o ON o.id = e.offering_id
JOIN courses AS c ON c.id = o.course_id
WHERE c.title = 'Databases'
  AND o.semester = '2025-Fall'
  AND e.grade > (
      SELECT AVG(e2.grade)
      FROM enrollments AS e2
      JOIN offerings AS o2 ON o2.id = e2.offering_id
      JOIN courses AS c2 ON c2.id = o2.course_id
      WHERE c2.title = 'Databases'
        AND o2.semester = '2025-Fall'
  )
```

Run id: `20261126-060954-6847bb`, trace: [traces/20261126-060954-6847bb.json](traces/20261126-060954-6847bb.json)

## Filter by semester

Command: `python -m agent.cli --db university.db "How many enrolments are there in Spring 2026?"`

Question: How many enrolments are there in Spring 2026?

Status: answered

Answer: There are 7 enrolments in Spring 2026.

SQL that ran:

```sql
SELECT COUNT(*) AS enrollment_count
FROM enrollments AS e
JOIN offerings AS o ON o.id = e.offering_id
WHERE o.semester = '2026-Spring';
```

Run id: `20261126-060958-01b07e`, trace: [traces/20261126-060958-01b07e.json](traces/20261126-060958-01b07e.json)

## Filter by teacher

Command: `python -m agent.cli --db university.db "What is the average grade in the classes taught by Besnik Krasniqi?"`

Question: What is the average grade in the classes taught by Besnik Krasniqi?

Status: answered

Answer: The average grade in classes taught by Besnik Krasniqi is 91.33333333333333.

SQL that ran:

```sql
SELECT AVG(e.grade) AS average_grade
FROM enrollments AS e
JOIN offerings AS o ON e.offering_id = o.id
JOIN teachers AS t ON o.teacher_id = t.id
WHERE t.name = 'Besnik Krasniqi'
```

Run id: `20261126-061001-1967aa`, trace: [traces/20261126-061001-1967aa.json](traces/20261126-061001-1967aa.json)

## Filter by student

Command: `python -m agent.cli --db university.db "Which courses did Arta take, and what grade did she get in each?"`

Question: Which courses did Arta take, and what grade did she get in each?

Status: answered

Answer: Arta took CS101 Databases and got 95.0, CS102 Algorithms and got 98.0, and CS201 Machine Learning and got 99.0.

SQL that ran:

```sql
SELECT c.code AS course_code, c.title AS course_title, e.grade AS grade
FROM students AS s
JOIN enrollments AS e ON e.student_id = s.id
JOIN offerings AS o ON o.id = e.offering_id
JOIN courses AS c ON c.id = o.course_id
WHERE s.name = 'Arta'
```

Run id: `20261126-061005-228166`, trace: [traces/20261126-061005-228166.json](traces/20261126-061005-228166.json)

## An unclear question

Command: `python -m agent.cli --db university.db "How many students passed Databases in Fall 2025?"`

Question: How many students passed Databases in Fall 2025?

Status: clarification

Answer: What minimum grade counts as passing?

Run id: `20261126-061008-05949d`, trace: [traces/20261126-061008-05949d.json](traces/20261126-061008-05949d.json)

## Nothing to average

Command: `python -m agent.cli --db university.db "What is the average grade in Databases in Fall 2030?"`

Question: What is the average grade in Databases in Fall 2030?

Status: no_value

Answer: No value is available for average_grade.

SQL that ran:

```sql
SELECT AVG(e.grade) AS average_grade
FROM courses AS c
JOIN offerings AS o ON o.course_id = c.id
JOIN enrollments AS e ON e.offering_id = o.id
WHERE c.title = 'Databases'
  AND o.semester = '2030-Fall';
```

Run id: `20261126-061010-2a482a`, trace: [traces/20261126-061010-2a482a.json](traces/20261126-061010-2a482a.json)

## A different database, same agent

Command: `python -m agent.cli --db library.db "Which books are currently on loan?"`

Question: Which books are currently on loan?

Status: answered

Answer: The books currently on loan are Emma, Dune, and Dracula.

SQL that ran:

```sql
SELECT DISTINCT b.title AS book_title
FROM books AS b
JOIN loans AS l ON l.book_id = b.id
WHERE l.returned_on IS NULL
```

Run id: `20261126-061012-dd48e6`, trace: [traces/20261126-061012-dd48e6.json](traces/20261126-061012-dd48e6.json)
