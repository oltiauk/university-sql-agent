from db.build import ROOT

PAGE = (ROOT / "agent" / "ui.html").read_text()


def test_page_has_no_canned_data():
    for canned in ("Arta", "Blerim", "Databases", "7c41e0", "GRADES", "seeded", "anthropic ·"):
        assert canned not in PAGE


def test_page_keeps_the_design():
    design = (ROOT / "docs" / "design" / "sql-agent-ui.html").read_text()
    for token in ("#F7F9FC", "#1B2333", "border-radius:28px", ".bubble", ".orb", ".choice", ".own", ".trace-btn", ".steps", ".dot", ".rows", "JetBrains Mono"):
        assert token in design and token in PAGE


def test_setup_screen_is_in_the_page():
    for needed in ("/api/ask", "/api/state", "/api/runs", "/api/setup", "/api/ollama", 'type="password"', 'autocomplete="new-password"',
                   "Which AI should answer?", "Found in your environment",
                   "Ollama is not running on this computer", "Never saved", "Looking for Ollama"):
        assert needed in PAGE


def test_start_screen_keeps_its_design():
    design = (ROOT / "docs" / "design" / "start-screen.html").read_text()
    for token in (".switch", ".models", ".start", "max-width:380px", "width:64px;height:64px", "border-radius:16px", "#E3ECFA"):
        assert token in design and token in PAGE


def test_key_is_not_stored_by_the_page():
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "?key=", "&key="):
        assert forbidden not in PAGE
