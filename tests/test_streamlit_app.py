"""UI smoke tests: every page renders for every role, and UI actions go through the workflow controls."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parent.parent / "streamlit_app.py")
PAGES = ["Dashboard", "New Invoice", "Approval Inbox", "Invoice Processing", "Exception Queue",
         "Audit Trail", "Architecture"]
ROLES = ["Dana Brooks", "Priya Shah", "Marcus Lee", "Elena Torres"]


def fresh_app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setenv("AP_DATABASE_URL", f"sqlite:///{tmp_path / 'ui.db'}")
    monkeypatch.setenv("AP_LLM_PROVIDER", "mock")
    at = AppTest.from_file(APP, default_timeout=90).run()
    at.button(key="reset_demo").click().run()  # deterministic state (the resource cache is process-wide)
    assert not at.exception
    return at


def test_all_pages_render_for_every_role_and_controls_hold(tmp_path, monkeypatch):
    at = fresh_app(tmp_path, monkeypatch)
    assert at.metric[0].value == "20"
    for role in ROLES:
        at.selectbox(key="user").set_value(role).run()
        for page in PAGES:
            at.radio(key="page").set_value(page).run()
            assert not at.exception, (role, page)

    at.selectbox(key="user").set_value("Dana Brooks").run()
    at.radio(key="page").set_value("Invoice Processing").run()
    next(s for s in at.selectbox if s.label == "Invoice").set_value("AP-0007").run()  # duplicate
    at.button(key="post-AP-0007").click().run()
    assert any("POSTING BLOCKED" in e.value for e in at.error)


def test_invoice_travels_between_role_inboxes(tmp_path, monkeypatch):
    at = fresh_app(tmp_path, monkeypatch)
    at.selectbox(key="user").set_value("Priya Shah").run()
    at.radio(key="page").set_value("Approval Inbox").run()
    at.selectbox(key="inbox_sel").set_value("AP-0004").run()
    at.button(key="inbox-approve-AP-0004").click().run()
    assert any("now waiting for Department Manager" in s.value for s in at.success)

    at.selectbox(key="user").set_value("Marcus Lee").run()
    assert "AP-0004" in at.selectbox(key="inbox_sel").options[0]
    at.selectbox(key="inbox_sel").set_value("AP-0004").run()
    at.text_input(key="inbox-reason-AP-0004").input("Over budget").run()
    at.button(key="inbox-reject-AP-0004").click().run()
    assert any("AP-0004 is now REJECTED" in s.value for s in at.success)


def test_manual_entry_form_creates_invoice(tmp_path, monkeypatch):
    at = fresh_app(tmp_path, monkeypatch)
    at.radio(key="page").set_value("New Invoice").run()
    at.selectbox(key="ni_vendor_0").set_value("Acme Office Supplies").run()
    at.selectbox(key="ni_po_Acme Office Supplies_0").set_value("PO-5003").run()  # pre-fills PO lines
    at.text_input(key="ni_number_0").input("INV-7777").run()
    assert [m.value for m in at.metric][:3] == ["$500.00", "$0.00", "$500.00"]
    at.button(key="ni_submit_0").click().run()
    assert not at.exception
    assert at.radio(key="page").value == "Invoice Processing"
    assert any("AP-0021 created" in s.value for s in at.success)
