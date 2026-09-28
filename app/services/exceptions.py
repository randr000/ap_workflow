"""Exception engine: turns deterministic findings into structured exceptions.

The policy table is the single place that defines, per exception type, its
default severity, who owns it, whether it blocks approval, and the recommended
action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.approval import ApproverRole
from app.domain.exception import ExceptionRecord, ExceptionType, Severity


@dataclass(frozen=True)
class ExceptionPolicy:
    severity: Severity
    route_to: ApproverRole
    blocking: bool
    recommended_action: str


T, R = ExceptionType, ApproverRole

EXCEPTION_POLICY: dict[ExceptionType, ExceptionPolicy] = {
    T.PRICE_VARIANCE: ExceptionPolicy(
        Severity.MEDIUM, R.PROCUREMENT_MANAGER, False,
        "Route to Procurement Manager to accept the price change or request a credit memo."),
    T.QUANTITY_VARIANCE: ExceptionPolicy(
        Severity.MEDIUM, R.PROCUREMENT_MANAGER, False,
        "Route to Procurement Manager to confirm quantities; request a credit memo for units not ordered/received."),
    T.MISSING_RECEIPT: ExceptionPolicy(
        Severity.MEDIUM, R.PROCUREMENT_MANAGER, False,
        "Confirm goods were received (post the receipt) before approving payment."),
    T.VENDOR_MISMATCH: ExceptionPolicy(
        Severity.HIGH, R.PROCUREMENT_MANAGER, True,
        "Invoice vendor differs from PO vendor. Reject and request a corrected invoice."),
    T.MISSING_PO: ExceptionPolicy(
        Severity.MEDIUM, R.AP_REVIEW_QUEUE, False,
        "Vendor requires a PO. AP to obtain a PO number or escalate to Controller for a non-PO exception."),
    T.VENDOR_AMBIGUITY: ExceptionPolicy(
        Severity.HIGH, R.AP_EXCEPTION_QUEUE, True,
        "Human review required: confirm the vendor in the vendor master (possible duplicate vendor records)."),
    T.DUPLICATE_INVOICE: ExceptionPolicy(
        Severity.HIGH, R.AP_EXCEPTION_QUEUE, True,
        "Compare with the original invoice. Reject if duplicate; dismiss only with a documented reason."),
    T.LOW_CODING_CONFIDENCE: ExceptionPolicy(
        Severity.MEDIUM, R.AP_REVIEW_QUEUE, False,
        "AP Review to confirm or correct the proposed GL account and dimensions."),
    T.INVALID_ACCOUNT: ExceptionPolicy(
        Severity.HIGH, R.AP_REVIEW_QUEUE, True,
        "Correct the GL account to an active account in the chart of accounts."),
    T.INVALID_DIMENSION: ExceptionPolicy(
        Severity.HIGH, R.AP_REVIEW_QUEUE, True,
        "Correct the department/location to a valid dimension value."),
    T.INVOICE_ARITHMETIC_ERROR: ExceptionPolicy(
        Severity.HIGH, R.AP_EXCEPTION_QUEUE, True,
        "Invoice does not foot. Reject and request a corrected invoice from the vendor."),
    T.ACCOUNTING_VALIDATION_ERROR: ExceptionPolicy(
        Severity.HIGH, R.CONTROLLER, True,
        "Posting blocked by accounting validation. Correct the entry or reject the invoice."),
}

# Duplicate severity follows detection confidence; only HIGH duplicates block approval.
DUPLICATE_SEVERITY = {"HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM, "LOW": Severity.LOW}


def new_exception(invoice_id: str, seq: int, type: ExceptionType, message: str,
                  evidence: dict[str, Any] | None = None, severity: Severity | None = None,
                  blocking: bool | None = None) -> ExceptionRecord:
    policy = EXCEPTION_POLICY[type]
    return ExceptionRecord(
        id=f"EX-{invoice_id.removeprefix('AP-')}-{seq:02d}",
        invoice_id=invoice_id,
        type=type,
        severity=severity or policy.severity,
        message=message,
        evidence=evidence or {},
        recommended_action=policy.recommended_action,
        route_to=policy.route_to,
        blocking=policy.blocking if blocking is None else blocking,
    )
