import sqlite3
import time
from contextlib import closing

import pytest

from db.build import ROOT, build
from agent.db import check_sql, connect, get_schema, run_query


@pytest.fixture
def conn(db_path):
    with closing(connect(db_path)) as c:
        yield c


def test_connection_is_read_only(conn):
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM students")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("WITH x AS (SELECT 1) DELETE FROM students")


def test_connect_missing_file(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        connect(str(missing)).execute("SELECT 1")  # connect itself fails, nothing to close
    assert not missing.exists()


def test_schema_lists_tables_and_sample_rows(conn):
    schema = get_schema(conn)
    for name in ("teachers", "students", "courses", "offerings", "enrollments"):
        assert f"CREATE TABLE {name}" in schema
    assert "2025-Fall" in schema          # value format visible to the model
    assert "Arta" in schema and "Era" not in schema   # first 3 rows only
    assert "sqlite_" not in schema


def test_schema_odd_tables(tmp_path):
    path = tmp_path / "odd.db"
    rw = sqlite3.connect(path)
    rw.executescript('CREATE TABLE empty_one (a TEXT); CREATE TABLE "order" (x INT); INSERT INTO "order" VALUES (7); CREATE TABLE "a""b" (x INT); INSERT INTO "a""b" VALUES (9); CREATE VIEW v AS SELECT x FROM "order";')
    rw.close()
    with closing(connect(str(path))) as c:
        schema = get_schema(c)
    assert "CREATE TABLE empty_one" in schema and 'CREATE TABLE "order"' in schema and "7" in schema
    assert 'CREATE TABLE "a""b"' in schema and "CREATE VIEW v" in schema


@pytest.mark.parametrize("sql", ["SELECT 1", "select 1;", "  SELECT 1  \n", "WITH x AS (SELECT 1) SELECT * FROM x"])
def test_guard_accepts(sql):
    check_sql(sql)


@pytest.mark.parametrize("sql", [
    "", "INSERT INTO t VALUES (1)", "DROP TABLE t", "UPDATE t SET a = 1", "PRAGMA table_info(t)",
    "SELECT 1; SELECT 2", "SELECT 1; DROP TABLE t",
    "```sql\nSELECT 1\n```", "-- note\nSELECT 1",
])
def test_guard_rejects(sql):
    with pytest.raises(ValueError):
        check_sql(sql)


def test_run_query(conn):
    cols, rows, truncated = run_query(conn, "SELECT id, name FROM students ORDER BY id LIMIT 2;")
    assert (cols, rows, truncated) == (["id", "name"], [(1, "Arta"), (2, "Blerim")], False)


def test_run_query_guards_first(conn):
    with pytest.raises(ValueError):
        run_query(conn, "DELETE FROM students")


def test_run_query_bad_sql_raises_db_error(conn):
    with pytest.raises(sqlite3.Error):
        run_query(conn, "SELECT nope FROM students")


def test_row_cap(tmp_path):
    path = tmp_path / "many.db"
    rw = sqlite3.connect(path)
    rw.execute("CREATE TABLE n (i INT)")
    rw.executemany("INSERT INTO n VALUES (?)", [(i,) for i in range(101)])
    rw.commit(); rw.close()
    with closing(connect(str(path))) as conn:
        _, rows, truncated = run_query(conn, "SELECT i FROM n WHERE i < 100")
        assert (len(rows), truncated) == (100, False)
        _, rows, truncated = run_query(conn, "SELECT i FROM n")
        assert (len(rows), truncated) == (100, True)


def test_connect_odd_path(tmp_path):
    path = tmp_path / "a?b#c d%41.db"
    build(ROOT / "db/schema.sql", ROOT / "db/seed.sql", path)
    with closing(connect(str(path))) as c:
        assert c.execute("SELECT COUNT(*) FROM students").fetchall() == [(8,)]
        with pytest.raises(sqlite3.OperationalError):
            c.execute("DELETE FROM students")
    assert [p.name for p in tmp_path.iterdir()] == ["a?b#c d%41.db"]


def test_query_time_limit(conn, monkeypatch):
    monkeypatch.setattr("agent.db.TIMEOUT_SECONDS", 0.2)
    with pytest.raises(sqlite3.OperationalError):
        run_query(conn, "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT count(*) FROM n")
    assert run_query(conn, "SELECT 1")[1] == [(1,)]


def test_schema_truncates_long_values(tmp_path):
    path = tmp_path / "long.db"
    rw = sqlite3.connect(path)
    rw.execute("CREATE TABLE t (a TEXT)")
    rw.execute("INSERT INTO t VALUES (?)", ("x" * 10_000,))
    rw.commit(); rw.close()
    with closing(connect(str(path))) as c:
        assert len(get_schema(c)) < 1000


def test_time_limit_does_not_outlive_query(conn, monkeypatch):
    monkeypatch.setattr("agent.db.TIMEOUT_SECONDS", 0.05)
    run_query(conn, "SELECT 1")
    time.sleep(0.1)
    sql = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x < 200000) SELECT count(*) FROM n"
    assert conn.execute(sql).fetchall() == [(200000,)]
