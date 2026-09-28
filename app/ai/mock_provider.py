"""Deterministic mock provider — the default "demo mode".

It stands in for an LLM with transparent, rule-based behaviour so the entire
demo is reproducible without an API key:

* extraction   — parses the synthetic invoice text layout
* vendor       — suggests the most similar candidate
* coding       — PO coding > vendor history > vendor default > keywords, with a
                 deterministic confidence derived from how strongly signals agree
* explanations — short templates built from structured exception evidence
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from pydantic import ValidationError

from app.ai.provider import AIProviderError, VendorSuggestion
from app.domain.accounting import ClassificationContext, ClassificationProposal
from app.domain.exception import ExceptionRecord, ExceptionType
from app.domain.invoice import Invoice, InvoiceExtraction
from app.domain.vendor import Vendor, normalize_vendor_name

# GL keyword signals, in priority order for ties.
GL_KEYWORDS: list[tuple[str, list[str]]] = [
    ("6600", ["cloud", "compute", "hosting", "storage", "bandwidth", "server", "instance"]),
    ("6200", ["software", "license", "licence", "subscription", "saas", "seats"]),
    ("6400", ["airfare", "hotel", "travel", "flight", "lodging", "car rental"]),
    ("6500", ["electricity", "electric", "water", "utility", "utilities", "natural gas"]),
    ("6700", ["rent", "lease", "office space"]),
    ("6300", ["legal", "consulting", "audit", "advisory", "retainer", "attorney"]),
    ("6100", ["paper", "toner", "stapler", "markers", "pens", "office supplies", "desk mat"]),
    ("6900", ["advertising", "media buy", "marketing campaign", "sponsorship"]),
    ("1300", ["inventory", "shelving", "stretch wrap", "pallet"]),
]
GL_DEFAULT_DEPARTMENT = {"6600": "Technology", "6200": "Technology", "6400": "Sales",
                         "6300": "Finance", "6900": "Marketing"}
HQ_LOCATION = "Miami"
FALLBACK_GL = "6300"

_FIELD = r"^\s*{label}\s*:\s*(.+?)\s*$"


def _field(text: str, label: str) -> str | None:
    m = re.search(_FIELD.format(label=label), text, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else None


def _amount(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(raw.replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        return None


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def keyword_gl(descriptions: list[str]) -> str | None:
    text = " ".join(descriptions).lower()
    hits: Counter[str] = Counter()
    for gl, words in GL_KEYWORDS:
        for w in words:
            if re.search(rf"\b{re.escape(w)}\b", text):
                hits[gl] += 1
    if not hits:
        return None
    best = max(hits.values())
    return next(gl for gl, _ in GL_KEYWORDS if hits.get(gl) == best)


def location_from_ship_to(ship_to: str | None) -> str | None:
    if not ship_to:
        return None
    m = re.search(r"-\s*([A-Za-z .]+?)\s+Office\b", ship_to)
    return m.group(1).strip() if m else None


class MockLLMProvider:
    name = "Mock provider (deterministic demo mode)"

    # --- extraction --------------------------------------------------------

    def extract_invoice(self, document: str) -> InvoiceExtraction:
        lines = [ln for ln in document.splitlines()]
        vendor_name = next((ln.strip() for ln in lines if ln.strip()), "")
        items, in_table = [], False
        for ln in lines:
            if "|" in ln and "description" in ln.lower():
                in_table = True
                continue
            if in_table:
                if not ln.strip():
                    break
                cells = [c.strip() for c in ln.split("|")]
                if len(cells) == 4:
                    items.append({"description": cells[0], "quantity": _amount(cells[1]),
                                  "unit_price": _amount(cells[2]), "amount": _amount(cells[3])})
        payload = {
            "vendor_name": vendor_name,
            "vendor_tax_id": _field(document, "Tax ID"),
            "invoice_number": _field(document, "Invoice Number"),
            "invoice_date": _date(_field(document, "Invoice Date")),
            "due_date": _date(_field(document, "Due Date")),
            "po_number": _field(document, "PO Number"),
            "currency": _field(document, "Currency") or "USD",
            "ship_to": _field(document, "Ship To"),
            "subtotal": _amount(_field(document, "Subtotal")),
            "tax": _amount(_field(document, "Tax")) or Decimal("0.00"),
            "total": _amount(_field(document, "Total")),
            "lines": items,
        }
        required = ["vendor_name", "invoice_number", "invoice_date", "subtotal", "total"]
        found = sum(1 for k in required if payload[k]) + (1 if items else 0)
        payload["confidence"] = round(0.70 + 0.28 * found / (len(required) + 1), 2)
        try:
            return InvoiceExtraction.model_validate(payload)
        except ValidationError as e:
            raise AIProviderError(f"Extraction output failed validation: {e.error_count()} error(s)") from e

    # --- vendor ------------------------------------------------------------

    def suggest_vendor(self, vendor_name: str, candidates: list[Vendor]) -> VendorSuggestion:
        if not candidates:
            return VendorSuggestion(vendor_id=None, confidence=0.0, reason="No candidate vendors supplied.")
        norm = normalize_vendor_name(vendor_name)
        scored = sorted(((SequenceMatcher(None, norm, v.normalized_name).ratio(), v) for v in candidates),
                        key=lambda t: (-t[0], t[1].id))
        best_score, best = scored[0]
        tied = len(scored) > 1 and abs(scored[1][0] - best_score) < 0.02
        if tied:
            return VendorSuggestion(
                vendor_id=best.id, confidence=0.5,
                reason=f"{len(scored)} vendor records are equally similar to '{vendor_name}' and the "
                       f"invoice has no tax ID; {best.id} is only a tie-break guess.")
        return VendorSuggestion(vendor_id=best.id, confidence=round(best_score, 2),
                                reason=f"Closest vendor name match to '{vendor_name}'.")

    # --- classification ----------------------------------------------------

    def classify_invoice(self, invoice: Invoice, context: ClassificationContext) -> ClassificationProposal:
        kw = keyword_gl(context.descriptions)
        ship_loc = location_from_ship_to(context.ship_to)
        hist = context.history[0] if context.history else None
        acct_names = {a.number: a.name for a in context.accounts}

        def name(gl: str) -> str:
            return acct_names.get(gl, "account")

        if context.po_coding:
            c = context.po_coding[0]
            return ClassificationProposal(
                gl_account=c["gl_account"], department=c.get("department"),
                location=c.get("location") or ship_loc, confidence=0.97,
                reason=f"Coding inherited from purchase order {invoice.po_number}.")

        if hist and hist.count >= 3:
            location = ship_loc or hist.location
            if kw is None or kw == hist.gl_account:
                agree = kw == hist.gl_account
                return ClassificationProposal(
                    gl_account=hist.gl_account, department=hist.department, location=location,
                    confidence=0.96 if agree else 0.93,
                    reason=(f"{name(hist.gl_account)} based on {hist.count} prior {context.vendor_name} invoices"
                            + (" and matching invoice description." if agree else ".")))
            return ClassificationProposal(
                gl_account=kw, department=hist.department, location=location, confidence=0.80,
                reason=(f"Description indicates {name(kw)}, but vendor history is {hist.gl_account} "
                        f"({name(hist.gl_account)}). Conflicting signals — review suggested."))

        if context.vendor_default_gl:
            gl = context.vendor_default_gl if kw in (None, context.vendor_default_gl) else kw
            conf = 0.90 if kw == context.vendor_default_gl else (0.86 if kw is None else 0.78)
            return ClassificationProposal(
                gl_account=gl, department=context.vendor_default_department or GL_DEFAULT_DEPARTMENT.get(gl, "Operations"),
                location=ship_loc or HQ_LOCATION, confidence=conf,
                reason=f"{name(gl)} based on vendor default coding (no invoice history).")

        if kw:
            return ClassificationProposal(
                gl_account=kw, department=GL_DEFAULT_DEPARTMENT.get(kw, "Operations"),
                location=ship_loc or HQ_LOCATION, confidence=0.80,
                reason=f"{name(kw)} inferred from invoice description only (new vendor, no history).")

        return ClassificationProposal(
            gl_account=FALLBACK_GL, department="Operations", location=ship_loc or HQ_LOCATION,
            confidence=0.55,
            reason="No vendor history, vendor default, or descriptive keywords; generic services account proposed.")

    # --- explanations ------------------------------------------------------

    def explain_exception(self, exception: ExceptionRecord) -> str:
        ev = exception.evidence
        t = exception.type
        if t == ExceptionType.PRICE_VARIANCE:
            return (f"The invoice is ${ev.get('variance')} above the PO price for the same quantities "
                    f"(${ev.get('invoice_amount')} vs ${ev.get('po_amount')}), exceeding the ${ev.get('tolerance')} "
                    "tolerance. Likely an unapproved price increase.")
        if t == ExceptionType.QUANTITY_VARIANCE:
            if ev.get("basis") == "received":
                return (f"Vendor billed {ev.get('invoiced')} units but only {ev.get('received')} were received. "
                        "Paying now would pay for goods not yet delivered.")
            if ev.get("basis") == "ordered":
                return (f"Vendor billed {ev.get('invoiced')} units against {ev.get('ordered')} ordered "
                        f"(${ev.get('variance_amount')} over the PO).")
            return "One or more invoice lines do not correspond to any PO line."
        if t == ExceptionType.DUPLICATE_INVOICE:
            return (f"{ev.get('confidence', '').title()}-confidence match with {ev.get('original_invoice_id')} "
                    f"({ev.get('original_invoice_number')}): " + "; ".join(ev.get("reasons", [])) + ".")
        if t == ExceptionType.LOW_CODING_CONFIDENCE:
            return f"The coding proposal has weak supporting evidence ({ev.get('confidence')}). A human should confirm the account."
        if t == ExceptionType.VENDOR_AMBIGUITY:
            return ev.get("summary") or "The invoice vendor could not be uniquely identified."
        if t in (ExceptionType.INVALID_ACCOUNT, ExceptionType.INVALID_DIMENSION):
            return f"The proposed coding references a value that fails master-data validation: {exception.message}"
        if t == ExceptionType.MISSING_PO:
            return "This vendor is set up as PO-required, but the invoice does not reference a valid PO."
        if t == ExceptionType.MISSING_RECEIPT:
            return "The PO requires a three-way match, but no goods receipt has been recorded yet."
        return exception.message
