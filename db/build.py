import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).parent.parent


def build(schema_path, seed_path, db_path):
    Path(db_path).unlink(missing_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        for path in (schema_path, seed_path):
            conn.executescript(Path(path).read_text())


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) not in (0, 3):
        sys.exit("usage: build.py [<schema> <seed> <out>]")
    build(*(args or [ROOT / "db/schema.sql", ROOT / "db/seed.sql", ROOT / "university.db"]))
