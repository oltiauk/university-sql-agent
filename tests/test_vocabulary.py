from db.build import ROOT

BANNED = ("student", "teacher", "course", "enrollment", "grade", "semester")

def test_agent_code_has_no_schema_words():
    for path in (ROOT / "agent").glob("*.py"):
        text = path.read_text().lower()
        for word in BANNED:
            assert word not in text, f"{path.name} contains {word!r}"
