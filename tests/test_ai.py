import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.ai.mock_provider import MockLLMProvider, keyword_gl, location_from_ship_to
from app.ai.openai_provider import OpenAICompatibleProvider
from app.ai.provider import AIProviderError, create_provider
from app.config import ConfidenceThresholds, Settings
from app.domain.exception import ExceptionType
from app.domain.vendor import VendorMatchMethod, VendorMatchStatus
from app.services.classification import ConfidenceBand, build_context, confidence_band, validate_coding
from app.services.extraction import build_invoice, extract
from app.services.vendor_matching import identify_vendor
from demo.documents import InvoiceSpec, render_invoice

MOCK = MockLLMProvider()


def doc(**kw):
    base = dict(vendor_name="Acme Office Supplies", invoice_number="INV-1001", invoice_date=date(2026, 9, 5),
                lines=[("Copy paper, letter, 10-ream case", "20", "25.00")], po_number="PO-5001",
                tax_id="59-1234567")
    base.update(kw)
    return render_invoice(InvoiceSpec(**base))


# --- extraction -------------------------------------------------------------

def test_mock_extraction_reads_document():
    ext = extract(MOCK, doc(tax="12.34", lines=[("Stapler", "10", "18.25"), ("Paper", "1000", "1.50")]))
    assert ext.vendor_name == "Acme Office Supplies"
    assert ext.vendor_tax_id == "59-1234567"
    assert ext.invoice_number == "INV-1001"
    assert ext.invoice_date == date(2026, 9, 5)
    assert ext.po_number == "PO-5001"
    assert ext.ship_to == "Harborview Holdings - Miami Office"
    assert [ln.amount for ln in ext.lines] == [Decimal("182.50"), Decimal("1500.00")]
    assert ext.total == Decimal("1694.84")
    assert ext.confidence >= 0.95


def test_non_po_document_has_no_po():
    assert extract(MOCK, doc(po_number=None)).po_number is None


def test_extraction_rejects_garbage():
    with pytest.raises(AIProviderError):
        extract(MOCK, "hello world, not an invoice")
    with pytest.raises(AIProviderError):
        extract(MOCK, "   ")


def test_build_invoice_normalizes(reference):
    inv = build_invoice(extract(MOCK, doc(po_number="po-5001")), "AP-0001", "V001", "test")
    assert inv.po_number == "PO-5001"
    assert inv.lines[0].line_number == 1 and inv.arithmetic_issues() == []


# --- vendor identification --------------------------------------------------

def test_vendor_exact_tax_id(reference):
    m = identify_vendor(extract(MOCK, doc(vendor_name="ACME OFFICE SUPPLY CO")), reference, MOCK)
    assert (m.status, m.method, m.vendor_id) == (VendorMatchStatus.MATCHED, VendorMatchMethod.TAX_ID, "V001")


def test_vendor_exact_normalized_name(reference):
    m = identify_vendor(extract(MOCK, doc(vendor_name="SUMMIT LEGAL PARTNERS LLP", tax_id=None)), reference, MOCK)
    assert m.status == VendorMatchStatus.MATCHED and m.vendor_id == "V003"


def test_vendor_fuzzy_match(reference):
    m = identify_vendor(extract(MOCK, doc(vendor_name="Pinnacle Industrial Supplies", tax_id=None)), reference, MOCK)
    assert (m.status, m.method, m.vendor_id) == (VendorMatchStatus.MATCHED, VendorMatchMethod.FUZZY, "V007")


def test_vendor_ambiguous_requires_human(reference):
    m = identify_vendor(extract(MOCK, doc(vendor_name="Metro Supply Co", tax_id=None)), reference, MOCK)
    assert m.status == VendorMatchStatus.AMBIGUOUS
    assert m.vendor_id is None  # the AI suggestion is recorded but never auto-accepted
    assert {c.vendor_id for c in m.candidates} == {"V010", "V011"}
    assert m.ai_suggestion in {"V010", "V011"}


def test_vendor_not_found(reference):
    m = identify_vendor(extract(MOCK, doc(vendor_name="Zephyr Quantum Widgets", tax_id=None)), reference, MOCK)
    assert m.status == VendorMatchStatus.NOT_FOUND


def test_ai_vendor_suggestion_outside_candidates_is_ignored(reference):
    class RogueProvider(MockLLMProvider):
        def suggest_vendor(self, vendor_name, candidates):
            from app.ai.provider import VendorSuggestion
            return VendorSuggestion(vendor_id="V999", confidence=0.99, reason="trust me")

    m = identify_vendor(extract(MOCK, doc(vendor_name="Metro Supply Co", tax_id=None)), reference, RogueProvider())
    assert m.ai_suggestion is None and m.vendor_id is None


# --- classification ---------------------------------------------------------

def _classify(reference, vendor_id, descriptions, po_number=None, city="Miami"):
    lines = [(d, "1", "100.00") for d in descriptions]
    ext = extract(MOCK, doc(lines=lines, po_number=po_number, ship_to_city=city))
    inv = build_invoice(ext, "AP-0001", vendor_id, "test")
    ctx = build_context(inv, reference.vendor(vendor_id), reference, ship_to=ext.ship_to)
    return MOCK.classify_invoice(inv, ctx)


def test_classification_uses_history_and_description(reference):
    p = _classify(reference, "V002", ["Cloud compute usage - September 2026"])
    assert (p.gl_account, p.department, p.location) == ("6600", "Technology", "Miami")
    assert p.confidence >= 0.9
    assert confidence_band(p.confidence, ConfidenceThresholds()) == ConfidenceBand.STRAIGHT_THROUGH


def test_classification_inherits_po_coding(reference):
    p = _classify(reference, "V001", ["Ergonomic desk mat"], po_number="PO-5003")
    assert (p.gl_account, p.department, p.location) == ("6100", "Finance", "New York")


def test_conflicting_signals_fall_to_review_band(reference):
    p = _classify(reference, "V002", ["Annual monitoring software license"])
    assert p.gl_account == "6200"
    assert confidence_band(p.confidence, ConfidenceThresholds()) == ConfidenceBand.HUMAN_REVIEW


def test_unknown_vendor_vague_description_is_low_confidence(reference):
    p = _classify(reference, "V013", ["Q3 support services - miscellaneous"])
    assert confidence_band(p.confidence, ConfidenceThresholds()) == ConfidenceBand.EXCEPTION


def test_invalid_ai_coding_is_caught_deterministically(reference):
    p = _classify(reference, "V009", ["Client visit - airfare and hotel"], city="Chicago")
    assert p.location == "Chicago"
    issues = validate_coding(p, reference)
    assert [t for t, _, _ in issues] == [ExceptionType.INVALID_DIMENSION]

    stale = _classify(reference, "V012", ["Brand refresh design services"])
    assert stale.gl_account == "6800"
    assert [t for t, _, _ in validate_coding(stale, reference)] == [ExceptionType.INVALID_ACCOUNT]


def test_thresholds_are_configurable():
    strict = ConfidenceThresholds(straight_through=0.99, review=0.95)
    assert confidence_band(0.97, strict) == ConfidenceBand.HUMAN_REVIEW
    assert confidence_band(0.90, strict) == ConfidenceBand.EXCEPTION


def test_keyword_and_location_helpers():
    assert keyword_gl(["Electricity service - August"]) == "6500"
    assert keyword_gl(["Current period adjustment"]) is None  # 'rent' must match as a whole word
    assert location_from_ship_to("Harborview Holdings - New York Office") == "New York"


def test_explanations_are_concise(reference):
    from app.services.exceptions import new_exception
    exc = new_exception("AP-0001", 1, ExceptionType.PRICE_VARIANCE, "x",
                        {"variance": "40.00", "invoice_amount": "540.00", "po_amount": "500.00", "tolerance": "25.00"})
    text = MOCK.explain_exception(exc)
    assert "$40.00" in text and len(text) < 300


# --- provider factory / OpenAI-compatible provider --------------------------

def test_default_provider_is_mock_without_api_key():
    assert isinstance(create_provider(Settings(llm_provider="openai", openai_api_key=None)), MockLLMProvider)
    assert isinstance(create_provider(Settings()), MockLLMProvider)


def _openai_with(content: str) -> OpenAICompatibleProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return OpenAICompatibleProvider("key", "https://llm.example/v1", "test-model", transport=httpx.MockTransport(handler))


def test_openai_provider_validates_structured_output():
    payload = {"vendor_name": "Acme Office Supplies", "invoice_number": "INV-1", "invoice_date": "2026-09-25",
               "po_number": "PO-5001", "subtotal": 500.0, "tax": 0.0, "total": 500.0, "confidence": 0.9,
               "lines": [{"description": "Paper", "quantity": 20, "unit_price": 25.0, "amount": 500.0}]}
    ext = _openai_with(json.dumps(payload)).extract_invoice("doc")
    assert ext.total == Decimal("500.0") and isinstance(ext.total, Decimal)


def test_openai_provider_rejects_invalid_output():
    with pytest.raises(AIProviderError):
        _openai_with(json.dumps({"vendor_name": "Acme"})).extract_invoice("doc")
    with pytest.raises(AIProviderError):
        _openai_with("not json").extract_invoice("doc")
    with pytest.raises(AIProviderError):  # confidence out of range
        _openai_with(json.dumps({"gl_account": "6600", "confidence": 1.7, "reason": "x"})).classify_invoice(None, _ctx())


def _ctx():
    from app.domain.accounting import ClassificationContext
    return ClassificationContext(vendor_id="V002", vendor_name="Nimbus", descriptions=["x"])
