"""Structured AP exceptions."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.approval import ApproverRole


class ExceptionType(StrEnum):
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QUANTITY_VARIANCE = "QUANTITY_VARIANCE"
    MISSING_PO = "MISSING_PO"
    VENDOR_AMBIGUITY = "VENDOR_AMBIGUITY"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    LOW_CODING_CONFIDENCE = "LOW_CODING_CONFIDENCE"
    MISSING_RECEIPT = "MISSING_RECEIPT"
    INVALID_ACCOUNT = "INVALID_ACCOUNT"
    INVALID_DIMENSION = "INVALID_DIMENSION"
    VENDOR_MISMATCH = "VENDOR_MISMATCH"
    INVOICE_ARITHMETIC_ERROR = "INVOICE_ARITHMETIC_ERROR"
    ACCOUNTING_VALIDATION_ERROR = "ACCOUNTING_VALIDATION_ERROR"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ExceptionStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ExceptionRecord(BaseModel):
    id: str
    invoice_id: str
    type: ExceptionType
    severity: Severity
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: ExceptionStatus = ExceptionStatus.OPEN
    recommended_action: str
    route_to: ApproverRole
    # Blocking exceptions cannot be cleared by approving the invoice; they need a
    # specific corrective action (fix coding, confirm vendor, dismiss, or reject).
    blocking: bool = False
    explanation: str | None = None  # concise AI explanation (no chain-of-thought)
    resolution: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def is_open(self) -> bool:
        return self.status == ExceptionStatus.OPEN
