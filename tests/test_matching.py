from decimal import Decimal

from app.config import Tolerances
from app.domain.exception import ExceptionType
from app.services.matching import effective_price_tolerance, match_invoice, pair_lines, within_price_tolerance

TOL = Tolerances()


def issue_types(result):
    return [i.type for i in result.issues]


def test_exact_three_way_match(reference, invoice_factory):
    inv = invoice_factory(po_number="PO-5001")
    result = match_invoice(inv, reference.purchase_order("PO-5001"), reference, TOL)
    assert result.passed
    assert result.receipt_found
    line = result.lines[0]
    assert line.ordered_quantity == line.received_quantity == line.invoiced_quantity == Decimal("20")
    assert result.total_price_variance == Decimal("0.00")


def test_price_variance_above_tolerance(reference, invoice_factory):
    # PO: 20 x $25 = $500. Invoice: 20 x $27 = $540. Variance $40 > $25 tolerance.
    inv = invoice_factory(po_number="PO-5003", lines=[("Ergonomic desk mat", "20", "27.00")])
    result = match_invoice(inv, reference.purchase_order("PO-5003"), reference, TOL)
    assert issue_types(result) == [ExceptionType.PRICE_VARIANCE]
    ev = result.issues[0].evidence
    assert ev["variance"] == "40.00"
    assert ev["tolerance"] == "25.00"
    assert ev["po_amount"] == "500.00"


def test_price_variance_within_tolerance(reference, invoice_factory):
    # 40 x $12.75 vs 40 x $12.50 -> $10 variance (2%) is within the $25 / 5% tolerance.
    inv = invoice_factory(po_number="PO-5002", lines=[("Toner cartridge, black, high yield", "40", "12.75")])
    result = match_invoice(inv, reference.purchase_order("PO-5002"), reference, TOL)
    assert result.passed
    assert result.total_price_variance == Decimal("10.00")


def test_tolerance_is_lesser_of_amount_and_percent():
    assert effective_price_tolerance(Decimal("500.00"), TOL) == Decimal("25.00")
    assert effective_price_tolerance(Decimal("200.00"), TOL) == Decimal("10.00")  # 5% < $25
    assert effective_price_tolerance(Decimal("100000.00"), TOL) == Decimal("25.00")
    assert within_price_tolerance(Decimal("25.00"), Decimal("500.00"), TOL)
    assert not within_price_tolerance(Decimal("25.01"), Decimal("500.00"), TOL)


def test_configurable_tolerance(reference, invoice_factory):
    loose = Tolerances(price_amount=Decimal("50.00"), price_percent=Decimal("10"))
    inv = invoice_factory(po_number="PO-5003", lines=[("Ergonomic desk mat", "20", "27.00")])
    assert match_invoice(inv, reference.purchase_order("PO-5003"), reference, loose).passed


def test_quantity_variance_against_ordered(reference, invoice_factory):
    inv = invoice_factory(vendor_id="V004", po_number="PO-5004",
                          lines=[("Process improvement consulting (hours)", "48", "150.00")])
    result = match_invoice(inv, reference.purchase_order("PO-5004"), reference, TOL)
    assert issue_types(result) == [ExceptionType.QUANTITY_VARIANCE]
    ev = result.issues[0].evidence
    assert ev["basis"] == "ordered" and ev["variance_units"] == "8" and ev["variance_amount"] == "1200.00"


def test_quantity_tolerance_allows_small_overage(reference, invoice_factory):
    inv = invoice_factory(vendor_id="V004", po_number="PO-5004",
                          lines=[("Process improvement consulting (hours)", "41", "150.00")])
    tol = Tolerances(quantity=Decimal("1"))
    assert match_invoice(inv, reference.purchase_order("PO-5004"), reference, tol).passed


def test_three_way_match_failure_received_less_than_invoiced(reference, invoice_factory):
    # Ordered 100, received 80, invoiced 100.
    inv = invoice_factory(vendor_id="V007", po_number="PO-5005",
                          lines=[("Industrial shelving unit, 48 in", "100", "18.00")])
    result = match_invoice(inv, reference.purchase_order("PO-5005"), reference, TOL)
    assert issue_types(result) == [ExceptionType.QUANTITY_VARIANCE]
    ev = result.issues[0].evidence
    assert ev["basis"] == "received" and ev["received"] == "80" and ev["variance_units"] == "20"


def test_three_way_missing_receipt(reference, invoice_factory):
    inv = invoice_factory(vendor_id="V007", po_number="PO-5007",
                          lines=[("Pallet stretch wrap, 18 in roll", "120", "18.00")])
    result = match_invoice(inv, reference.purchase_order("PO-5007"), reference, TOL)
    assert issue_types(result) == [ExceptionType.MISSING_RECEIPT]
    assert not result.receipt_found


def test_two_way_match_ignores_receipts(reference, invoice_factory):
    inv = invoice_factory(vendor_id="V004", po_number="PO-5004",
                          lines=[("Process improvement consulting (hours)", "40", "150.00")])
    result = match_invoice(inv, reference.purchase_order("PO-5004"), reference, TOL)
    assert result.passed
    assert result.lines[0].received_quantity is None


def test_vendor_mismatch(reference, invoice_factory):
    inv = invoice_factory(vendor_id="V010", po_number="PO-5001")
    result = match_invoice(inv, reference.purchase_order("PO-5001"), reference, TOL)
    assert ExceptionType.VENDOR_MISMATCH in issue_types(result)


def test_multi_line_pairing_by_description(reference, invoice_factory):
    inv = invoice_factory(po_number="PO-5006", lines=[
        ("Whiteboard markers 12 pack", "20", "12.00"),
        ("Heavy-duty stapler", "10", "18.25"),
        ("Printer paper legal 10 ream case", "10", "32.00"),
    ])
    po = reference.purchase_order("PO-5006")
    assert pair_lines(inv.lines, po.lines) == {1: 3, 2: 1, 3: 2}
    assert match_invoice(inv, po, reference, TOL).passed


def test_line_not_on_po(reference, invoice_factory):
    inv = invoice_factory(po_number="PO-5001", lines=[
        ("Copy paper, letter, 10-ream case", "20", "25.00"),
        ("Espresso machine", "1", "300.00"),
    ])
    result = match_invoice(inv, reference.purchase_order("PO-5001"), reference, TOL)
    assert any(i.evidence.get("basis") == "not_on_po" for i in result.issues)
