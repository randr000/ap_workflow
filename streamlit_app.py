"""AI AP Automation Agent — Streamlit demo UI.

    streamlit run streamlit_app.py

The UI is a thin presentation layer: every button calls the workflow service,
which enforces the state machine, permissions and accounting validation. The
UI has no way to change invoice state directly.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation

import altair as alt
import pandas as pd
import streamlit as st

from app.ai.mock_provider import MockLLMProvider
from app.ai.ollama_provider import OllamaProvider
from app.ai.provider import PROVIDER_LABELS, create_provider
from app.config import DATA_DIR, load_settings
from app.domain.approval import DEMO_USERS, StepStatus, get_user
from app.domain.case import IntakeMethod, InvoiceCase
from app.domain.exception import ExceptionType, Severity
from app.domain.money import fmt_money, money
from app.domain.workflow import WorkflowState as S
from app.services.classification import confidence_band
from app.services.extraction import IntakeError, extraction_from_fields
from app.services.reporting import dashboard_metrics
from app.services.workflow_service import (
    APWorkflowService,
    PostingBlockedError,
    WorkflowError,
    create_workflow_service,
)
from demo.benchmark import run_benchmark
from demo.scenarios import run_demo_batch

st.set_page_config(page_title="AI AP Automation Agent", page_icon="🧾", layout="wide")

SERIES_COLOR = "#2a78d6"
PAGES = ["Dashboard", "New Invoice", "Approval Inbox", "Invoice Processing", "Exception Queue",
         "Audit Trail", "Architecture"]
STATUS_COLOR = {
    S.POSTED: "green", S.VALIDATED: "green", S.APPROVED: "blue", S.PENDING_APPROVAL: "orange",
    S.EXCEPTION: "red", S.REJECTED: "gray",
}
SEVERITY_ICON = {Severity.HIGH: "🔴", Severity.MEDIUM: "🟠", Severity.LOW: "🟡"}
HAPPY_PATH = [S.RECEIVED, S.EXTRACTED, S.CLASSIFIED, S.MATCHED, S.PENDING_APPROVAL, S.APPROVED, S.VALIDATED, S.POSTED]
INTAKE_LABEL = {
    IntakeMethod.AI_EXTRACTION: ("AI Extraction", "AI"),
    IntakeMethod.MANUAL_ENTRY: ("Invoice Details — entered manually", "HUMAN"),
    IntakeMethod.STRUCTURED_UPLOAD: ("Invoice Details — structured upload (JSON)", "HUMAN"),
}

settings = load_settings()


# --------------------------------------------------------------------------- services


@st.cache_resource
def get_repo():
    """Shared database. The 20 demo invoices are processed with the deterministic mock provider so the
    scripted demo is reproducible, whichever provider is selected for new invoices."""
    base = create_workflow_service(settings=settings, provider=MockLLMProvider())
    if not base.list():
        run_demo_batch(base)
    return base.repo


@st.cache_resource
def get_provider(kind: str, ollama_url: str, ollama_model: str):
    return create_provider(settings, kind, ollama_base_url=ollama_url, ollama_model=ollama_model)


@st.cache_data(ttl=60, show_spinner=False)
def ollama_status(url: str, model: str) -> tuple[bool, str, list[str]]:
    return OllamaProvider(url, model, timeout=5.0).check_connection()


def reset_demo() -> None:
    get_repo.clear()
    run_demo_batch(create_workflow_service(reset=True, settings=settings, provider=MockLLMProvider()))
    flash("success", "Demo data reset: 20 invoices re-processed from data/invoices/.")


# --------------------------------------------------------------------------- helpers


def md(text) -> str:
    """Escape '$' so Streamlit markdown doesn't render dollar amounts as LaTeX."""
    return str(text).replace("$", "\\$")


def flash(kind: str, message: str) -> None:
    st.session_state["flash"] = (kind, message)


def show_flash() -> None:
    if msg := st.session_state.pop("flash", None):
        kind, text = msg
        {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)(md(text))


def where_now(case: InvoiceCase) -> str:
    """Where the invoice is on its route after an action."""
    if case.status == S.PENDING_APPROVAL and case.approval_route.next_step:
        return f"now waiting for {case.approval_route.next_step.role}"
    if case.status == S.VALIDATED:
        return "fully approved and validated — ready to post"
    if case.status == S.APPROVED:
        return "approved, but posting is blocked by accounting validation"
    if case.status == S.POSTED:
        return f"posted to the ledger as {case.journal_entry.id}"
    return f"now {case.status}"


def run_action(label: str, fn, *args) -> None:
    """Call a workflow-service action; surface control failures instead of bypassing them."""
    try:
        case = fn(*args)
        flash("success", f"{label}: {case.id} is {where_now(case)}.")
    except PostingBlockedError as e:
        detail = ""
        if e.report and e.report.failures:
            detail = " Failed checks: " + "; ".join(f"{c.rule} ({c.detail})" for c in e.report.failures)
        flash("error", f"🚫 {e}{detail}")
    except WorkflowError as e:
        flash("error", f"🚫 {e}")
    st.rerun()


def open_invoice(invoice_id: str) -> None:
    st.session_state["selected_invoice"] = invoice_id
    st.session_state["nav_to"] = "Invoice Processing"  # applied before the nav widget renders


def go_to(page: str) -> None:
    st.session_state["nav_to"] = page


def status_badge(status: S) -> str:
    return f":{STATUS_COLOR.get(status, 'blue')}-badge[{status}]"


def acct_label(svc, number: str | None) -> str:
    acct = svc.reference.account(number)
    return acct.label if acct else f"{number or '—'} (unknown)"


def section(title: str, owner: str) -> None:
    tag = {"AI": ":violet-badge[AI proposal]", "DET": ":blue-badge[Deterministic]",
           "HUMAN": ":orange-badge[Human-in-the-loop]"}[owner]
    st.markdown(f"#### {title} {tag}")


def render_route(case: InvoiceCase) -> None:
    """The invoice's journey through approver roles: ✅ done · ▶ current · ○ still to come."""
    route = case.approval_route
    if route is None:
        return
    parts = [":gray[✓ Received]"]
    if not route.steps and route.auto_approval_eligible:
        parts.append(":green[⚡ Auto-approved by policy]")
    current = route.next_step if case.status == S.PENDING_APPROVAL else None
    for step in route.steps:
        if step.status == StepStatus.APPROVED:
            parts.append(f":green[✅ {step.role}]")
        elif step.status == StepStatus.REJECTED:
            parts.append(f"**:red-background[✖ {step.role}]**")
        elif current is not None and step.role == current.role:
            parts.append(f"**:orange-background[▶ {step.role}]**")
        else:
            parts.append(f":gray[○ {step.role}]")
    if case.status == S.POSTED:
        parts.append(":green[✅ Posted]")
    elif case.status == S.VALIDATED:
        parts.append("**:orange-background[▶ Post]**")
    elif case.status == S.REJECTED:
        if not any(s.status == StepStatus.REJECTED for s in route.steps):
            parts.append("**:red-background[✖ Rejected]**")
    else:
        parts.append(":gray[○ Post]")
    st.markdown(" → ".join(parts))
    decided = [s for s in route.steps if s.decided_by]
    if decided:
        st.caption(" · ".join(
            f"{s.role}: {s.status.lower()} by {s.decided_by}" + (f" — “{md(s.comment)}”" if s.comment else "")
            for s in decided))


# --------------------------------------------------------------------------- sidebar

if nav := st.session_state.pop("nav_to", None):
    st.session_state["page"] = nav

with st.sidebar:
    st.markdown("## 🧾 AI AP Automation Agent")
    st.caption("Proof of Concept")
    st.radio("Navigate", PAGES, key="page", label_visibility="collapsed")
    st.divider()

    user_name = st.selectbox("Your role", [u.name for u in DEMO_USERS],
                             format_func=lambda n: get_user(n).title, key="user")
    user = get_user(user_name)
    st.caption(f"Signed in as {user.name} · approves steps for: " + ", ".join(user.roles)
               + (" · can post" if user.can_post else ""))
    inbox_slot = st.empty()
    st.divider()

    kinds = ["mock", "ollama"] + (["openai"] if settings.openai_api_key else [])
    default_kind = settings.llm_provider if settings.llm_provider in kinds else "mock"
    kind = st.selectbox("AI provider", kinds, index=kinds.index(default_kind), format_func=PROVIDER_LABELS.get,
                        key="provider")
    ollama_url, ollama_model, ollama_ok = settings.ollama_base_url, settings.ollama_model, True
    if kind == "ollama":
        ollama_url = st.text_input("Ollama URL", value=settings.ollama_base_url, key="ollama_url",
                                   help="e.g. http://192.168.1.50:11434. On the Ollama machine, start Ollama with "
                                        "OLLAMA_HOST=0.0.0.0 so it accepts connections from the network.")
        ollama_model = st.text_input("Model", value=settings.ollama_model, key="ollama_model",
                                     help="Any model pulled on that machine, e.g. llama3.1, qwen2.5:7b.")
        ollama_ok, message, models = ollama_status(ollama_url.strip(), ollama_model.strip())
        st.caption(("🟢 " if ollama_ok else "🔴 ") + md(message))
        if models and not ollama_ok:
            st.caption("Installed models: " + ", ".join(models))
        st.button("Re-check connection", on_click=ollama_status.clear, width="stretch")
    provider = get_provider(kind, ollama_url.strip(), ollama_model.strip())
    st.caption(f"New invoices use **{provider.name}**. Demo data is always processed with the deterministic mock.")
    st.divider()
    st.caption("**Principle:** AI proposes and reasons. Deterministic application logic validates and controls.")
    if st.button("Reset demo data", width="stretch", key="reset_demo"):
        reset_demo()
        st.rerun()

svc = APWorkflowService(get_repo(), provider, settings)
cases = svc.list()
inbox = svc.pending_for(user)
inbox_slot.markdown(f"📥 **{len(inbox)}** invoice(s) waiting for you"
                    + (f" · **{len(svc.ready_to_post())}** ready to post" if user.can_post else ""))
show_flash()
page = st.session_state["page"]


# --------------------------------------------------------------------------- dashboard


def bar_chart(counts: dict[str, int], title: str) -> alt.Chart:
    df = pd.DataFrame({"label": list(counts), "count": list(counts.values())})
    base = alt.Chart(df, title=title).encode(
        y=alt.Y("label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220, ticks=False, domain=False)),
        x=alt.X("count:Q", title=None, axis=alt.Axis(grid=False, domain=False, ticks=False, labels=False)),
        tooltip=[alt.Tooltip("label:N", title="Type"), alt.Tooltip("count:Q", title="Count")],
    )
    bars = base.mark_bar(color=SERIES_COLOR, size=18, cornerRadiusEnd=4)
    labels = base.mark_text(align="left", dx=6).encode(text="count:Q")
    return (bars + labels).properties(height=max(120, 34 * len(df)))


def page_dashboard() -> None:
    st.title("AP Dashboard")
    st.caption(f"Harborview Holdings, Inc. · {len(cases)} invoices")
    m = dashboard_metrics(cases, svc.repo.list_ledger())

    c = st.columns(5)
    c[0].metric("Invoices Processed", m["invoices_processed"], border=True)
    c[1].metric("Straight-Through Rate", f"{m['straight_through_rate']:.0%}",
                help="Auto-approved and validated with no human touch.", border=True)
    c[2].metric("Open Exceptions", m["open_exceptions"], border=True)
    c[3].metric("Pending Approvals", m["pending_approvals"], border=True)
    c[4].metric("Posted Amount", fmt_money(m["posted_amount"]), help=f"{m['posted_count']} invoices posted",
                border=True)

    st.info("**AI proposes and reasons. Deterministic application logic validates and controls.** "
            "Extraction and GL coding are AI proposals; matching, tolerances, duplicates, approval routing, "
            "journal-entry validation and posting are deterministic code.", icon="🧭")

    left, right = st.columns(2)
    with left:
        if m["exceptions_detected"]:
            st.altair_chart(bar_chart(m["exceptions_detected"], "Exceptions detected by type"),
                            width="stretch")
            with st.expander("Table view"):
                st.dataframe(pd.DataFrame({
                    "Exception": list(m["exceptions_detected"]),
                    "Detected": list(m["exceptions_detected"].values()),
                    "Still open": [m["exceptions_open"].get(k, 0) for k in m["exceptions_detected"]],
                }), hide_index=True, width="stretch")
    with right:
        order = [s.value for s in S if s.value in m["status_counts"]]
        st.altair_chart(bar_chart({k: m["status_counts"][k] for k in order}, "Invoices by workflow status"),
                        width="stretch")
        with st.expander("Table view"):
            st.dataframe(pd.DataFrame({"Status": order, "Invoices": [m["status_counts"][k] for k in order]}),
                         hide_index=True, width="stretch")

    st.subheader("Invoice register")
    rows = [{
        "Invoice": c.id, "Vendor": c.invoice.vendor_name, "Number": c.invoice.invoice_number,
        "Amount": fmt_money(c.invoice.total), "PO": c.invoice.po_number or "—", "Status": c.status.value,
        "Exceptions": ", ".join(sorted({e.type.value for e in c.exceptions})) or "—",
        "Next step": (c.approval_route.next_step.role.value if c.approval_route and c.approval_route.next_step
                      and c.status == S.PENDING_APPROVAL else ("Post" if c.status == S.VALIDATED else "—")),
    } for c in cases]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if rows:
        o = st.columns([3, 1])
        target = o[0].selectbox("Open invoice", [r["Invoice"] for r in rows], label_visibility="collapsed",
                                format_func=lambda i: next(f"{r['Invoice']} · {r['Vendor']} · {r['Number']}"
                                                           for r in rows if r["Invoice"] == i))
        o[1].button("Open invoice →", on_click=open_invoice, args=(target,), width="stretch")

    with st.expander("Synthetic benchmark — 100 generated invoices"):
        st.caption("⚠️ Synthetic benchmark for demonstration purposes. Generated invoices with a controlled scenario "
                   "mix and a rule-based mock AI; not representative of real-world AP performance.")
        if st.button("Run benchmark"):
            with st.spinner("Processing 100 synthetic invoices…"):
                b = run_benchmark()
            pct = lambda v: f"{v:.1%}"  # noqa: E731
            st.dataframe(pd.DataFrame([
                ("Total invoices", str(b["total_invoices"])),
                ("Straight-through invoices", f"{b['straight_through_invoices']} ({pct(b['straight_through_rate'])})"),
                ("Exception rate", pct(b["exception_rate"])),
                ("Human-review rate", pct(b["human_review_rate"])),
                ("Duplicate detection rate", pct(b["duplicate_detection_rate"])),
                ("PO-match rate (PO-backed invoices)", pct(b["po_match_rate"])),
                ("Classification accuracy (GL)", pct(b["classification_accuracy"])),
                ("Expected-outcome agreement", pct(b["expected_outcome_agreement"])),
            ], columns=["Metric", "Value"]), hide_index=True, width="stretch")

    ledger = svc.repo.list_ledger()
    with st.expander(f"General ledger — {len({r['journal_entry_id'] for r in ledger})} posted journal entries"):
        if ledger:
            st.dataframe(pd.DataFrame([{
                "JE": r["journal_entry_id"], "Invoice": r["invoice_id"], "Date": r["posting_date"],
                "Account": acct_label(svc, r["account"]), "Dept": r["department"] or "", "Location": r["location"] or "",
                "Debit": fmt_money(r["debit"]) if r["debit"] else "", "Credit": fmt_money(r["credit"]) if r["credit"] else "",
                "Posted by": r["posted_by"],
            } for r in ledger]), hide_index=True, width="stretch")
        else:
            st.caption("Nothing posted yet. Open a VALIDATED invoice and click Post.")


# --------------------------------------------------------------------------- new invoice


def _processing(label: str):
    return st.spinner(f"{label} with {provider.name}… (a local LLM can take a minute)")


def _parse_lines(df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    """Turn the line-item editor into Decimal-safe dicts. Money is typed as text and parsed as Decimal."""
    lines, errors = [], []
    for i, row in enumerate(df.to_dict("records"), start=1):
        desc = str(row.get("Description") or "").strip()
        qty_raw, price_raw = row.get("Quantity"), row.get("Unit price")
        price_txt = "" if price_raw is None or pd.isna(price_raw) else str(price_raw).strip()
        if not desc and not price_txt:
            continue  # empty row
        if not desc:
            errors.append(f"Line {i}: description is required.")
            continue
        try:
            qty = Decimal(str(qty_raw)) if qty_raw is not None and not pd.isna(qty_raw) else Decimal("1")
            price = Decimal(price_txt.replace("$", "").replace(",", ""))
        except InvalidOperation:
            errors.append(f"Line {i}: quantity and unit price must be numbers.")
            continue
        if qty <= 0 or price < 0:
            errors.append(f"Line {i}: quantity must be positive and unit price non-negative.")
            continue
        lines.append({"description": desc, "quantity": qty, "unit_price": price, "amount": money(qty * price)})
    return lines, errors


def render_entry_form() -> None:
    ver = st.session_state.setdefault("ni_ver", 0)

    def k(name: str) -> str:
        return f"ni_{name}_{ver}"

    vendors = [v for v in svc.reference.vendors if v.active]
    c = st.columns([2, 1])
    vendor_name = c[0].selectbox("Vendor *", [v.name for v in vendors], index=None, key=k("vendor"),
                                 placeholder="Choose a vendor, or type a new name", accept_new_options=True)
    known = next((v for v in vendors if v.name == vendor_name), None)
    if known:
        c[1].text_input("Vendor tax ID (on file)", value=known.tax_id or "", disabled=True, key=k(f"tax_{known.id}"))
        tax_id = known.tax_id
    else:
        tax_id = c[1].text_input("Vendor tax ID (optional)", key=k("taxid"))

    c = st.columns(3)
    invoice_number = c[0].text_input("Invoice number *", key=k("number"))
    invoice_date = c[1].date_input("Invoice date *", value=date.today(), key=k("date"))
    due_date = c[2].date_input("Due date", value=None, key=k("due"))

    c = st.columns(2)
    po_options = ["No PO"] + [p.po_number for p in svc.reference.purchase_orders if known and p.vendor_id == known.id]
    po_choice = c[0].selectbox("PO number", po_options, key=k(f"po_{vendor_name}"), accept_new_options=True,
                               help="Choosing a PO pre-fills its lines. You can also type a PO number.")
    po_number = None if po_choice in (None, "No PO") else po_choice.strip()
    ship_to = c[1].selectbox("Ship-to location", svc.reference.locations, index=None, key=k("shipto"),
                             placeholder="Optional — choose or type", accept_new_options=True)

    po = svc.reference.purchase_order(po_number)
    if po:
        initial = pd.DataFrame([{"Description": ln.description, "Quantity": float(ln.quantity),
                                 "Unit price": str(ln.unit_price)} for ln in po.lines])
        st.caption(f"Lines pre-filled from {po.po_number}. Edit them to match the invoice — "
                   "differences are caught by PO matching.")
    else:
        initial = pd.DataFrame([{"Description": "", "Quantity": 1.0, "Unit price": ""}])
    edited = st.data_editor(
        initial, num_rows="dynamic", hide_index=True, width="stretch", key=k(f"lines_{po_number or 'none'}"),
        column_config={
            "Description": st.column_config.TextColumn("Description", required=True, width="large"),
            "Quantity": st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0, format="%g"),
            "Unit price": st.column_config.TextColumn("Unit price", help="e.g. 27.00 — parsed as Decimal"),
        })

    c = st.columns(2)
    tax_txt = c[0].text_input("Sales tax", value="0.00", key=k("tax"))
    printed_total = c[1].text_input("Invoice total as printed (optional)", key=k("printed_total"),
                                    help="Leave blank to use subtotal + tax. If you enter it, the system checks "
                                         "that the invoice foots.")

    lines, errors = _parse_lines(edited)
    try:
        tax = money(Decimal(tax_txt.replace("$", "").replace(",", "") or "0"))
    except InvalidOperation:
        tax, errors = Decimal("0.00"), errors + ["Sales tax must be a number."]
    subtotal = money(sum((ln["amount"] for ln in lines), Decimal("0")))
    s = st.columns(3)
    s[0].metric("Subtotal", fmt_money(subtotal))
    s[1].metric("Tax", fmt_money(tax))
    s[2].metric("Total", fmt_money(subtotal + tax))
    if printed_total.strip():
        try:
            if money(Decimal(printed_total.replace("$", "").replace(",", ""))) != money(subtotal + tax):
                st.warning("The printed total differs from subtotal + tax. The invoice will be flagged "
                           "INVOICE_ARITHMETIC_ERROR.", icon="⚠️")
        except InvalidOperation:
            errors.append("Printed total must be a number.")

    if st.button("Submit invoice", type="primary", key=k("submit")):
        if not vendor_name:
            errors.append("Vendor is required.")
        if not invoice_number.strip():
            errors.append("Invoice number is required.")
        if not lines:
            errors.append("At least one line item with a description and unit price is required.")
        if errors:
            st.error("\n".join(f"- {md(e)}" for e in errors))
            return
        data = {"vendor_name": vendor_name, "vendor_tax_id": tax_id or None,
                "invoice_number": invoice_number.strip(), "invoice_date": invoice_date, "due_date": due_date,
                "po_number": po_number, "ship_to": ship_to, "tax": tax, "lines": lines,
                "total": printed_total.strip() or None}
        try:
            extraction = extraction_from_fields(data)
            with _processing("Coding and checking the invoice"):
                case = svc.process_structured(extraction, entered_by=user.name, source="manual",
                                              intake=IntakeMethod.MANUAL_ENTRY)
        except (IntakeError, WorkflowError) as e:
            st.error(md(str(e)))
            return
        st.session_state["ni_ver"] = ver + 1  # clears the form
        flash("success", f"{case.id} created — {where_now(case)}.")
        open_invoice(case.id)
        st.rerun()


def _process_upload(name: str, raw: bytes) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    if name.lower().endswith(".json"):
        try:
            payload = json.loads(text, parse_float=Decimal)
        except json.JSONDecodeError as e:
            return [{"File": name, "Result": f"❌ Invalid JSON: {e}"}]
        items = payload.get("invoices", payload) if isinstance(payload, dict) else payload
        items = items if isinstance(items, list) else [items]
        results = []
        for n, item in enumerate(items, start=1):
            label = name if len(items) == 1 else f"{name} #{n}"
            try:
                case = svc.process_structured(extraction_from_fields(item), entered_by=user.name,
                                              source=f"upload:{name}", intake=IntakeMethod.STRUCTURED_UPLOAD)
                results.append({"File": label, "Result": f"✅ {case.id} — {where_now(case)}", "Invoice": case.id})
            except (IntakeError, WorkflowError) as e:
                results.append({"File": label, "Result": f"❌ {e}"})
        return results
    try:
        case = svc.process_document(text, source=f"upload:{name}")
        return [{"File": name, "Result": f"✅ {case.id} — {where_now(case)}", "Invoice": case.id}]
    except WorkflowError as e:
        hint = (" (The mock provider only reads the sample layout — choose Ollama in the sidebar for "
                "free-form invoices.)" if kind == "mock" else "")
        return [{"File": name, "Result": f"❌ {e}{hint}"}]


def render_upload() -> None:
    st.markdown("- **.txt** — invoice text, read by the selected AI provider\n"
                "- **.json** — invoice fields (one invoice, a list, or `{\"invoices\": [...]}`); no AI extraction\n\n"
                "Scanned PDFs and images need OCR, which is not implemented yet.")
    c = st.columns(2)
    c[0].download_button("⬇️ Sample invoice (.txt)", (DATA_DIR / "samples" / "sample_invoice.txt").read_bytes(),
                         file_name="sample_invoice.txt", width="stretch")
    c[1].download_button("⬇️ JSON template", (DATA_DIR / "samples" / "invoice_template.json").read_bytes(),
                         file_name="invoice_template.json", width="stretch")
    ver = st.session_state.setdefault("up_ver", 0)
    files = st.file_uploader("Invoice files", type=["txt", "json"], accept_multiple_files=True, key=f"upload_{ver}")
    if files and st.button(f"Process {len(files)} file(s)", type="primary", key="process_uploads"):
        results = []
        with _processing(f"Processing {len(files)} file(s)"):
            for f in files:
                results.extend(_process_upload(f.name, f.getvalue()))
        st.session_state["upload_results"] = results
        st.session_state["up_ver"] = ver + 1  # clears the uploader
        st.rerun()
    if results := st.session_state.get("upload_results"):
        st.dataframe(pd.DataFrame(results).fillna(""), hide_index=True, width="stretch")
        created = [r["Invoice"] for r in results if r.get("Invoice")]
        if created:
            o = st.columns([3, 1])
            target = o[0].selectbox("Open", created, label_visibility="collapsed", key="upload_open")
            o[1].button("Open invoice →", on_click=open_invoice, args=(target,), width="stretch", key="upload_open_btn")


def render_paste() -> None:
    st.caption("Paste invoice text. The mock provider reads the sample layout; Ollama or an OpenAI-compatible "
               "model can read free-form text.")
    ver = st.session_state.setdefault("paste_ver", 0)
    text = st.text_area("Invoice text", height=260, key=f"paste_{ver}")
    if st.button("Process invoice", type="primary", key="process_paste"):
        try:
            with _processing("Extracting and checking the invoice"):
                case = svc.process_document(text, source="paste")
        except WorkflowError as e:
            st.error(md(str(e)))
            return
        st.session_state["paste_ver"] = ver + 1
        flash("success", f"{case.id} created — {where_now(case)}.")
        open_invoice(case.id)
        st.rerun()


def page_new_invoice() -> None:
    st.title("New Invoice")
    st.caption(f"Entering as **{user.role_label}** · AI provider: **{provider.name}**. Whatever the intake method, "
               "every invoice goes through the same deterministic controls.")
    if not ollama_ok:
        st.warning("The selected Ollama host is not ready (see the sidebar). Text invoices can't be extracted, and "
                   "manually entered invoices will be routed for manual coding. Switch to the mock provider or fix "
                   "the connection.", icon="🔴")
    tab_enter, tab_upload, tab_paste = st.tabs(["✍️ Enter details", "📄 Upload file", "📋 Paste text"])
    with tab_enter:
        render_entry_form()
    with tab_upload:
        render_upload()
    with tab_paste:
        render_paste()


# --------------------------------------------------------------------------- approval inbox


def render_review_panel(case: InvoiceCase) -> None:
    inv, step = case.invoice, case.approval_route.next_step
    with st.container(border=True):
        st.markdown(f"### {inv.vendor_name} · {inv.invoice_number} · {md(fmt_money(inv.total))}")
        render_route(case)
        st.markdown(f"**Why it's with you:** {md(step.reason)}")

        left, right = st.columns(2)
        with left:
            facts = [("Invoice", case.id), ("Invoice date", str(inv.invoice_date)), ("PO", inv.po_number or "Non-PO")]
            if case.match_result:
                mr = case.match_result
                facts.append(("PO match", ("✅ within tolerance" if mr.passed else "❌ failed")
                              + f" (variance {fmt_money(mr.total_price_variance)}, tolerance {fmt_money(mr.price_tolerance)})"))
            if case.classification:
                cl = case.classification
                facts += [("GL account", acct_label(svc, cl.gl_account)),
                          ("Department / location", f"{cl.department} / {cl.location}"),
                          ("Coding", f"{cl.source} · {cl.confidence:.0%} confidence")]
            facts.append(("Intake", {IntakeMethod.AI_EXTRACTION: "AI extraction", IntakeMethod.MANUAL_ENTRY:
                                     "Manual entry", IntakeMethod.STRUCTURED_UPLOAD: "JSON upload"}[case.intake]))
            st.dataframe(pd.DataFrame(facts, columns=["", "Value"]), hide_index=True, width="stretch")
        with right:
            if case.open_exceptions:
                for exc in case.open_exceptions:
                    st.markdown(f"{SEVERITY_ICON[exc.severity]} **{exc.type}**" + (" · ⛔ blocking" if exc.blocking else "")
                                + f"  \n{md(exc.message)}" + (f"  \n🤖 *{md(exc.explanation)}*" if exc.explanation else ""))
            else:
                st.success("No open exceptions.", icon="✅")

        if case.journal_entry:
            je = case.journal_entry
            st.caption(f"Proposed journal entry {je.id}")
            st.dataframe(pd.DataFrame([{
                "Account": acct_label(svc, ln.account), "Dept": ln.department or "", "Location": ln.location or "",
                "Debit": fmt_money(ln.debit) if ln.debit else "", "Credit": fmt_money(ln.credit) if ln.credit else "",
            } for ln in je.lines]), hide_index=True, width="stretch")

        if case.blocking_exceptions:
            st.warning("Approval is blocked until the ⛔ exception(s) are resolved — correct the coding or dismiss "
                       "the duplicate on the invoice's detail page — or reject the invoice.", icon="⛔")
        t = st.columns(2)
        comment = t[0].text_input("Comment (optional)", key=f"inbox-comment-{case.id}")
        reason = t[1].text_input("Rejection reason (required to reject)", key=f"inbox-reason-{case.id}")
        b = st.columns(3)
        if b[0].button(f"✅ Approve as {step.role}", type="primary", key=f"inbox-approve-{case.id}", width="stretch"):
            run_action(f"Approved as {step.role}", svc.approve, case.id, user, comment or None)
        if b[1].button("✖ Reject", key=f"inbox-reject-{case.id}", width="stretch"):
            run_action("Rejected", svc.reject, case.id, user, reason)
        b[2].button("Open full detail →", on_click=open_invoice, args=(case.id,), key=f"inbox-open-{case.id}",
                    width="stretch")


def page_inbox() -> None:
    st.title("Approval Inbox")
    st.caption(f"Signed in as **{user.role_label}**. Each invoice travels through its approval route one role at a "
               "time, and only that role can approve its step. Switch role in the sidebar to follow an invoice "
               "along its route.")
    to_post = svc.ready_to_post() if user.can_post else []
    c = st.columns(3)
    c[0].metric("Waiting for you", len(inbox), border=True)
    c[1].metric("Blocked in your queue", sum(1 for x in inbox if x.blocking_exceptions), border=True,
                help="Has an exception that approval cannot clear (e.g. a high-confidence duplicate).")
    c[2].metric("Ready to post", len(to_post) if user.can_post else "—", border=True,
                help="Only AP Specialist and Controller can post.")

    with st.expander("Where invoices are right now"):
        waiting_by_role: dict[str, list[str]] = {}
        for x in cases:
            if x.status == S.PENDING_APPROVAL and x.approval_route and x.approval_route.next_step:
                waiting_by_role.setdefault(x.approval_route.next_step.role.value, []).append(x.id)
        rows = [{"Stage": role, "Invoices": len(ids), "IDs": ", ".join(ids)} for role, ids in waiting_by_role.items()]
        for label, status in (("Ready to post", S.VALIDATED), ("Posted", S.POSTED), ("Rejected", S.REJECTED)):
            ids = [x.id for x in cases if x.status == status]
            rows.append({"Stage": label, "Invoices": len(ids), "IDs": ", ".join(ids)})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if not inbox:
        st.success(f"Nothing is waiting for {user.title}.", icon="🎉")
    else:
        rows = []
        for x in inbox:
            steps = x.approval_route.steps
            idx = next(i for i, s in enumerate(steps, 1) if s.status == StepStatus.PENDING)
            rows.append({
                "Invoice": x.id, "Vendor": x.invoice.vendor_name, "Number": x.invoice.invoice_number,
                "Amount": fmt_money(x.invoice.total), "Your step": f"{idx} of {len(steps)}",
                "Why it's with you": x.approval_route.next_step.reason,
                "Open exceptions": ", ".join(("⛔ " if e.blocking else "") + e.type for e in x.open_exceptions) or "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        ids = [x.id for x in inbox]
        current = st.session_state.get("inbox_sel")
        sel = st.selectbox("Review", ids, index=ids.index(current) if current in ids else 0, key="inbox_sel",
                           format_func=lambda i: next(f"{r['Invoice']} · {r['Vendor']} · {r['Amount']}"
                                                      for r in rows if r["Invoice"] == i))
        render_review_panel(next(x for x in inbox if x.id == sel))

    if user.can_post:
        st.subheader("Ready to post")
        if not to_post:
            st.caption("No validated invoices waiting to be posted.")
        for x in to_post:
            r = st.columns([4, 1, 1])
            r[0].markdown(f"**{x.id}** · {x.invoice.vendor_name} · {x.invoice.invoice_number} · "
                          f"{md(fmt_money(x.invoice.total))}")
            r[1].button("Details", key=f"post-open-{x.id}", on_click=open_invoice, args=(x.id,), width="stretch")
            if r[2].button("Post", key=f"inbox-post-{x.id}", type="primary", width="stretch"):
                run_action("Posted", svc.post, x.id, user)


# --------------------------------------------------------------------------- invoice processing


def render_progress(case: InvoiceCase) -> None:
    reached = {h.to_state for h in case.state_history} | {S.RECEIVED}
    path = list(HAPPY_PATH)
    if S.EXCEPTION in reached:
        path.insert(path.index(S.PENDING_APPROVAL), S.EXCEPTION)
    if S.MATCHED not in reached and case.status not in (S.RECEIVED, S.EXTRACTED):
        path.remove(S.MATCHED)
    parts = []
    for s in path:
        if s == case.status:
            parts.append(f"**:{STATUS_COLOR.get(s, 'blue')}-background[{s}]**")
        elif s in reached:
            parts.append(f":gray[✓ {s}]")
        else:
            parts.append(f":gray[{s}]")
    if case.status == S.REJECTED:
        parts.append("**:gray-background[REJECTED]**")
    st.markdown(" → ".join(parts))


def render_invoice_detail(case: InvoiceCase) -> None:
    inv = case.invoice
    st.markdown(f"### {inv.vendor_name} · {inv.invoice_number} {status_badge(case.status)}")
    render_progress(case)

    c = st.columns(5)
    c[0].metric("Amount", fmt_money(inv.total))
    c[1].metric("Invoice date", inv.invoice_date.isoformat())
    c[2].metric("PO", inv.po_number or "Non-PO")
    c[3].metric("Vendor ID", inv.vendor_id or "Unidentified")
    c[4].metric("Open exceptions", len(case.open_exceptions))

    # --- intake & vendor
    left, right = st.columns(2)
    with left:
        title, owner = INTAKE_LABEL[case.intake]
        section(title, owner)
        ext = case.extraction
        st.dataframe(pd.DataFrame([
            ("Vendor name", ext.vendor_name), ("Tax ID", ext.vendor_tax_id or "—"),
            ("Invoice number", ext.invoice_number), ("Invoice date", str(ext.invoice_date)),
            ("Due date", str(ext.due_date or "—")), ("PO number", ext.po_number or "—"),
            ("Ship to", ext.ship_to or "—"), ("Subtotal", fmt_money(ext.subtotal)),
            ("Tax", fmt_money(ext.tax)), ("Total", fmt_money(ext.total)),
            ("Line items", str(len(ext.lines))),
            ("Extraction confidence" if case.intake == IntakeMethod.AI_EXTRACTION else "Source",
             f"{ext.confidence:.0%}" if case.intake == IntakeMethod.AI_EXTRACTION else inv.source),
        ], columns=["Field", "Value"]), hide_index=True, width="stretch")
        st.caption(f"AI provider: {case.ai_provider or 'n/a'}")
        with st.expander("Source document" if case.intake == IntakeMethod.AI_EXTRACTION else "Entered details"):
            st.code(case.document, language=None)
    with right:
        section("Vendor Identification", "DET")
        vm = case.vendor_match
        st.markdown(f"**{vm.status}** via `{vm.method}` · confidence {vm.confidence:.0%}  \n{md(vm.message)}")
        if vm.candidates:
            st.dataframe(pd.DataFrame([c.model_dump() for c in vm.candidates]), hide_index=True,
                         width="stretch")
        if vm.ai_suggestion:
            st.caption(f"🤖 AI suggestion (not auto-accepted): **{vm.ai_suggestion}** — {md(vm.ai_suggestion_reason)}")

        section("AI Classification", "AI")
        cl = case.classification
        if cl is None:
            st.caption("Not classified — processing halted before coding.")
        else:
            ai = case.ai_classification
            if cl.source == "HUMAN" and ai:
                st.caption(f"✏️ Corrected by {cl.corrected_by}. Original AI proposal: {ai.gl_account} / "
                           f"{ai.department} / {ai.location} ({ai.confidence:.0%}).")
            band = confidence_band(cl.confidence, settings.confidence)
            cc = st.columns(2)
            cc[0].markdown(f"**GL Account**  \n{acct_label(svc, cl.gl_account)}")
            cc[1].markdown(f"**Confidence**  \n{cl.confidence:.0%} · `{band}`")
            cc[0].markdown(f"**Department**  \n{cl.department}")
            cc[1].markdown(f"**Location**  \n{cl.location}")
            st.progress(cl.confidence)
            st.markdown(f"**Reason:** {md(cl.reason)}")
            if case.coding_issues:
                for issue in case.coding_issues:
                    st.error(f"Master-data validation: {md(issue)}", icon="🛑")
            st.caption(f"Bands: ≥{settings.confidence.straight_through:.0%} straight-through · "
                       f"{settings.confidence.review:.0%}–{settings.confidence.straight_through:.0%} human review · "
                       f"<{settings.confidence.review:.0%} exception")

    # --- PO matching & duplicates
    section("PO Matching", "DET")
    mr = case.match_result
    if mr:
        st.markdown(f"**{mr.po_number}** · {mr.match_type.replace('_', '-').lower()} match · "
                    + ("✅ matched within tolerance" if mr.passed else "❌ match failed"))
        st.dataframe(pd.DataFrame([{
            "Line": lm.invoice_line, "Description": lm.description, "Ordered": str(lm.ordered_quantity),
            "Received": "n/a (2-way)" if lm.received_quantity is None else str(lm.received_quantity),
            "Invoiced": str(lm.invoiced_quantity), "PO price": fmt_money(lm.po_unit_price),
            "Invoice price": fmt_money(lm.invoice_unit_price), "Price variance": fmt_money(lm.price_variance),
        } for lm in mr.lines]), hide_index=True, width="stretch")
        c = st.columns(3)
        c[0].metric("Total price variance", fmt_money(mr.total_price_variance))
        c[1].metric("Allowed tolerance", fmt_money(mr.price_tolerance),
                    help=f"Lesser of ${settings.tolerances.price_amount} or {settings.tolerances.price_percent}% "
                         "of the PO-priced amount")
        c[2].metric("Receipt on file", "Yes" if mr.receipt_found else "No")
    elif inv.po_number:
        st.warning(f"PO {inv.po_number} could not be matched (see exceptions).")
    else:
        st.caption("Non-PO invoice — no PO matching performed.")

    if case.duplicates:
        st.markdown("**Duplicate check:** " + "; ".join(
            f"{d.confidence} match with {d.invoice_id} ({d.invoice_number})" for d in case.duplicates))
    elif case.vendor_match.status == "MATCHED":
        st.markdown("**Duplicate check:** ✅ no duplicate candidates")

    # --- exceptions
    section(f"Exceptions ({len(case.exceptions)})", "DET")
    if not case.exceptions:
        st.success("No exceptions.", icon="✅")
    for exc in case.exceptions:
        with st.container(border=True):
            h = st.columns([4, 1])
            h[0].markdown(f"{SEVERITY_ICON[exc.severity]} **{exc.type}** · severity {exc.severity} · "
                          f"routed to **{exc.route_to}**" + (" · ⛔ blocking" if exc.blocking else ""))
            h[1].markdown(f":{'red' if exc.is_open else 'green'}-badge[{exc.status}]")
            st.markdown(md(exc.message))
            if exc.explanation:
                st.markdown(f"🤖 *{md(exc.explanation)}*")
            st.markdown(f"**Recommended action:** {md(exc.recommended_action)}")
            if exc.resolution:
                st.caption(f"Resolution: {md(exc.resolution)} ({exc.resolved_by})")
            with st.expander("Evidence"):
                st.json(exc.evidence)
            if exc.is_open and exc.type == ExceptionType.DUPLICATE_INVOICE:
                with st.form(f"dismiss-{exc.id}"):
                    reason = st.text_input("Reason this is NOT a duplicate (required)")
                    if st.form_submit_button("Dismiss duplicate flag"):
                        run_action("Duplicate flag dismissed", svc.dismiss_exception, case.id, exc.id, user, reason)

    # --- approval
    section("Approval Route", "DET")
    route = case.approval_route
    if route:
        render_route(case)
        st.markdown("  \n".join(f"- {md(r)}" for r in route.rationale))
        if route.steps:
            st.dataframe(pd.DataFrame([{
                "Step": i, "Approver": s.role.value, "Status": s.status.value, "Why": s.reason,
                "Decided by": s.decided_by or "", "Comment": s.comment or "",
            } for i, s in enumerate(route.steps, 1)]), hide_index=True, width="stretch")
        elif route.auto_approval_eligible:
            st.success("Auto-approved by policy (no human approval required).", icon="⚡")

    # --- accounting
    section("Proposed Journal Entry", "DET")
    je = case.journal_entry
    if je:
        st.caption(f"{je.id} · {je.date} · {je.description} · status **{je.status}**")
        rows = [{"Account": acct_label(svc, ln.account), "Department": ln.department or "",
                 "Location": ln.location or "", "Debit": fmt_money(ln.debit) if ln.debit else "",
                 "Credit": fmt_money(ln.credit) if ln.credit else "", "Memo": ln.memo} for ln in je.lines]
        rows.append({"Account": "Total", "Department": "", "Location": "", "Debit": fmt_money(je.total_debits),
                     "Credit": fmt_money(je.total_credits), "Memo": ""})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("No journal entry — the invoice has not been coded.")

    # --- validation
    section("Accounting Validation", "DET")
    if case.validation:
        for chk in case.validation.checks:
            st.markdown(f"{'✅' if chk.passed else '❌'} **{chk.rule}** — {md(chk.detail)}")
        if case.status == S.POSTED:
            st.success(f"POSTED to ledger as {je.id}.", icon="📒")
        elif case.validation.passed:
            st.success("All controls passed — ready to post.", icon="✅")
        else:
            st.error("POSTING BLOCKED — resolve the failed checks above.", icon="🚫")
    else:
        st.caption("Validation runs once all approvals are complete. Posting before then is blocked.")

    # --- actions
    section("Actions", "HUMAN")
    st.caption(f"Your role: **{user.role_label}**. Every action is checked by the workflow service — "
               "the UI cannot bypass state, permission or validation rules.")
    step = route.next_step if route and case.status == S.PENDING_APPROVAL else None
    t = st.columns(2)
    comment = t[0].text_input("Approval comment (optional)", key=f"comment-{case.id}")
    reason = t[1].text_input("Rejection reason (required to reject)", key=f"reject-reason-{case.id}")
    a = st.columns(3)
    approve_label = f"Approve as {step.role}" if step else "Approve"
    if a[0].button(approve_label, type="primary", key=f"approve-{case.id}", width="stretch"):
        run_action(f"Approved as {step.role}" if step else "Approved", svc.approve, case.id, user, comment or None)
    if a[1].button("Reject", key=f"reject-{case.id}", width="stretch"):
        run_action("Rejected", svc.reject, case.id, user, reason)
    if a[2].button("Post", key=f"post-{case.id}", width="stretch"):
        run_action("Posted", svc.post, case.id, user)

    if case.status in (S.PENDING_APPROVAL, S.APPROVED) and case.classification is not None:
        with st.expander("✏️ Correct coding (AP Review / Controller)"):
            accounts = svc.reference.codable_accounts()
            nums = [acct.number for acct in accounts]
            cl = case.classification
            with st.form(f"coding-{case.id}"):
                gl = st.selectbox("GL account", nums, format_func=lambda n: acct_label(svc, n),
                                  index=nums.index(cl.gl_account) if cl.gl_account in nums else 0)
                depts, locs = svc.reference.departments, svc.reference.locations
                dept = st.selectbox("Department", depts,
                                    index=depts.index(cl.department) if cl.department in depts else 0)
                loc = st.selectbox("Location", locs, index=locs.index(cl.location) if cl.location in locs else 0)
                if st.form_submit_button("Apply correction"):
                    run_action("Coding corrected", svc.correct_coding, case.id, user, gl, dept, loc)


def page_invoice() -> None:
    head = st.columns([4, 1])
    head[0].title("Invoice Processing")
    head[1].button("➕ New invoice", on_click=go_to, args=("New Invoice",), width="stretch")
    ids = [c.id for c in cases]
    if not ids:
        st.info("No invoices yet. Add one on the New Invoice page.")
        return
    labels = {c.id: f"{c.id} · {c.invoice.invoice_number} · {c.invoice.vendor_name} · "
                    f"{fmt_money(c.invoice.total)} · {c.status}" for c in cases}
    current = st.session_state.get("selected_invoice", ids[0])
    selected = st.selectbox("Invoice", ids, index=ids.index(current) if current in ids else 0,
                            format_func=labels.get)
    st.session_state["selected_invoice"] = selected
    st.divider()
    render_invoice_detail(svc.get(selected))


# --------------------------------------------------------------------------- exception queue


def page_exceptions() -> None:
    st.title("Exception Queue")
    rows, index = [], {}
    for c in cases:
        for e in c.exceptions:
            index[e.id] = (c, e)
            rows.append({
                "ID": e.id, "Invoice": c.id, "Vendor": c.invoice.vendor_name, "Exception": e.type.value,
                "Severity": e.severity.value, "Amount": fmt_money(c.invoice.total), "Status": e.status.value,
                "Owner": e.route_to.value, "Recommended Action": e.recommended_action,
            })
    if not rows:
        st.success("No exceptions.")
        return
    df = pd.DataFrame(rows)
    f = st.columns(3)
    status_filter = f[0].multiselect("Status", sorted(df["Status"].unique()), default=["OPEN"])
    type_filter = f[1].multiselect("Exception type", sorted(df["Exception"].unique()))
    sev_filter = f[2].multiselect("Severity", ["HIGH", "MEDIUM", "LOW"])
    if status_filter:
        df = df[df["Status"].isin(status_filter)]
    if type_filter:
        df = df[df["Exception"].isin(type_filter)]
    if sev_filter:
        df = df[df["Severity"].isin(sev_filter)]
    st.caption(f"{len(df)} exception(s)")
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key="exc-table")
    if not event.selection.rows or event.selection.rows[0] >= len(df):
        st.caption("Select an exception to inspect its evidence.")
        return
    case, exc = index[df.iloc[event.selection.rows[0]]["ID"]]
    with st.container(border=True):
        st.markdown(f"### {SEVERITY_ICON[exc.severity]} {exc.type} — {case.id} ({case.invoice.vendor_name}, "
                    f"{case.invoice.invoice_number})")
        st.markdown(md(exc.message))
        if exc.explanation:
            st.markdown(f"🤖 **AI explanation:** *{md(exc.explanation)}*")
        st.markdown(f"**Recommended action:** {md(exc.recommended_action)}")
        st.markdown(f"**Routed to:** {exc.route_to} · **Blocking:** {'yes' if exc.blocking else 'no'} · "
                    f"**Status:** {exc.status}")
        st.json(exc.evidence)
        st.button("Open invoice →", on_click=open_invoice, args=(case.id,))


# --------------------------------------------------------------------------- audit trail


def page_audit() -> None:
    st.title("Audit Trail")
    st.caption("Append-only. AI events store concise structured outputs only — never prompts or chain-of-thought.")
    f = st.columns(2)
    inv_filter = f[0].selectbox("Invoice", ["All"] + [c.id for c in cases],
                                format_func=lambda i: i if i == "All" else
                                f"{i} · {next(c.invoice.invoice_number for c in cases if c.id == i)}")
    actor_filter = f[1].multiselect("Actor type", ["SYSTEM", "AI", "HUMAN"], default=["SYSTEM", "AI", "HUMAN"])
    events = svc.repo.list_audit(None if inv_filter == "All" else inv_filter)
    rows = [{
        "Time": e.timestamp.strftime("%Y-%m-%d %H:%M:%S"), "Invoice": e.invoice_id or "", "Actor type": e.actor_type.value,
        "Actor": e.actor, "Action": e.action,
        "Details": json.dumps(e.details, default=str)[:300] if e.details else "",
    } for e in events if e.actor_type.value in actor_filter]
    st.caption(f"{len(rows)} event(s)")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=600)


# --------------------------------------------------------------------------- architecture

ARCH_DOT = """
digraph G {
  graph [rankdir=TB, bgcolor="#fcfcfb", pad="0.4", nodesep="0.6", ranksep="0.5", fontname="Helvetica"];
  node  [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=14, color="#8a8984",
         fontcolor="#0b0b0b", margin="0.3,0.14", width=7.5];
  edge  [fontname="Helvetica", fontsize=12, color="#52514e", fontcolor="#52514e", penwidth=1.4];

  ai   [label="AI Layer  (probabilistic — proposes)\\nInvoice extraction · Vendor suggestion · GL & dimension coding · Exception explanations\\nMock (demo) | Ollama (local / LAN) | OpenAI-compatible", fillcolor="#e9e4fb"];
  wf   [label="Workflow Layer\\nIntake (document · manual entry · JSON) · State machine · Role-based approval route · Audit trail", fillcolor="#dcebfb"];
  ctl  [label="Deterministic Control Layer  (validates & controls)\\nVendor master · PO matching & tolerances · Duplicates · Exceptions · Approval routing · Permissions", fillcolor="#dcebfb"];
  acc  [label="Accounting Layer\\nJournal-entry proposal · Debits = credits · Accounts & dimensions · Invoice = JE · Period open", fillcolor="#dcebfb"];
  erp  [label="ERP Boundary — LedgerGateway\\nInternal ledger (MVP)  |  NetSuite · Oracle · OpenSumma (future)", style="rounded,dashed,filled", fillcolor="#f1f0ec"];

  ai  -> wf  [label="  typed, schema-validated proposals"];
  wf  -> ctl [label="  every proposal is checked"];
  ctl -> acc [label="  approved invoice"];
  acc -> erp [label="  post() only after all validation passes"];
  ai  -> erp [style=dashed, color="#e34948", fontcolor="#e34948", arrowhead=tee, constraint=false,
              label="✕ no direct path — AI cannot post  "];
}
"""


def page_architecture() -> None:
    st.title("Architecture")
    st.markdown("> **AI proposes and reasons. Deterministic application logic validates and controls.**")
    st.graphviz_chart(ARCH_DOT, width="stretch")

    left, right = st.columns(2)
    with left:
        st.markdown("#### 🤖 AI may")
        st.markdown("""
- Extract invoice fields from documents
- Suggest a vendor when the match is ambiguous
- Propose GL account, department and location
- Assign a confidence score and a one-line reason
- Explain exceptions in plain language
""")
    with right:
        st.markdown("#### 🧮 Deterministic code controls")
        st.markdown("""
- Arithmetic, totals, tolerances and PO matching
- Vendor / account / dimension existence
- Duplicate detection and exception creation
- Approval thresholds, routing and role permissions
- Workflow state transitions
- Debit = credit, invoice = JE, period status
- Posting eligibility and the ledger write
""")

    st.markdown("#### Why separate them?")
    st.markdown("""
- **Financial integrity is not probabilistic.** An LLM can be 99% right about a GL code; a trial balance must be 100% right. Every AI output is a *proposal* that must pass deterministic validation.
- **The LLM is not the system of record.** Proposals are validated with Pydantic, checked against master data, and stored alongside the human decisions that accepted or corrected them.
- **The LLM cannot bypass accounting validation.** The provider interface only returns data; it has no reference to the workflow service or the ledger. Posting re-runs every control immediately before the ledger write.
- **Confidence drives human involvement.** ≥90% can flow straight through; 75–89% goes to AP review; <75% raises an exception. Thresholds are configuration, not model behaviour.
- **Auditability.** Every AI proposal, system decision and human action is an audit event with concise structured evidence — no hidden reasoning.
- **Replaceable parts.** Swap the model (mock ↔ Ollama ↔ any OpenAI-compatible API) or the ledger (internal ↔ ERP) without touching the controls.
""")
    st.markdown("#### Invoice lifecycle")
    st.code("RECEIVED → EXTRACTED → CLASSIFIED → MATCHED → [EXCEPTION] → PENDING_APPROVAL → APPROVED → VALIDATED → POSTED\n"
            "                                                     ↘ REJECTED (from any non-terminal state)", language=None)


{"Dashboard": page_dashboard, "New Invoice": page_new_invoice, "Approval Inbox": page_inbox,
 "Invoice Processing": page_invoice, "Exception Queue": page_exceptions, "Audit Trail": page_audit,
 "Architecture": page_architecture}[page]()
