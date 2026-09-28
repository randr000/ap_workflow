"""Read-only metrics for the dashboard, the API and the synthetic benchmark."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from app.domain.case import InvoiceCase
from app.domain.money import ZERO
from app.domain.reference import AP_CONTROL_ACCOUNT
from app.domain.workflow import WorkflowState


def dashboard_metrics(cases: list[InvoiceCase], ledger_rows: list[dict]) -> dict:
    processed = len(cases)
    stp = sum(1 for c in cases if c.straight_through)
    posted_amount = sum((r["credit"] for r in ledger_rows if r["account"] == AP_CONTROL_ACCOUNT), ZERO)
    detected = Counter(e.type.value for c in cases for e in c.exceptions)
    open_ = Counter(e.type.value for c in cases for e in c.exceptions if e.is_open)
    return {
        "invoices_processed": processed,
        "straight_through": stp,
        "straight_through_rate": (stp / processed) if processed else 0.0,
        "open_exceptions": sum(open_.values()),
        "invoices_with_exceptions": sum(1 for c in cases if c.exceptions),
        "pending_approvals": sum(1 for c in cases if c.status == WorkflowState.PENDING_APPROVAL),
        "ready_to_post": sum(1 for c in cases if c.status == WorkflowState.VALIDATED),
        "posted_count": sum(1 for c in cases if c.status == WorkflowState.POSTED),
        "posted_amount": Decimal(posted_amount),
        "total_amount": sum((c.invoice.total for c in cases), ZERO),
        "exceptions_detected": dict(detected.most_common()),
        "exceptions_open": dict(open_.most_common()),
        "status_counts": dict(Counter(c.status.value for c in cases)),
    }
