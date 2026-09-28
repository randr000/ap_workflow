"""Deterministic duplicate-invoice detection.

Confidence levels:
    HIGH   — same vendor + same invoice number (after normalization)
    MEDIUM — same vendor + same amount + similar invoice number or same invoice date
    LOW    — same vendor + same amount within the look-back window
Low-confidence candidates are flagged for review, never auto-rejected.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.domain.invoice import DuplicateCandidate, DuplicateConfidence, Invoice
from app.domain.money import money
from app.domain.workflow import WorkflowState

_RANK = {DuplicateConfidence.HIGH: 3, DuplicateConfidence.MEDIUM: 2, DuplicateConfidence.LOW: 1}


def normalize_invoice_number(number: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", number.upper())


def similar_invoice_numbers(a: str, b: str) -> bool:
    na, nb = normalize_invoice_number(a), normalize_invoice_number(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.9


def assess_pair(invoice: Invoice, other: Invoice, window_days: int) -> DuplicateCandidate | None:
    if not invoice.vendor_id or invoice.vendor_id != other.vendor_id:
        return None
    same_number = normalize_invoice_number(invoice.invoice_number) == normalize_invoice_number(other.invoice_number)
    same_amount = money(invoice.total) == money(other.total)
    similar_number = similar_invoice_numbers(invoice.invoice_number, other.invoice_number)
    same_date = invoice.invoice_date == other.invoice_date
    within_window = abs((invoice.invoice_date - other.invoice_date).days) <= window_days
    same_po = bool(invoice.po_number) and invoice.po_number == other.po_number

    reasons = ["Same vendor"]
    if same_number:
        confidence = DuplicateConfidence.HIGH
        reasons.append(f"Same invoice number ({other.invoice_number})")
    elif same_amount and (similar_number or same_date):
        confidence = DuplicateConfidence.MEDIUM
        if similar_number:
            reasons.append(f"Similar invoice number ({invoice.invoice_number} vs {other.invoice_number})")
        if same_date:
            reasons.append("Same invoice date")
    elif same_amount and within_window:
        confidence = DuplicateConfidence.LOW
    else:
        return None
    if same_amount:
        reasons.append(f"Same amount ({money(other.total)})")
    if same_po:
        reasons.append(f"Same PO ({other.po_number})")

    return DuplicateCandidate(
        invoice_id=other.id, invoice_number=other.invoice_number, vendor_id=other.vendor_id,
        invoice_date=other.invoice_date, total=other.total, po_number=other.po_number,
        status=other.status, confidence=confidence, reasons=reasons)


def find_duplicates(invoice: Invoice, existing: list[Invoice], window_days: int = 60) -> list[DuplicateCandidate]:
    """Compare against previously received invoices (rejected invoices are ignored)."""
    candidates = []
    for other in existing:
        if other.id == invoice.id or other.status == WorkflowState.REJECTED:
            continue
        candidate = assess_pair(invoice, other, window_days)
        if candidate:
            candidates.append(candidate)
    return sorted(candidates, key=lambda c: -_RANK[c.confidence])
