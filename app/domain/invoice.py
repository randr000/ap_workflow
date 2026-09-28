"""Invoice domain model, the AI extraction contract, and duplicate candidates."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.domain.money import money
from app.domain.workflow import WorkflowState


class InvoiceLine(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    gl_account: str | None = None
    department: str | None = None
    class_name: str | None = Field(default=None, alias="class")
    location: str | None = None


class Invoice(BaseModel):
    id: str
    invoice_number: str
    vendor_id: str | None
    vendor_name: str
    invoice_date: date
    due_date: date | None = None
    po_number: str | None = None
    currency: str = "USD"
    subtotal: Decimal
    tax: Decimal = Decimal("0.00")
    total: Decimal
    status: WorkflowState = WorkflowState.RECEIVED
    lines: list[InvoiceLine]
    source: str = "demo"
    created_at: datetime = Field(default_factory=datetime.now)

    def arithmetic_issues(self) -> list[str]:
        """Deterministic arithmetic checks on the (extracted) invoice document."""
        issues: list[str] = []
        for line in self.lines:
            expected = money(line.quantity * line.unit_price)
            if money(line.amount) != expected:
                issues.append(
                    f"Line {line.line_number}: {line.quantity} x {line.unit_price} = {expected}, "
                    f"but line amount is {line.amount}."
                )
        line_sum = money(sum((ln.amount for ln in self.lines), Decimal("0")))
        if line_sum != money(self.subtotal):
            issues.append(f"Line amounts sum to {line_sum}, but subtotal is {self.subtotal}.")
        if money(self.subtotal + self.tax) != money(self.total):
            issues.append(
                f"Subtotal {self.subtotal} + tax {self.tax} = {money(self.subtotal + self.tax)}, "
                f"but total is {self.total}."
            )
        return issues


# LLM JSON numbers arrive as floats; convert via str so no binary artifacts reach Decimal.
LLMDecimal = Annotated[Decimal, BeforeValidator(lambda v: str(v) if isinstance(v, float) else v)]


class ExtractedLine(BaseModel):
    description: str
    quantity: LLMDecimal
    unit_price: LLMDecimal
    amount: LLMDecimal


class InvoiceExtraction(BaseModel):
    """Structured output contract for AI invoice extraction.

    Whatever the LLM returns is parsed into this model; anything that does not
    validate is rejected before it reaches the workflow.
    """

    vendor_name: str = Field(min_length=1)
    vendor_tax_id: str | None = None
    invoice_number: str = Field(min_length=1)
    invoice_date: date
    due_date: date | None = None
    po_number: str | None = None
    currency: str = "USD"
    ship_to: str | None = None
    subtotal: LLMDecimal
    tax: LLMDecimal = Decimal("0.00")
    total: LLMDecimal
    lines: list[ExtractedLine] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)

    @field_validator("po_number", "vendor_tax_id", "ship_to", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and v.strip().lower() in {"", "none", "n/a", "null"}:
            return None
        return v


class DuplicateConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class DuplicateCandidate(BaseModel):
    invoice_id: str
    invoice_number: str
    vendor_id: str
    invoice_date: date
    total: Decimal
    po_number: str | None = None
    status: str
    confidence: DuplicateConfidence
    reasons: list[str]
