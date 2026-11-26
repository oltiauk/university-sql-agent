import sqlite3


def connect(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def get_schema(conn: sqlite3.Connection) -> str:
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY rowid"
    ).fetchall()
    parts = []
    for name, sql in tables:
        cur = conn.execute(f'SELECT * FROM "{name}" LIMIT 3')
        cols = [d[0] for d in cur.description]
        lines = [sql, "columns: " + ", ".join(cols)] + [repr(r) for r in cur.fetchall()]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def check_sql(sql: str) -> None:
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    first = s.split(None, 1)[0].upper() if s else ""
    # ponytail: any ';' is rejected, even inside a quoted string; real SQL tokenizer if that bites
    if ";" in s or first not in ("SELECT", "WITH"):
        raise ValueError("Only a single SELECT statement is allowed.")


def run_query(conn: sqlite3.Connection, sql: str, max_rows: int = 100) -> tuple[list[str], list[tuple], bool]:
    check_sql(sql)
    cur = conn.execute(sql)
    rows = cur.fetchmany(max_rows + 1)
    return [d[0] for d in cur.description], rows[:max_rows], len(rows) > max_rows
