import pytest

from db.build import ROOT, build


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "university.db"
    build(ROOT / "db/schema.sql", ROOT / "db/seed.sql", path)
    return str(path)
