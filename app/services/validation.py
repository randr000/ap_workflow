"""Deterministic accounting validation — the gate in front of the ledger.

Every rule must pass before a journal entry can be posted. The workflow service
calls this at approval time AND again immediately before posting, so no caller
(UI, API, or AI) can bypass it.
"""

from __future__ import annotations

from app.domain.accounting import AccountType, JournalEntry, ValidationCheck, ValidationReport
from app.domain.approval import ApprovalRoute
from app.domain.exception import ExceptionRecord, ExceptionType
from app.domain.invoice import Invoice
from app.domain.money import ZERO, fmt_money, money
from app.domain.reference import AP_CONTROL_ACCOUNT, ReferenceData
from app.domain.workflow import WorkflowState

REQUIRED_DIMENSION_TYPES = (AccountType.EXPENSE, AccountType.ASSET)


def _balanced(je: JournalEntry) -> ValidationCheck:
    bad_lines = [i for i, ln in enumerate(je.lines, 1)
                 if ln.debit < 0 or ln.credit < 0 or (ln.debit > 0) == (ln.credit > 0)]
    debits, credits = money(je.total_debits), money(je.total_credits)
    ok = debits == credits and debits > ZERO and not bad_lines
    detail = f"Debits {fmt_money(debits)} / Credits {fmt_money(credits)}"
    if bad_lines:
        detail += f"; lines {bad_lines} must carry exactly one positive debit or credit"
    return ValidationCheck(rule="Debits = Credits", passed=ok, detail=detail)


def _accounts(je: JournalEntry, ref: ReferenceData) -> ValidationCheck:
    problems = []
    for ln in je.lines:
        acct = ref.account(ln.account)
        if acct is None:
            problems.append(f"{ln.account or '(blank)'} does not exist")
        elif not acct.active:
            problems.append(f"{ln.account} is inactive")
    return ValidationCheck(rule="Accounts valid", passed=not problems,
                           detail="; ".join(problems) or "All accounts exist and are active")


def _dimensions(je: JournalEntry, ref: ReferenceData) -> ValidationCheck:
    problems = []
    for i, ln in enumerate(je.lines, 1):
        acct = ref.account(ln.account)
        required = acct is not None and acct.type in REQUIRED_DIMENSION_TYPES
        if ln.department is None and required:
            problems.append(f"line {i}: department required")
        elif ln.department is not None and ln.department not in ref.departments:
            problems.append(f"line {i}: department '{ln.department}' is not valid")
        if ln.location is None and required:
            problems.append(f"line {i}: location required")
        elif ln.location is not None and ln.location not in ref.locations:
            problems.append(f"line {i}: location '{ln.location}' is not valid")
    return ValidationCheck(rule="Dimensions valid", passed=not problems,
                           detail="; ".join(problems) or "Department and location valid on all lines")


def _agrees_to_invoice(invoice: Invoice, je: JournalEntry) -> ValidationCheck:
    ap_credit = money(sum((ln.credit for ln in je.lines if ln.account == AP_CONTROL_ACCOUNT), ZERO))
    total = money(invoice.total)
    ok = ap_credit == total and money(je.total_debits) == total and je.invoice_id == invoice.id
    return ValidationCheck(
        rule="Invoice total = JE amount", passed=ok,
        detail=f"Invoice {fmt_money(total)} / AP credit {fmt_money(ap_credit)} / debits {fmt_money(je.total_debits)}")


def _approved(invoice: Invoice, route: ApprovalRoute | None) -> ValidationCheck:
    status_ok = invoice.status in (WorkflowState.APPROVED, WorkflowState.VALIDATED)
    route_ok = route is not None and route.is_complete
    return ValidationCheck(
        rule="Invoice approved", passed=status_ok and route_ok,
        detail=f"Status {invoice.status}; approval route "
               + ("complete" if route_ok else "incomplete"))


def _not_posted(invoice: Invoice, already_in_ledger: bool) -> ValidationCheck:
    ok = invoice.status != WorkflowState.POSTED and not already_in_ledger
    return ValidationCheck(rule="Not already posted", passed=ok,
                           detail="No prior posting" if ok else "Invoice already posted to the ledger")


def _period_open(je: JournalEntry, ref: ReferenceData) -> ValidationCheck:
    period = ref.period_for(je.date)
    ok = ref.period_is_open(je.date)
    label = period.period if period else "undefined"
    return ValidationCheck(rule="Period open", passed=ok,
                           detail=f"Period {label} is {'OPEN' if ok else (period.status if period else 'not defined')}")


def _exceptions_cleared(exceptions: list[ExceptionRecord]) -> ValidationCheck:
    open_ = [e for e in exceptions if e.is_open and e.type != ExceptionType.ACCOUNTING_VALIDATION_ERROR]
    dup = [e for e in open_ if e.type == ExceptionType.DUPLICATE_INVOICE]
    detail = ("Open duplicate-invoice flag" if dup else
              ", ".join(f"{e.type} open" for e in open_) or "No open exceptions")
    return ValidationCheck(rule="Exceptions resolved (no duplicate)", passed=not open_, detail=detail)


def validate_for_posting(
    invoice: Invoice,
    je: JournalEntry | None,
    route: ApprovalRoute | None,
    exceptions: list[ExceptionRecord],
    reference: ReferenceData,
    already_in_ledger: bool = False,
) -> ValidationReport:
    if je is None:
        return ValidationReport(checks=[ValidationCheck(
            rule="Journal entry exists", passed=False, detail="No journal entry has been proposed")])
    return ValidationReport(checks=[
        _balanced(je),
        _accounts(je, reference),
        _dimensions(je, reference),
        _agrees_to_invoice(invoice, je),
        _approved(invoice, route),
        _not_posted(invoice, already_in_ledger),
        _period_open(je, reference),
        _exceptions_cleared(exceptions),
    ])
