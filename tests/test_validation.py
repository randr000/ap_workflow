from datetime import date
from decimal import Decimal

import pytest

from app.domain.approval import ApprovalRoute, ApprovalStep, ApproverRole, StepStatus
from app.domain.exception import ExceptionType
from app.domain.workflow import WorkflowState
from app.services.exceptions import new_exception
from app.services.journal_entries import build_journal_entry
from app.services.validation import validate_for_posting

APPROVED_ROUTE = ApprovalRoute(auto_approval_eligible=False, steps=[
    ApprovalStep(role=ApproverRole.DEPARTMENT_MANAGER, reason="x", status=StepStatus.APPROVED)])


@pytest.fixture
def approved_invoice(invoice_factory):
    return invoice_factory(status=WorkflowState.APPROVED)


def failed_rules(report):
    return {c.rule for c in report.failures}


def run(inv, je, reference, route=APPROVED_ROUTE, exceptions=(), in_ledger=False):
    return validate_for_posting(inv, je, route, list(exceptions), reference, in_ledger)


def test_valid_entry_passes_all_rules(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    report = run(approved_invoice, je, reference)
    assert report.passed, report.failures
    assert len(report.checks) == 8


def test_unbalanced_entry_blocked(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    je.lines[0].debit = Decimal("499.99")
    assert "Debits = Credits" in failed_rules(run(approved_invoice, je, reference))


def test_line_with_both_debit_and_credit_blocked(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    je.lines[0].credit = Decimal("1.00")
    je.lines[1].credit += Decimal("1.00")
    assert "Debits = Credits" in failed_rules(run(approved_invoice, je, reference))


def test_invalid_account_blocked(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    je.lines[0].account = "9999"
    assert "Accounts valid" in failed_rules(run(approved_invoice, je, reference))


def test_inactive_account_blocked(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    je.lines[0].account = "6800"
    report = run(approved_invoice, je, reference)
    assert "Accounts valid" in failed_rules(report)
    assert "inactive" in report.failures[0].detail


def test_invalid_dimension_blocked(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    je.lines[0].location = "Chicago"
    assert "Dimensions valid" in failed_rules(run(approved_invoice, je, reference))


def test_missing_required_dimension_blocked(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    je.lines[0].department = None
    assert "Dimensions valid" in failed_rules(run(approved_invoice, je, reference))


def test_invoice_je_mismatch_blocked(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    je.lines[0].debit = Decimal("450.00")
    je.lines[1].credit = Decimal("450.00")  # balanced, but does not agree to the $500 invoice
    report = run(approved_invoice, je, reference)
    assert failed_rules(report) == {"Invoice total = JE amount"}


def test_unapproved_invoice_blocked(invoice_factory, reference):
    inv = invoice_factory(status=WorkflowState.PENDING_APPROVAL)
    je = build_journal_entry(inv, "Acme")
    pending = ApprovalRoute(auto_approval_eligible=False,
                            steps=[ApprovalStep(role=ApproverRole.CONTROLLER, reason="x")])
    assert "Invoice approved" in failed_rules(run(inv, je, reference, route=pending))


def test_already_posted_blocked(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    assert "Not already posted" in failed_rules(run(approved_invoice, je, reference, in_ledger=True))


def test_closed_period_blocked(invoice_factory, reference):
    inv = invoice_factory(status=WorkflowState.APPROVED, invoice_date=date(2026, 8, 20))
    je = build_journal_entry(inv, "Acme")
    report = run(inv, je, reference)
    assert failed_rules(report) == {"Period open"}


def test_open_duplicate_blocks_posting(approved_invoice, reference):
    je = build_journal_entry(approved_invoice, "Acme")
    dup = new_exception(approved_invoice.id, 1, ExceptionType.DUPLICATE_INVOICE, "dup")
    report = run(approved_invoice, je, reference, exceptions=[dup])
    assert failed_rules(report) == {"Exceptions resolved (no duplicate)"}


def test_missing_je_blocked(approved_invoice, reference):
    assert not run(approved_invoice, None, reference).passed
