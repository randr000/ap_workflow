"""Accounting domain: chart of accounts, periods, coding proposals, journal
entries and validation reports."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.money import ZERO


class AccountType(StrEnum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class Account(BaseModel):
    number: str
    name: str
    type: AccountType
    active: bool = True

    @property
    def label(self) -> str:
        return f"{self.number} - {self.name}"


class PeriodStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class AccountingPeriod(BaseModel):
    period: str  # "YYYY-MM"
    start_date: date
    end_date: date
    status: PeriodStatus

    def contains(self, d: date) -> bool:
        return self.start_date <= d <= self.end_date


class HistoricalCoding(BaseModel):
    vendor_id: str
    gl_account: str
    department: str
    location: str
    count: int


# ---------------------------------------------------------------------------
# AI coding contract
# ---------------------------------------------------------------------------


class ClassificationContext(BaseModel):
    """Everything the classifier is allowed to see. Built deterministically."""

    vendor_id: str
    vendor_name: str
    vendor_default_gl: str | None = None
    vendor_default_department: str | None = None
    descriptions: list[str]
    ship_to: str | None = None
    history: list[HistoricalCoding] = Field(default_factory=list)
    po_coding: list[dict] = Field(default_factory=list)
    accounts: list[Account] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class ClassificationProposal(BaseModel):
    """Structured AI output for GL / dimension coding. Validated deterministically
    against master data before it is applied to the invoice."""

    gl_account: str = Field(min_length=1)
    department: str | None = None
    location: str | None = None
    class_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=500)
    source: str = "AI"          # "AI" or "HUMAN" (after a coding correction)
    corrected_by: str | None = None


# ---------------------------------------------------------------------------
# Journal entries
# ---------------------------------------------------------------------------


class JournalEntryStatus(StrEnum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    POSTED = "POSTED"


class JournalEntryLine(BaseModel):
    account: str | None
    department: str | None = None
    location: str | None = None
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    memo: str = ""


class JournalEntry(BaseModel):
    id: str
    invoice_id: str
    date: date
    description: str
    lines: list[JournalEntryLine]
    status: JournalEntryStatus = JournalEntryStatus.PROPOSED
    created_by: str = "SYSTEM"
    created_at: datetime = Field(default_factory=datetime.now)
    posted_at: datetime | None = None

    @property
    def total_debits(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), ZERO)

    @property
    def total_credits(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), ZERO)


class ValidationCheck(BaseModel):
    rule: str
    passed: bool
    detail: str


class ValidationReport(BaseModel):
    checks: list[ValidationCheck]
    validated_at: datetime = Field(default_factory=datetime.now)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[ValidationCheck]:
        return [c for c in self.checks if not c.passed]


class PostingReceipt(BaseModel):
    """What the ledger (or, later, an ERP) returns after a successful post."""

    journal_entry_id: str
    invoice_id: str
    posting_date: date
    posted_at: datetime
    ledger: str
    reference: str
