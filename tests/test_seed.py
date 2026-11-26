import sqlite3
from contextlib import closing

import pytest

from db.build import ROOT, build


def rows(db_path, sql):
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(sql).fetchall()


CLASS = """FROM enrollments e
JOIN students s ON s.id = e.student_id
JOIN offerings o ON o.id = e.offering_id
JOIN courses c ON c.id = o.course_id
WHERE c.title = 'Databases' AND o.semester = '2025-Fall'"""

def test_five_tables(db_path):
    names = {r[0] for r in rows(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"teachers", "students", "courses", "offerings", "enrollments"}

def test_databases_fall_class(db_path):
    got = rows(db_path, f"SELECT s.name, e.grade {CLASS} ORDER BY e.grade DESC")
    assert got == [("Arta", 95), ("Blerim", 88), ("Dren", 82), ("Era", 70), ("Fatos", 55)]

def test_above_class_average(db_path):
    got = rows(db_path, f"""SELECT s.name {CLASS} AND e.grade > (
        SELECT AVG(e2.grade) FROM enrollments e2 WHERE e2.offering_id = o.id) ORDER BY s.name""")
    assert got == [("Arta",), ("Blerim",), ("Dren",)]

def test_overall_average_gives_a_different_answer(db_path):
    assert rows(db_path, "SELECT AVG(grade) FROM enrollments") == [(87.0,)]
    got = rows(db_path, f"SELECT s.name {CLASS} AND e.grade > (SELECT AVG(grade) FROM enrollments) ORDER BY s.name")
    assert got == [("Arta",), ("Blerim",)]

def test_people_and_enrollments_differ(db_path):
    got = rows(db_path, """SELECT COUNT(*), COUNT(DISTINCT e.student_id) FROM enrollments e
        JOIN offerings o ON o.id = e.offering_id JOIN teachers t ON t.id = o.teacher_id
        WHERE t.name = 'Besnik Krasniqi'""")
    assert got == [(6, 5)]

def test_average_by_teacher(db_path):
    got = dict(rows(db_path, """SELECT t.name, ROUND(AVG(e.grade), 1) FROM enrollments e
        JOIN offerings o ON o.id = e.offering_id JOIN teachers t ON t.id = o.teacher_id GROUP BY t.id"""))
    assert got == {"Lira Hoxha": 83.0, "Besnik Krasniqi": 91.3, "Mira Berisha": 89.0}

def test_semester_filter(db_path):
    got = rows(db_path, """SELECT COUNT(*) FROM enrollments e JOIN offerings o ON o.id = e.offering_id
        WHERE o.semester = '2026-Spring'""")
    assert got == [(7,)]

def test_totals(db_path):
    assert rows(db_path, "SELECT COUNT(*) FROM enrollments") == [(19,)]
    assert rows(db_path, "SELECT COUNT(*) FROM enrollments WHERE grade IS NULL") == [(2,)]
    assert rows(db_path, "SELECT COUNT(*), COUNT(DISTINCT name) FROM students") == [(8, 8)]

def test_constraints(db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        for bad in ("INSERT INTO students(name) VALUES (NULL)",
                    "INSERT INTO teachers(name) VALUES (NULL)",
                    "INSERT INTO enrollments(student_id, offering_id, grade) VALUES (1, 6, 101)",
                    "INSERT INTO enrollments(student_id, offering_id, grade) VALUES (1, 1, 50)"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(bad)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

def test_build_twice(db_path):
    build(ROOT / "db/schema.sql", ROOT / "db/seed.sql", db_path)
    assert rows(db_path, "SELECT COUNT(*) FROM students") == [(8,)]
