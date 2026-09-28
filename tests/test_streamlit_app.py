"""UI smoke test: every page renders and UI actions go through the workflow controls."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_all_pages_render_and_controls_hold(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_DATABASE_URL", f"sqlite:///{tmp_path / 'ui.db'}")
    at = AppTest.from_file(str(Path(__file__).parent.parent / "streamlit_app.py"), default_timeout=60).run()
    # A fresh DB is loaded with the 20 demo invoices on first run.
    at.sidebar.button[0].click().run()  # "Reset demo data" -> deterministic state
    assert not at.exception
    assert at.metric[0].value == "20"

    for page in ["Invoice Processing", "Exception Queue", "Audit Trail", "Architecture", "Dashboard"]:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page

    at.sidebar.radio[0].set_value("Invoice Processing").run()
    next(s for s in at.selectbox if s.label == "Invoice").set_value("AP-0007").run()  # duplicate
    next(b for b in at.button if b.label == "Post").click().run()
    assert any("POSTING BLOCKED" in e.value for e in at.error)
