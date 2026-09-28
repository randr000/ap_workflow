"""AI AP Automation Agent — Streamlit demo UI.

    streamlit run streamlit_app.py

The UI is a thin presentation layer: every button calls the workflow service,
which enforces the state machine, permissions and accounting validation. The
UI has no way to change invoice state directly.
"""

from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st

from app.config import load_settings
from app.domain.approval import DEMO_USERS, get_user
from app.domain.case import InvoiceCase
from app.domain.exception import ExceptionType, Severity
from app.domain.money import fmt_money
from app.domain.workflow import WorkflowState as S
from app.services.classification import confidence_band
from app.services.reporting import dashboard_metrics
from app.services.workflow_service import PostingBlockedError, WorkflowError, create_workflow_service
from demo.benchmark import run_benchmark
from demo.scenarios import run_demo_batch

st.set_page_config(page_title="AI AP Automation Agent", page_icon="🧾", layout="wide")

SERIES_COLOR = "#2a78d6"
PAGES = ["Dashboard", "Invoice Processing", "Exception Queue", "Audit Trail", "Architecture"]
STATUS_COLOR = {
    S.POSTED: "green", S.VALIDATED: "green", S.APPROVED: "blue", S.PENDING_APPROVAL: "orange",
    S.EXCEPTION: "red", S.REJECTED: "gray",
}
SEVERITY_ICON = {Severity.HIGH: "🔴", Severity.MEDIUM: "🟠", Severity.LOW: "🟡"}
HAPPY_PATH = [S.RECEIVED, S.EXTRACTED, S.CLASSIFIED, S.MATCHED, S.PENDING_APPROVAL, S.APPROVED, S.VALIDATED, S.POSTED]


# --------------------------------------------------------------------------- service


@st.cache_resource
def get_service():
    svc = create_workflow_service()
    if not svc.list():
        run_demo_batch(svc)
    return svc


def reset_demo() -> None:
    get_service.clear()
    run_demo_batch(create_workflow_service(reset=True))
    flash("success", "Demo data reset: 20 invoices re-processed from data/invoices/.")


def md(text) -> str:
    """Escape '$' so Streamlit markdown doesn't render dollar amounts as LaTeX."""
    return str(text).replace("$", "\\$")


def flash(kind: str, message: str) -> None:
    st.session_state["flash"] = (kind, message)


def show_flash() -> None:
    if msg := st.session_state.pop("flash", None):
        kind, text = msg
        {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)(md(text))


def run_action(label: str, fn, *args) -> None:
    """Call a workflow-service action; surface control failures instead of bypassing them."""
    try:
        case = fn(*args)
        flash("success", f"{label}: invoice is now {case.status}.")
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


def status_badge(status: S) -> str:
    return f":{STATUS_COLOR.get(status, 'blue')}-badge[{status}]"


def acct_label(svc, number: str | None) -> str:
    acct = svc.reference.account(number)
    return acct.label if acct else f"{number or '—'} (unknown)"


# --------------------------------------------------------------------------- sidebar

svc = get_service()
settings = load_settings()
if nav := st.session_state.pop("nav_to", None):
    st.session_state["page"] = nav

with st.sidebar:
    st.markdown("## 🧾 AI AP Automation Agent")
    st.caption("Proof of Concept")
    st.radio("Navigate", PAGES, key="page", label_visibility="collapsed")
    st.divider()
    user_name = st.selectbox("Acting as", [u.name for u in DEMO_USERS],
                             format_func=lambda n: get_user(n).label, key="user")
    user = get_user(user_name)
    st.caption("Roles: " + ", ".join(user.roles) + (" · can post" if user.can_post else ""))
    st.divider()
    st.caption(f"**AI provider:** {svc.provider.name}")
    st.caption("**Principle:** AI proposes and reasons. Deterministic application logic validates and controls.")
    if st.button("Reset demo data", width="stretch"):
        reset_demo()
        st.rerun()

show_flash()
cases = svc.list()
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
    st.caption("Harborview Holdings, Inc. · September 2026 · 20 synthetic demo invoices")
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


def section(title: str, owner: str) -> None:
    tag = {"AI": ":violet-badge[AI proposal]", "DET": ":blue-badge[Deterministic]",
           "HUMAN": ":orange-badge[Human-in-the-loop]"}[owner]
    st.markdown(f"#### {title} {tag}")


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

    # --- extraction & vendor
    left, right = st.columns(2)
    with left:
        section("AI Extraction", "AI")
        ext = case.extraction
        st.dataframe(pd.DataFrame([
            ("Vendor name", ext.vendor_name), ("Tax ID", ext.vendor_tax_id or "—"),
            ("Invoice number", ext.invoice_number), ("Invoice date", str(ext.invoice_date)),
            ("Due date", str(ext.due_date or "—")), ("PO number", ext.po_number or "—"),
            ("Ship to", ext.ship_to or "—"), ("Subtotal", fmt_money(ext.subtotal)),
            ("Tax", fmt_money(ext.tax)), ("Total", fmt_money(ext.total)),
            ("Line items", str(len(ext.lines))), ("Extraction confidence", f"{ext.confidence:.0%}"),
        ], columns=["Field", "Extracted value"]), hide_index=True, width="stretch")
        with st.expander("Source document"):
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
    section("Approval Routing", "DET")
    route = case.approval_route
    if route:
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
    st.caption(f"Acting as **{user.label}**. Every action is checked by the workflow service — "
               "the UI cannot bypass state, permission or validation rules.")
    step = route.next_step if route and case.status == S.PENDING_APPROVAL else None
    t = st.columns(2)
    comment = t[0].text_input("Approval comment (optional)", key=f"comment-{case.id}")
    reason = t[1].text_input("Rejection reason (required to reject)", key=f"reject-reason-{case.id}")
    a = st.columns(3)
    approve_label = f"Approve as {step.role}" if step else "Approve"
    if a[0].button(approve_label, type="primary", key=f"approve-{case.id}", width="stretch"):
        run_action("Approved", svc.approve, case.id, user, comment or None)
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
    st.title("Invoice Processing")
    ids = [c.id for c in cases]
    labels = {c.id: f"{c.id} · {c.invoice.invoice_number} · {c.invoice.vendor_name} · "
                    f"{fmt_money(c.invoice.total)} · {c.status}" for c in cases}
    if ids:
        current = st.session_state.get("selected_invoice", ids[0])
        selected = st.selectbox("Invoice", ids, index=ids.index(current) if current in ids else 0,
                                format_func=labels.get)
        st.session_state["selected_invoice"] = selected

    with st.expander("➕ Process a new invoice document"):
        st.caption("Paste invoice text (same layout as data/invoices/*.txt in mock mode; any layout with an LLM provider).")
        text = st.text_area("Invoice document", height=220)
        if st.button("Process invoice"):
            try:
                case = svc.process_document(text, source="upload")
                st.session_state["selected_invoice"] = case.id
                flash("success", f"Processed {case.id}: {case.status}.")
            except WorkflowError as e:
                flash("error", str(e))
            st.rerun()

    if ids:
        st.divider()
        render_invoice_detail(svc.get(st.session_state["selected_invoice"]))


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

  ai   [label="AI Layer  (probabilistic — proposes)\nInvoice extraction · Vendor suggestion · GL & dimension coding · Exception explanations", fillcolor="#e9e4fb"];
  wf   [label="Workflow Layer\nState machine · Orchestration · Human-in-the-loop approvals · Audit trail", fillcolor="#dcebfb"];
  ctl  [label="Deterministic Control Layer  (validates & controls)\nVendor master · PO matching & tolerances · Duplicates · Exceptions · Approval routing · Permissions", fillcolor="#dcebfb"];
  acc  [label="Accounting Layer\nJournal-entry proposal · Debits = credits · Accounts & dimensions · Invoice = JE · Period open", fillcolor="#dcebfb"];
  erp  [label="ERP Boundary — LedgerGateway\nInternal ledger (MVP)  |  NetSuite · Oracle · OpenSumma (future)", style="rounded,dashed,filled", fillcolor="#f1f0ec"];

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
- Approval thresholds, routing and permissions
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
- **Replaceable parts.** Swap the model (mock ↔ any OpenAI-compatible API) or the ledger (internal ↔ ERP) without touching the controls.
""")
    st.markdown("#### Invoice lifecycle")
    st.code("RECEIVED → EXTRACTED → CLASSIFIED → MATCHED → [EXCEPTION] → PENDING_APPROVAL → APPROVED → VALIDATED → POSTED\n"
            "                                                     ↘ REJECTED (from any non-terminal state)", language=None)


{"Dashboard": page_dashboard, "Invoice Processing": page_invoice, "Exception Queue": page_exceptions,
 "Audit Trail": page_audit, "Architecture": page_architecture}[page]()
