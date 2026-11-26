import json
import re

import pytest

import examples.make_examples
from db.build import ROOT
from agent.cli import main
from examples.make_examples import KEPT, make
from tests.fakes import FakeModel


def index(tmp_path):
    return json.loads((tmp_path / "examples.json").read_text())

def test_scripted_examples_are_reproducible(tmp_path):
    first = make(live=False, out_dir=str(tmp_path))
    second = make(live=False, out_dir=str(tmp_path))
    assert [e["id"] for e in first] == ["scripted-retry", "scripted-failed", "scripted-mismatch"]
    assert [e["status"] for e in second] == ["answered", "failed", "mismatch"]
    files = sorted(p.stem for p in (tmp_path / "traces").glob("*.json"))
    assert files == sorted(e["run_id"] for e in second)          # the first run's three traces were replaced
    assert {e["run_id"] for e in first}.isdisjoint(files)

def test_example_traces_open_by_run_id(tmp_path, capsys):
    entries = make(live=False, out_dir=str(tmp_path))
    code = main(["--trace", entries[0]["run_id"], "--traces", str(tmp_path / "traces")])
    assert code == 0 and "generate_sql" in capsys.readouterr().out

def test_scripted_traces_show_what_they_claim(tmp_path):
    make(live=False, out_dir=str(tmp_path))
    load = lambda name: json.loads((tmp_path / "traces" / f"{index(tmp_path)[name]['run_id']}.json").read_text())
    retry = load("scripted-retry")
    assert [s["node"] for s in retry["steps"]].count("generate_sql") == 2 and retry["status"] == "answered"
    assert load("scripted-failed")["answer"].startswith("The question could not be answered.")
    assert load("scripted-mismatch")["answer"] == "The question asks for a single value, but the query returned 5 rows."

def test_readme_lists_every_example_and_labels_scripted_ones(tmp_path):
    make(live=False, out_dir=str(tmp_path))
    text = (tmp_path / "README.md").read_text()
    for entry in index(tmp_path).values():
        assert f"traces/{entry['run_id']}.json" in text and (tmp_path / "traces" / f"{entry['run_id']}.json").exists()
    assert text.count("Scripted: produced with a fake model") == 3
    assert "--trace" in text and "--traces examples/traces" in text      # how to open one by its run id

def test_live_examples_run_with_an_injected_model_and_leave_no_absolute_path(tmp_path):
    entries = make(live=True, model=FakeModel([{"sql": "SELECT 1 AS one"}, "one"] * 8), out_dir=str(tmp_path))
    assert len(entries) == 11 and len(list((tmp_path / "traces").glob("*.json"))) == 11
    library = index(tmp_path)["library"]
    assert json.loads((tmp_path / "traces" / f"{library['run_id']}.json").read_text())["database"] == "library.db"
    for path in list((tmp_path / "traces").glob("*.json")) + [tmp_path / "README.md", tmp_path / "examples.json"]:
        text = path.read_text()
        assert "/Users/" not in text and str(tmp_path) not in text and str(ROOT) not in text

def test_index_stays_consistent_when_a_run_raises(tmp_path, monkeypatch):
    make(live=False, out_dir=str(tmp_path))
    real, calls = examples.make_examples.run_traced, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(examples.make_examples, "run_traced", flaky)
    with pytest.raises(OSError):
        make(live=False, out_dir=str(tmp_path))
    for entry in index(tmp_path).values():
        assert (tmp_path / "traces" / f"{entry['run_id']}.json").exists()
    for link in re.findall(r"\]\((traces/[^)]+\.json)\)", (tmp_path / "README.md").read_text()):
        assert (tmp_path / link).exists()

def test_error_run_keeps_the_previous_example(tmp_path):
    make(live=True, model=FakeModel([{"sql": "SELECT 1 AS one"}, "one"] * 8), out_dir=str(tmp_path))
    before = index(tmp_path)["aggregate"]
    with pytest.raises(SystemExit):
        make(live=True, model=FakeModel([RuntimeError("down")]), out_dir=str(tmp_path))
    assert index(tmp_path)["aggregate"] == before
    assert (tmp_path / "traces" / f"{before['run_id']}.json").exists()
    assert len(list((tmp_path / "traces").glob("*.json"))) == 11


def test_kept_wrong_answer_is_listed_and_never_replaced(tmp_path):
    kept = ROOT / "examples" / "traces" / f"{KEPT}.json"
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces" / kept.name).write_text(kept.read_text())
    make(live=False, out_dir=str(tmp_path))
    make(live=False, out_dir=str(tmp_path))
    text = (tmp_path / "README.md").read_text()
    assert (tmp_path / "traces" / kept.name).read_text() == kept.read_text()      # untouched by regeneration
    assert f"traces/{KEPT}.json" in text and "A wrong answer that looked right" in text and "95.0" in text
    assert "/Users/" not in kept.read_text() and json.loads(kept.read_text())["database"] == "university.db"
