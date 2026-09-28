"""InvoiceCase — the aggregate that carries an invoice through the AP workflow,
together with every AI proposal and deterministic result produced along the way."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.accounting import ClassificationProposal, JournalEntry, ValidationReport
from app.domain.approval import ApprovalRoute
from app.domain.exception import ExceptionRecord
from app.domain.invoice import DuplicateCandidate, Invoice, InvoiceExtraction
from app.domain.purchase_order import MatchResult
from app.domain.vendor import VendorMatch
from app.domain.workflow import StateChange, WorkflowState


class IntakeMethod(StrEnum):
    AI_EXTRACTION = "AI_EXTRACTION"          # document text read by the AI provider
    MANUAL_ENTRY = "MANUAL_ENTRY"            # details typed in by a user
    STRUCTURED_UPLOAD = "STRUCTURED_UPLOAD"  # JSON file with invoice fields


class InvoiceCase(BaseModel):
    invoice: Invoice
    document: str
    intake: IntakeMethod = IntakeMethod.AI_EXTRACTION
    ai_provider: str | None = None  # provider that extracted and/or coded this invoice
    extraction: InvoiceExtraction | None = None
    vendor_match: VendorMatch | None = None
    classification: ClassificationProposal | None = None
    ai_classification: ClassificationProposal | None = None  # original AI proposal, kept after human correction
    coding_issues: list[str] = Field(default_factory=list)
    match_result: MatchResult | None = None
    duplicates: list[DuplicateCandidate] = Field(default_factory=list)
    exceptions: list[ExceptionRecord] = Field(default_factory=list)
    approval_route: ApprovalRoute | None = None
    journal_entry: JournalEntry | None = None
    validation: ValidationReport | None = None
    state_history: list[StateChange] = Field(default_factory=list)
    straight_through: bool = False

    @property
    def id(self) -> str:
        return self.invoice.id

    @property
    def status(self) -> WorkflowState:
        return self.invoice.status

    @property
    def open_exceptions(self) -> list[ExceptionRecord]:
        return [e for e in self.exceptions if e.is_open]

    @property
    def blocking_exceptions(self) -> list[ExceptionRecord]:
        return [e for e in self.exceptions if e.is_open and e.blocking]
