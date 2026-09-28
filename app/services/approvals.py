"""Deterministic approval routing.

Rules (thresholds configurable in app.config):
    total < $1,000 AND no exceptions AND coding confidence >= 90%  -> auto-approval eligible
    $1,000 – $9,999.99                                              -> Department Manager
    >= $10,000                                                      -> Controller
    PO mismatch (price / quantity / receipt)                        -> Procurement Manager
    Duplicate candidate / vendor ambiguity                          -> AP Exception Queue
    Low AI confidence / invalid coding / missing PO                 -> AP Review Queue
    Missing PO on a PO-required vendor                              -> + Controller
Controller approval supersedes Department Manager approval.
"""

from __future__ import annotations

from app.config import ApprovalThresholds, ConfidenceThresholds
from app.domain.approval import ROLE_ORDER, ApprovalRoute, ApprovalStep, ApproverRole
from app.domain.exception import ExceptionRecord, ExceptionType
from app.domain.invoice import Invoice
from app.domain.money import fmt_money


def route_invoice(
    invoice: Invoice,
    open_exceptions: list[ExceptionRecord],
    coding_confidence: float | None,
    po_matched: bool | None,
    approvals: ApprovalThresholds,
    confidence: ConfidenceThresholds,
) -> ApprovalRoute:
    reasons: dict[ApproverRole, list[str]] = {}

    def add(role: ApproverRole, reason: str) -> None:
        reasons.setdefault(role, [])
        if reason not in reasons[role]:
            reasons[role].append(reason)

    amount = invoice.total
    department = invoice.lines[0].department if invoice.lines else None

    if amount >= approvals.controller_threshold:
        add(ApproverRole.CONTROLLER,
            f"Invoice amount {fmt_money(amount)} is at or above the {fmt_money(approvals.controller_threshold)} Controller threshold.")
    elif amount >= approvals.auto_approval_limit:
        add(ApproverRole.DEPARTMENT_MANAGER,
            f"Invoice amount {fmt_money(amount)} requires budget-owner approval"
            + (f" ({department})." if department else "."))

    for exc in open_exceptions:
        add(exc.route_to, f"{exc.type}: {exc.message}")
        if exc.type == ExceptionType.MISSING_PO:
            add(ApproverRole.CONTROLLER, "Non-PO spend from a PO-required vendor requires Controller sign-off.")

    low_confidence = coding_confidence is None or coding_confidence < confidence.straight_through
    has_low_conf_exception = any(e.type == ExceptionType.LOW_CODING_CONFIDENCE for e in open_exceptions)
    if low_confidence and not has_low_conf_exception and coding_confidence is not None:
        add(ApproverRole.AP_REVIEW_QUEUE,
            f"AI coding confidence {coding_confidence:.0%} is below the "
            f"{confidence.straight_through:.0%} straight-through threshold.")

    if ApproverRole.CONTROLLER in reasons and ApproverRole.DEPARTMENT_MANAGER in reasons:
        reasons.pop(ApproverRole.DEPARTMENT_MANAGER)

    auto_eligible = (amount < approvals.auto_approval_limit and not open_exceptions
                     and not low_confidence)

    rationale = [
        f"Invoice amount: {fmt_money(amount)}",
        "PO match: " + ("N/A (non-PO invoice)" if po_matched is None else ("Yes" if po_matched else "No")),
        "Exceptions: " + (", ".join(sorted({e.type for e in open_exceptions})) or "None"),
        "AI coding confidence: " + (f"{coding_confidence:.0%}" if coding_confidence is not None else "n/a"),
    ]
    if auto_eligible:
        rationale.append(
            f"Auto-approval eligible: under {fmt_money(approvals.auto_approval_limit)}, no exceptions, "
            f"confidence ≥ {confidence.straight_through:.0%}.")

    steps = [ApprovalStep(role=role, reason=" ".join(reasons[role])) for role in ROLE_ORDER if role in reasons]
    return ApprovalRoute(auto_approval_eligible=auto_eligible, steps=steps, rationale=rationale)
