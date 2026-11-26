import sqlite3
import time
from pathlib import Path

MAX_ROWS = 100
TIMEOUT_SECONDS = 5


def connect(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)


def get_schema(conn: sqlite3.Connection) -> str:
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY rowid"
    ).fetchall()
    parts = []
    for name, sql in tables:
        quoted = name.replace('"', '""')
        rows = conn.execute(f'SELECT * FROM "{quoted}" LIMIT 3').fetchall()
        # cut each value to 100 chars so one huge value cannot flood the schema text
        parts.append("\n".join([sql] + ["(" + ", ".join(repr(v)[:100] for v in r) + ")" for r in rows]))
    return "\n\n".join(parts)


def check_sql(sql: str) -> None:
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    first = s.split(None, 1)[0].upper() if s else ""
    # a write hidden behind WITH (WITH x AS (SELECT 1) DELETE ...) passes here; the read-only connection stops it
    # ponytail: any ';' is rejected, even inside a quoted string; real SQL tokenizer if that bites
    if ";" in s or first not in ("SELECT", "WITH"):
        raise ValueError("Only a single SELECT statement is allowed.")


def run_query(conn: sqlite3.Connection, sql: str) -> tuple[list[str], list[tuple], bool]:
    check_sql(sql)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    cur = conn.execute(sql)
    rows = cur.fetchmany(MAX_ROWS + 1)
    return [d[0] for d in cur.description], rows[:MAX_ROWS], len(rows) > MAX_ROWS
