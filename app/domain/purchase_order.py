"""Purchase orders and PO-matching results."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MatchType(StrEnum):
    TWO_WAY = "TWO_WAY"      # invoice vs PO (typical for services)
    THREE_WAY = "THREE_WAY"  # invoice vs PO vs receipt (typical for goods)


class POLine(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    received_quantity: Decimal = Decimal("0")
    gl_account: str | None = None
    department: str | None = None
    class_name: str | None = Field(default=None, alias="class")
    location: str | None = None


class PurchaseOrder(BaseModel):
    id: str
    po_number: str
    vendor_id: str
    po_date: date
    currency: str = "USD"
    status: str = "OPEN"
    match_type: MatchType = MatchType.TWO_WAY
    lines: list[POLine]

    @property
    def total(self) -> Decimal:
        return sum((ln.amount for ln in self.lines), Decimal("0"))


class LineMatch(BaseModel):
    invoice_line: int
    po_line: int | None
    description: str
    ordered_quantity: Decimal
    received_quantity: Decimal | None  # None for two-way matches
    invoiced_quantity: Decimal
    po_unit_price: Decimal
    invoice_unit_price: Decimal
    price_variance: Decimal            # (invoice price - PO price) x invoiced qty
    quantity_variance_ordered: Decimal
    quantity_variance_received: Decimal | None


class MatchIssue(BaseModel):
    """A deterministic matching failure. Converted into an ExceptionRecord by the
    exception engine."""

    type: str
    message: str
    evidence: dict = Field(default_factory=dict)


class MatchResult(BaseModel):
    po_number: str
    match_type: MatchType
    vendor_matches: bool
    lines: list[LineMatch]
    total_price_variance: Decimal
    price_tolerance: Decimal
    receipt_found: bool
    issues: list[MatchIssue] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues
