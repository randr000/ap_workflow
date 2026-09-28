"""Deterministic PO matching (two-way and three-way) with tolerance rules.

No AI is involved in any calculation here. Line pairing uses a deterministic
description-similarity heuristic; every quantity, price and variance figure is
computed with Decimal arithmetic.
"""

from __future__ import annotations

import re
from decimal import Decimal
from difflib import SequenceMatcher

from app.config import Tolerances
from app.domain.exception import ExceptionType
from app.domain.invoice import Invoice, InvoiceLine
from app.domain.money import ZERO, money
from app.domain.purchase_order import LineMatch, MatchIssue, MatchResult, MatchType, POLine, PurchaseOrder
from app.domain.reference import ReferenceData

LINE_SIMILARITY_THRESHOLD = 0.6


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def description_similarity(a: str, b: str) -> float:
    """Max of character-sequence similarity and token overlap (order-insensitive)."""
    na, nb = _norm(a), _norm(b)
    ta, tb = set(na.split()), set(nb.split())
    token_score = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return max(SequenceMatcher(None, na, nb).ratio(), token_score)


def effective_price_tolerance(base_amount: Decimal, tol: Tolerances) -> Decimal:
    """Lesser of the fixed amount and the percentage of the PO-priced amount."""
    pct_limit = money(abs(base_amount) * tol.price_percent / Decimal("100"))
    return min(money(tol.price_amount), pct_limit)


def within_price_tolerance(variance: Decimal, base_amount: Decimal, tol: Tolerances) -> bool:
    return abs(variance) <= effective_price_tolerance(base_amount, tol)


def pair_lines(invoice_lines: list[InvoiceLine], po_lines: list[POLine]) -> dict[int, int | None]:
    """Map invoice line -> PO line by best description similarity (each PO line used once).
    Falls back to positional pairing when line counts agree."""
    pairs: dict[int, int | None] = {}
    used: set[int] = set()
    for inv_line in invoice_lines:
        best, best_score = None, 0.0
        for po_line in po_lines:
            if po_line.line_number in used:
                continue
            score = description_similarity(inv_line.description, po_line.description)
            if score > best_score:
                best, best_score = po_line, score
        if best is not None and best_score >= LINE_SIMILARITY_THRESHOLD:
            pairs[inv_line.line_number] = best.line_number
            used.add(best.line_number)
        else:
            pairs[inv_line.line_number] = None
    if len(invoice_lines) == len(po_lines):
        for inv_line, po_line in zip(invoice_lines, po_lines):
            if pairs[inv_line.line_number] is None and po_line.line_number not in used:
                pairs[inv_line.line_number] = po_line.line_number
                used.add(po_line.line_number)
    return pairs


def match_invoice(invoice: Invoice, po: PurchaseOrder, reference: ReferenceData,
                  tolerances: Tolerances) -> MatchResult:
    three_way = po.match_type == MatchType.THREE_WAY
    receipt_found = bool(reference.receipts_for(po.po_number))
    pairs = pair_lines(invoice.lines, po.lines)
    po_by_no = {ln.line_number: ln for ln in po.lines}

    line_results: list[LineMatch] = []
    issues: list[MatchIssue] = []

    vendor_matches = invoice.vendor_id == po.vendor_id
    if not vendor_matches:
        issues.append(MatchIssue(
            type=ExceptionType.VENDOR_MISMATCH,
            message=f"Invoice vendor {invoice.vendor_id} does not match PO vendor {po.vendor_id}.",
            evidence={"invoice_vendor": invoice.vendor_id, "po_vendor": po.vendor_id,
                      "po_number": po.po_number}))

    total_variance = ZERO
    total_base = ZERO
    qty_ordered_issues, qty_received_issues, unmatched = [], [], []

    for inv_line in invoice.lines:
        po_line = po_by_no.get(pairs[inv_line.line_number])
        if po_line is None:
            unmatched.append(inv_line.description)
            line_results.append(LineMatch(
                invoice_line=inv_line.line_number, po_line=None, description=inv_line.description,
                ordered_quantity=ZERO, received_quantity=None, invoiced_quantity=inv_line.quantity,
                po_unit_price=ZERO, invoice_unit_price=inv_line.unit_price, price_variance=ZERO,
                quantity_variance_ordered=inv_line.quantity, quantity_variance_received=None))
            continue

        received = reference.received_quantity(po.po_number, po_line.line_number) if three_way else None
        price_variance = money((inv_line.unit_price - po_line.unit_price) * inv_line.quantity)
        qty_var_ordered = inv_line.quantity - po_line.quantity
        qty_var_received = inv_line.quantity - received if received is not None else None

        total_variance += abs(price_variance)
        total_base += money(po_line.unit_price * inv_line.quantity)

        if qty_var_ordered > tolerances.quantity:
            qty_ordered_issues.append((inv_line, po_line, qty_var_ordered))
        if three_way and receipt_found and qty_var_received is not None and qty_var_received > tolerances.quantity:
            qty_received_issues.append((inv_line, po_line, received, qty_var_received))

        line_results.append(LineMatch(
            invoice_line=inv_line.line_number, po_line=po_line.line_number,
            description=inv_line.description, ordered_quantity=po_line.quantity,
            received_quantity=received, invoiced_quantity=inv_line.quantity,
            po_unit_price=po_line.unit_price, invoice_unit_price=inv_line.unit_price,
            price_variance=price_variance, quantity_variance_ordered=qty_var_ordered,
            quantity_variance_received=qty_var_received))

    tolerance = effective_price_tolerance(total_base, tolerances)
    if total_variance > tolerance:
        issues.append(MatchIssue(
            type=ExceptionType.PRICE_VARIANCE,
            message="Invoice price variance exceeds configured tolerance.",
            evidence={
                "po_number": po.po_number,
                "invoice_amount": str(money(invoice.subtotal)),
                "po_amount": str(total_base),
                "variance": str(money(total_variance)),
                "tolerance": str(tolerance),
                "tolerance_rule": (f"lesser of ${tolerances.price_amount} or "
                                   f"{tolerances.price_percent}% of PO amount"),
                "lines": [{"line": lm.invoice_line, "po_price": str(lm.po_unit_price),
                           "invoice_price": str(lm.invoice_unit_price),
                           "variance": str(lm.price_variance)}
                          for lm in line_results if lm.price_variance != 0],
            }))

    for inv_line, po_line, var in qty_ordered_issues:
        issues.append(MatchIssue(
            type=ExceptionType.QUANTITY_VARIANCE,
            message=f"Invoiced quantity exceeds quantity ordered on {po.po_number}.",
            evidence={"po_number": po.po_number, "basis": "ordered", "description": inv_line.description,
                      "ordered": str(po_line.quantity), "invoiced": str(inv_line.quantity),
                      "variance_units": str(var), "variance_amount": str(money(var * po_line.unit_price)),
                      "tolerance_units": str(tolerances.quantity)}))

    for inv_line, po_line, received, var in qty_received_issues:
        issues.append(MatchIssue(
            type=ExceptionType.QUANTITY_VARIANCE,
            message=f"Three-way match failed: invoiced quantity exceeds quantity received on {po.po_number}.",
            evidence={"po_number": po.po_number, "basis": "received", "description": inv_line.description,
                      "ordered": str(po_line.quantity), "received": str(received),
                      "invoiced": str(inv_line.quantity), "variance_units": str(var),
                      "variance_amount": str(money(var * po_line.unit_price)),
                      "tolerance_units": str(tolerances.quantity)}))

    if unmatched:
        issues.append(MatchIssue(
            type=ExceptionType.QUANTITY_VARIANCE,
            message=f"Invoice line(s) not found on {po.po_number}.",
            evidence={"po_number": po.po_number, "basis": "not_on_po", "lines": unmatched}))

    if three_way and not receipt_found:
        issues.append(MatchIssue(
            type=ExceptionType.MISSING_RECEIPT,
            message=f"Three-way match required but no goods receipt exists for {po.po_number}.",
            evidence={"po_number": po.po_number, "match_type": po.match_type}))

    return MatchResult(
        po_number=po.po_number, match_type=po.match_type, vendor_matches=vendor_matches,
        lines=line_results, total_price_variance=money(total_variance), price_tolerance=tolerance,
        receipt_found=receipt_found, issues=issues)
