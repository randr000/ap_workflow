from datetime import date

from app.domain.invoice import DuplicateConfidence
from app.domain.workflow import WorkflowState
from app.services.duplicate_detection import find_duplicates, similar_invoice_numbers


def test_exact_duplicate_is_high(invoice_factory):
    original = invoice_factory(id="AP-0001", invoice_number="INV-1001")
    dup = invoice_factory(id="AP-0002", invoice_number="INV-1001")
    found = find_duplicates(dup, [original])
    assert len(found) == 1
    assert found[0].confidence == DuplicateConfidence.HIGH
    assert found[0].invoice_id == "AP-0001"


def test_invoice_number_normalization_still_high(invoice_factory):
    original = invoice_factory(id="AP-0001", invoice_number="INV-1001")
    dup = invoice_factory(id="AP-0002", invoice_number="inv 1001")
    assert find_duplicates(dup, [original])[0].confidence == DuplicateConfidence.HIGH


def test_different_invoice_number_same_amount_is_low(invoice_factory):
    original = invoice_factory(id="AP-0001", invoice_number="INV-1001", invoice_date=date(2026, 9, 1))
    other = invoice_factory(id="AP-0002", invoice_number="INV-2077", invoice_date=date(2026, 9, 20))
    found = find_duplicates(other, [original])
    assert [c.confidence for c in found] == [DuplicateConfidence.LOW]


def test_different_invoice_number_and_amount_is_not_duplicate(invoice_factory):
    original = invoice_factory(id="AP-0001", invoice_number="INV-1001")
    other = invoice_factory(id="AP-0002", invoice_number="INV-2077",
                            lines=[("Copy paper, letter, 10-ream case", "10", "25.00")])
    assert find_duplicates(other, [original]) == []


def test_different_vendor_is_not_duplicate(invoice_factory):
    original = invoice_factory(id="AP-0001", invoice_number="INV-1001", vendor_id="V001")
    other = invoice_factory(id="AP-0002", invoice_number="INV-1001", vendor_id="V005")
    assert find_duplicates(other, [original]) == []


def test_similar_invoice_number_same_amount_is_medium(invoice_factory):
    original = invoice_factory(id="AP-0001", invoice_number="CD-2026-0915", invoice_date=date(2026, 9, 15))
    resubmitted = invoice_factory(id="AP-0002", invoice_number="CD-2026-0915-R", invoice_date=date(2026, 9, 24))
    found = find_duplicates(resubmitted, [original])
    assert found[0].confidence == DuplicateConfidence.MEDIUM
    assert any("Similar invoice number" in r for r in found[0].reasons)


def test_same_amount_outside_window_is_ignored(invoice_factory):
    original = invoice_factory(id="AP-0001", invoice_number="INV-1001", invoice_date=date(2026, 5, 1))
    other = invoice_factory(id="AP-0002", invoice_number="INV-2077", invoice_date=date(2026, 9, 20))
    assert find_duplicates(other, [original], window_days=60) == []


def test_rejected_invoices_are_ignored(invoice_factory):
    original = invoice_factory(id="AP-0001", invoice_number="INV-1001", status=WorkflowState.REJECTED)
    dup = invoice_factory(id="AP-0002", invoice_number="INV-1001")
    assert find_duplicates(dup, [original]) == []


def test_similar_invoice_numbers_helper():
    assert similar_invoice_numbers("CD-2026-0915", "CD20260915R")
    assert not similar_invoice_numbers("INV-1001", "INV-1002")
