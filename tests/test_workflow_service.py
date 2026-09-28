"""Integration tests: full workflow through the service with the mock provider."""

from datetime import date
from decimal import Decimal

import pytest

from app.ai.mock_provider import MockLLMProvider
from app.config import Settings
from app.db.database import IN_MEMORY_URL
from app.domain.accounting import JournalEntryStatus
from app.domain.approval import ApproverRole, get_user
from app.domain.exception import ExceptionStatus, ExceptionType
from app.domain.workflow import ActorType, WorkflowState as S
from app.services.workflow_service import PostingBlockedError, WorkflowError, create_workflow_service
from demo.documents import InvoiceSpec, render_invoice

AP = get_user("Dana Brooks")
PROCUREMENT = get_user("Priya Shah")
DEPT_MGR = get_user("Marcus Lee")
CONTROLLER = get_user("Elena Torres")


@pytest.fixture
def svc():
    return create_workflow_service(IN_MEMORY_URL, reset=True, settings=Settings(), provider=MockLLMProvider())


def doc(**kw):
    base = dict(vendor_name="Acme Office Supplies", invoice_number="INV-1001", invoice_date=date(2026, 9, 5),
                lines=[("Copy paper, letter, 10-ream case", "20", "25.00")], po_number="PO-5001",
                tax_id="59-1234567")
    base.update(kw)
    return render_invoice(InvoiceSpec(**base))


def types(case):
    return {e.type for e in case.exceptions}


def test_perfect_match_is_straight_through_then_human_posts(svc):
    case = svc.process_document(doc())
    assert case.status == S.VALIDATED
    assert case.straight_through and case.exceptions == []
    assert [h.to_state for h in case.state_history] == [S.EXTRACTED, S.CLASSIFIED, S.MATCHED, S.APPROVED, S.VALIDATED]
    assert case.validation.passed

    posted = svc.post(case.id, AP)
    assert posted.status == S.POSTED
    assert posted.journal_entry.status == JournalEntryStatus.POSTED
    ledger = svc.repo.list_ledger()
    assert sum(r["debit"] for r in ledger) == sum(r["credit"] for r in ledger) == Decimal("500.00")


def test_posting_twice_is_blocked(svc):
    case = svc.process_document(doc())
    svc.post(case.id, AP)
    with pytest.raises(PostingBlockedError):
        svc.post(case.id, AP)
    assert len(svc.repo.list_ledger()) == 2  # still only the original two lines


def test_posting_before_approval_is_blocked(svc):
    case = svc.process_document(doc(invoice_number="INV-2001", po_number="PO-5003",
                                    lines=[("Ergonomic desk mat", "20", "27.00")]))
    assert case.status == S.PENDING_APPROVAL
    with pytest.raises(PostingBlockedError):
        svc.post(case.id, AP)
    assert svc.repo.list_ledger() == []


def test_price_exception_routed_to_procurement_and_resolved_on_approval(svc):
    case = svc.process_document(doc(invoice_number="INV-1004", po_number="PO-5003",
                                    lines=[("Ergonomic desk mat", "20", "27.00")]))
    assert types(case) == {ExceptionType.PRICE_VARIANCE}
    assert [s.role for s in case.approval_route.steps] == [ApproverRole.PROCUREMENT_MANAGER]
    assert S.EXCEPTION in [h.to_state for h in case.state_history]

    with pytest.raises(WorkflowError, match="not authorized"):
        svc.approve(case.id, DEPT_MGR)

    case = svc.approve(case.id, PROCUREMENT, comment="Price increase accepted per vendor notice")
    assert case.status == S.VALIDATED
    assert case.exceptions[0].status == ExceptionStatus.RESOLVED
    assert svc.post(case.id, AP).status == S.POSTED


def test_rejected_invoice_cannot_post(svc):
    case = svc.process_document(doc(invoice_number="INV-2002", po_number="PO-5003",
                                    lines=[("Ergonomic desk mat", "20", "27.00")]))
    case = svc.reject(case.id, PROCUREMENT, "Price not agreed")
    assert case.status == S.REJECTED
    assert all(not e.is_open for e in case.exceptions)
    with pytest.raises(PostingBlockedError):
        svc.post(case.id, AP)
    with pytest.raises(WorkflowError):
        svc.approve(case.id, PROCUREMENT)


def test_duplicate_blocks_approval_and_posting(svc):
    first = svc.process_document(doc())
    svc.post(first.id, AP)
    dup = svc.process_document(doc())
    assert types(dup) == {ExceptionType.DUPLICATE_INVOICE}
    assert dup.exceptions[0].blocking
    assert dup.status == S.PENDING_APPROVAL
    with pytest.raises(WorkflowError, match="blocked"):
        svc.approve(dup.id, AP)
    with pytest.raises(PostingBlockedError):
        svc.post(dup.id, AP)
    assert svc.reject(dup.id, AP, "Duplicate of AP-0001").status == S.REJECTED


def test_duplicate_can_be_dismissed_with_reason_then_approved(svc):
    svc.process_document(doc())
    dup = svc.process_document(doc())
    exc_id = dup.exceptions[0].id
    with pytest.raises(WorkflowError, match="reason"):
        svc.dismiss_exception(dup.id, exc_id, AP, "  ")
    svc.dismiss_exception(dup.id, exc_id, AP, "Vendor confirmed re-used invoice number for a new shipment")
    case = svc.approve(dup.id, AP)
    assert case.status == S.VALIDATED


def test_high_value_requires_controller(svc):
    case = svc.process_document(doc(vendor_name="Summit Legal Partners LLP", tax_id="13-3456789",
                                    invoice_number="SLP-2026-0918", po_number=None,
                                    lines=[("Legal retainer - September", "1", "18500.00")]))
    assert [s.role for s in case.approval_route.steps] == [ApproverRole.CONTROLLER]
    with pytest.raises(WorkflowError):
        svc.approve(case.id, DEPT_MGR)
    assert svc.approve(case.id, CONTROLLER).status == S.VALIDATED


def test_multi_step_approval_in_order(svc):
    case = svc.process_document(doc(vendor_name="Brightpath Consulting Group", tax_id="36-4567890",
                                    invoice_number="BCG-0912", po_number="PO-5004",
                                    lines=[("Process improvement consulting (hours)", "48", "150.00")]))
    assert [s.role for s in case.approval_route.steps] == [ApproverRole.PROCUREMENT_MANAGER,
                                                           ApproverRole.DEPARTMENT_MANAGER]
    with pytest.raises(WorkflowError):
        svc.approve(case.id, DEPT_MGR)  # must follow route order
    assert svc.approve(case.id, PROCUREMENT).status == S.PENDING_APPROVAL
    assert svc.approve(case.id, DEPT_MGR).status == S.VALIDATED


def test_invalid_dimension_blocks_until_coding_corrected(svc):
    case = svc.process_document(doc(vendor_name="Skyway Travel Management", tax_id="13-9012345",
                                    invoice_number="SKY-4471", po_number=None, ship_to_city="Chicago",
                                    lines=[("Client visit - airfare and hotel", "1", "1240.00")]))
    assert types(case) == {ExceptionType.INVALID_DIMENSION}
    with pytest.raises(WorkflowError, match="blocked"):
        svc.approve(case.id, AP)
    with pytest.raises(WorkflowError, match="Invalid coding"):
        svc.correct_coding(case.id, AP, "6400", "Sales", "Chicago")  # humans get validated too
    with pytest.raises(WorkflowError, match="not authorized"):
        svc.correct_coding(case.id, PROCUREMENT, "6400", "Sales", "New York")

    case = svc.correct_coding(case.id, AP, "6400", "Sales", "New York")
    assert case.classification.source == "HUMAN"
    assert case.ai_classification.location == "Chicago"  # original AI proposal preserved
    assert case.journal_entry.lines[0].location == "New York"
    svc.approve(case.id, AP)
    assert svc.approve(case.id, DEPT_MGR).status == S.VALIDATED


def test_ambiguous_vendor_halts_processing(svc):
    case = svc.process_document(doc(vendor_name="Metro Supply Co.", tax_id=None, invoice_number="MS-88120",
                                    po_number=None, lines=[("Janitorial supplies", "1", "640.00")]))
    assert types(case) == {ExceptionType.VENDOR_AMBIGUITY}
    assert case.classification is None and case.journal_entry is None
    assert case.status == S.PENDING_APPROVAL
    with pytest.raises(WorkflowError, match="blocked"):
        svc.approve(case.id, AP)


def test_closed_period_blocks_posting(svc):
    case = svc.process_document(doc(invoice_date=date(2026, 8, 28)))
    assert case.status == S.APPROVED  # auto-approved but validation failed
    assert not case.straight_through
    assert any(c.rule == "Period open" for c in case.validation.failures)
    assert ExceptionType.ACCOUNTING_VALIDATION_ERROR in types(case)
    with pytest.raises(PostingBlockedError):
        svc.post(case.id, AP)


def test_only_authorized_users_can_post(svc):
    case = svc.process_document(doc())
    with pytest.raises(WorkflowError, match="not authorized"):
        svc.post(case.id, PROCUREMENT)


def test_audit_trail_records_ai_system_and_human(svc):
    case = svc.process_document(doc())
    svc.post(case.id, AP)
    events = svc.repo.list_audit(case.id)
    actions = [e.action for e in events]
    assert actions[0] == "Invoice received"
    assert "Invoice extracted" in actions and "GL account proposed" in actions
    assert "Journal entry validated" in actions and actions[-1] == "Journal entry posted"
    assert {e.actor_type for e in events} == {ActorType.SYSTEM, ActorType.AI, ActorType.HUMAN}


def test_ai_provider_has_no_path_to_ledger(svc):
    """The provider interface only returns proposals; nothing on it can post or change state."""
    public = {n for n in dir(svc.provider) if not n.startswith("_")}
    assert public == {"name", "extract_invoice", "suggest_vendor", "classify_invoice", "explain_exception"}


def test_arithmetic_error_detected(svc):
    case = svc.process_document(doc(invoice_number="INV-3001", total_override="550.00"))
    assert ExceptionType.INVOICE_ARITHMETIC_ERROR in types(case)
    with pytest.raises(WorkflowError):
        svc.approve(case.id, AP)


# --- structured intake (manual entry / JSON upload) --------------------------

from app.domain.case import IntakeMethod  # noqa: E402
from app.services.extraction import IntakeError, extraction_from_fields  # noqa: E402


class NoExtractionProvider(MockLLMProvider):
    """Proves structured intake never asks the AI to extract."""

    def extract_invoice(self, document):
        raise AssertionError("AI extraction must not run for manually entered invoices")


def manual(**kw):
    base = {"vendor_name": "Acme Office Supplies", "vendor_tax_id": "59-1234567", "invoice_number": "INV-5555",
            "invoice_date": "2026-09-25", "po_number": "PO-5003",
            "lines": [{"description": "Ergonomic desk mat", "quantity": 20, "unit_price": "27.00"}]}
    base.update(kw)
    return extraction_from_fields(base)


def test_manual_entry_skips_ai_extraction_but_runs_every_control():
    svc = create_workflow_service(IN_MEMORY_URL, reset=True, settings=Settings(), provider=NoExtractionProvider())
    case = svc.process_structured(manual(), entered_by="Dana Brooks")
    assert case.intake == IntakeMethod.MANUAL_ENTRY
    assert case.extraction.total == Decimal("540.00")  # computed from the lines
    assert types(case) == {ExceptionType.PRICE_VARIANCE}  # PO matching still applies
    assert case.classification.gl_account == "6100"  # AI coding still proposes
    actions = [(e.actor_type, e.action) for e in svc.repo.list_audit(case.id)]
    assert (ActorType.HUMAN, "Invoice details entered manually") in actions
    assert not any(a == "Invoice extracted" for _, a in actions)


def test_structured_invoice_that_does_not_foot_is_flagged(svc):
    case = svc.process_structured(manual(total="600.00"), entered_by="Dana Brooks",
                                  intake=IntakeMethod.STRUCTURED_UPLOAD)
    assert ExceptionType.INVOICE_ARITHMETIC_ERROR in types(case)
    assert case.intake == IntakeMethod.STRUCTURED_UPLOAD


def test_manual_ship_to_city_is_validated(svc):
    case = svc.process_structured(manual(vendor_name="Skyway Travel Management", vendor_tax_id=None,
                                         invoice_number="SKY-1", po_number=None, ship_to="Chicago",
                                         lines=[{"description": "Client visit - airfare", "unit_price": "420"}]),
                                  entered_by="Dana Brooks")
    assert types(case) == {ExceptionType.INVALID_DIMENSION}


def test_extraction_from_fields_rejects_bad_input():
    with pytest.raises(IntakeError, match="line item"):
        extraction_from_fields({"vendor_name": "Acme", "invoice_number": "1", "invoice_date": "2026-09-01"})
    with pytest.raises(IntakeError, match="not a valid number"):
        manual(lines=[{"description": "x", "quantity": 1, "unit_price": "twelve"}])
    with pytest.raises(IntakeError, match="invoice_date"):
        manual(invoice_date="next tuesday")
    assert manual(po_number="").po_number is None


# --- role-based approval inbox ------------------------------------------------

def test_invoice_travels_through_role_inboxes(svc):
    case = svc.process_document(doc(vendor_name="Brightpath Consulting Group", tax_id="36-4567890",
                                    invoice_number="BCG-0912", po_number="PO-5004",
                                    lines=[("Process improvement consulting (hours)", "48", "150.00")]))

    def inboxes():
        return {u.name: [c.id for c in svc.pending_for(u)] for u in (AP, PROCUREMENT, DEPT_MGR, CONTROLLER)}

    assert inboxes() == {AP.name: [], PROCUREMENT.name: [case.id], DEPT_MGR.name: [], CONTROLLER.name: []}
    svc.approve(case.id, PROCUREMENT)
    assert inboxes() == {AP.name: [], PROCUREMENT.name: [], DEPT_MGR.name: [case.id], CONTROLLER.name: []}
    svc.approve(case.id, DEPT_MGR)
    assert all(ids == [] for ids in inboxes().values())
    assert [c.id for c in svc.ready_to_post()] == [case.id]


def test_each_role_can_only_act_on_its_own_step(svc):
    case = svc.process_document(doc(vendor_name="Nimbus Cloud Services", tax_id="91-2345678",
                                    invoice_number="NCS-1", po_number=None,
                                    lines=[("Cloud compute usage", "1", "4250.00")]))
    assert [s.role for s in case.approval_route.steps] == [ApproverRole.DEPARTMENT_MANAGER]
    with pytest.raises(WorkflowError, match="not authorized"):
        svc.approve(case.id, CONTROLLER)  # the Controller role no longer doubles as Department Manager
    with pytest.raises(WorkflowError, match="not authorized"):
        svc.reject(case.id, PROCUREMENT, "not mine")
    assert svc.reject(case.id, DEPT_MGR, "Not budgeted").status == S.REJECTED
