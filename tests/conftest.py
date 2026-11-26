import pytest

from db.build import ROOT, build


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "university.db"
    build(ROOT / "db/schema.sql", ROOT / "db/seed.sql", path)
    return str(path)


@pytest.fixture
def library_path(tmp_path):
    path = tmp_path / "library.db"
    build(ROOT / "db/library_schema.sql", ROOT / "db/library_seed.sql", path)
    return str(path)
