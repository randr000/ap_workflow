from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.case import InvoiceCase
from app.domain.invoice import InvoiceExtraction
from app.domain.money import fmt_money, money
from app.domain.vendor import normalize_vendor_name
from app.domain.workflow import WorkflowState


def test_money_rejects_float():
    with pytest.raises(TypeError):
        money(10.5)


def test_money_rounds_half_up():
    assert money(Decimal("2.345")) == Decimal("2.35")
    assert money("100") == Decimal("100.00")
    assert fmt_money(Decimal("4250")) == "$4,250.00"


def test_invoice_uses_decimal_and_defaults_to_received(invoice_factory):
    inv = invoice_factory()
    assert isinstance(inv.total, Decimal)
    assert inv.total == Decimal("500.00")
    assert inv.status == WorkflowState.RECEIVED
    assert inv.arithmetic_issues() == []


def test_invoice_arithmetic_issues_detected(invoice_factory):
    inv = invoice_factory()
    inv.lines[0].amount = Decimal("510.00")
    issues = inv.arithmetic_issues()
    assert any("Line 1" in i for i in issues)
    assert any("subtotal" in i for i in issues)


def test_invoice_total_must_equal_subtotal_plus_tax(invoice_factory):
    inv = invoice_factory(tax="35.00")
    assert inv.arithmetic_issues() == []
    inv.total = Decimal("500.00")
    assert any("total" in i for i in inv.arithmetic_issues())


def test_extraction_contract_converts_llm_floats_safely():
    ext = InvoiceExtraction.model_validate({
        "vendor_name": "Acme Office Supplies", "invoice_number": "INV-1", "invoice_date": "2026-09-01",
        "po_number": "", "subtotal": 0.1, "tax": 0.2, "total": 0.3,
        "lines": [{"description": "x", "quantity": 1, "unit_price": 0.1, "amount": 0.1}],
    })
    assert ext.subtotal + ext.tax == ext.total  # would fail with binary floats
    assert ext.po_number is None


def test_extraction_contract_rejects_invalid_payload():
    with pytest.raises(ValidationError):
        InvoiceExtraction.model_validate({"vendor_name": "", "invoice_number": "X",
                                          "invoice_date": "not-a-date", "subtotal": "1",
                                          "total": "1", "lines": []})


def test_vendor_name_normalization():
    assert normalize_vendor_name("Metro Supply Co.") == normalize_vendor_name("METRO SUPPLY COMPANY")
    assert normalize_vendor_name("Summit Legal Partners LLP") == "summit legal partners"


def test_case_round_trips_through_json(invoice_factory):
    case = InvoiceCase(invoice=invoice_factory(), document="doc")
    restored = InvoiceCase.model_validate_json(case.model_dump_json())
    assert restored.invoice.total == Decimal("500.00")
    assert restored.invoice.lines[0].unit_price == Decimal("25.00")


def test_reference_data_loads(reference):
    assert reference.account("6600").name == "Cloud Computing Expense"
    assert reference.active_account("6800") is None  # retired account
    assert "Technology" in reference.departments
    assert reference.period_is_open(date(2026, 9, 15))
    assert not reference.period_is_open(date(2026, 8, 15))
    po = reference.purchase_order("po-5005")
    assert po.lines[0].received_quantity == Decimal("80")


def test_repository_persists_reference_and_cases(repo, invoice_factory):
    ref = repo.load_reference()
    assert len(ref.vendors) == 13
    assert ref.purchase_order("PO-5006").total == Decimal("742.50")
    assert isinstance(ref.purchase_order("PO-5006").lines[0].unit_price, Decimal)

    case = InvoiceCase(invoice=invoice_factory(id=repo.next_invoice_id()), document="doc")
    repo.save_case(case)
    assert repo.next_invoice_id() == "AP-0002"
    loaded = repo.get_case("AP-0001")
    assert loaded.invoice.total == Decimal("500.00")
