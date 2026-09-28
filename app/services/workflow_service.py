"""AP workflow orchestration.

This is the only component that changes invoice state. It calls the AI layer for
proposals, runs the deterministic controls, enforces the state machine and
permissions, writes the audit trail, and — only after validation passes — posts
through the ledger gateway.

    AI proposes and reasons. Deterministic application logic validates and controls.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from app.ai.provider import AIProviderError, LLMProvider, create_provider
from app.config import Settings, load_settings
from app.db.database import init_db, make_engine, make_session_factory
from app.db.repository import Repository
from app.db.seed import seed_repository
from app.domain.accounting import ClassificationProposal, JournalEntryStatus, ValidationReport
from app.domain.approval import ApproverRole, StepStatus, User
from app.domain.case import IntakeMethod, InvoiceCase
from app.domain.exception import ExceptionStatus, ExceptionType
from app.domain.invoice import DuplicateConfidence, InvoiceExtraction
from app.domain.vendor import VendorMatchStatus
from app.domain.workflow import (
    TERMINAL_STATES,
    ActorType,
    AuditEvent,
    StateChange,
    WorkflowState,
    assert_transition,
)
from app.services.approvals import route_invoice
from app.services.classification import (
    ConfidenceBand,
    apply_coding,
    build_context,
    confidence_band,
    validate_coding,
)
from app.services.duplicate_detection import find_duplicates
from app.services.exceptions import DUPLICATE_SEVERITY, new_exception
from app.services.extraction import build_invoice, extract, render_details
from app.services.journal_entries import build_journal_entry
from app.services.ledger import InternalLedger, LedgerGateway
from app.services.matching import match_invoice
from app.services.validation import validate_for_posting
from app.services.vendor_matching import identify_vendor

CODING_EXCEPTIONS = {ExceptionType.INVALID_ACCOUNT, ExceptionType.INVALID_DIMENSION,
                     ExceptionType.LOW_CODING_CONFIDENCE}


class WorkflowError(Exception):
    """A workflow rule prevented the requested action."""


class PostingBlockedError(WorkflowError):
    def __init__(self, message: str, report: ValidationReport | None = None):
        super().__init__(message)
        self.report = report


class APWorkflowService:
    def __init__(self, repo: Repository, provider: LLMProvider, settings: Settings,
                 ledger: LedgerGateway | None = None):
        self.repo = repo
        self.provider = provider
        self.settings = settings
        self.ledger = ledger or InternalLedger(repo)
        self.reference = repo.load_reference()

    # ------------------------------------------------------------------ helpers

    def _audit(self, invoice_id: str | None, actor_type: ActorType, actor: str, action: str, **details) -> None:
        self.repo.add_audit(AuditEvent(invoice_id=invoice_id, actor_type=actor_type, actor=actor,
                                       action=action, details=details))

    def _transition(self, case: InvoiceCase, target: WorkflowState, actor: str = "SYSTEM") -> None:
        assert_transition(case.status, target)
        case.state_history.append(StateChange(from_state=case.status, to_state=target, actor=actor))
        case.invoice.status = target

    def _add_exception(self, case: InvoiceCase, type_: ExceptionType, message: str,
                       evidence: dict | None = None, **overrides) -> None:
        exc = new_exception(case.id, len(case.exceptions) + 1, type_, message, evidence, **overrides)
        try:
            exc.explanation = self.provider.explain_exception(exc)
        except AIProviderError:
            exc.explanation = None
        case.exceptions.append(exc)
        self._audit(case.id, ActorType.SYSTEM, "Exception Engine",
                    f"{type_.replace('_', ' ').title()} detected",
                    exception_id=exc.id, severity=exc.severity, message=message, route_to=exc.route_to)

    def _vendor_name(self, case: InvoiceCase) -> str:
        vendor = self.reference.vendor(case.invoice.vendor_id)
        return vendor.name if vendor else case.invoice.vendor_name

    def _refresh_journal_entry(self, case: InvoiceCase) -> None:
        if case.classification is None:
            case.journal_entry = None
            return
        case.journal_entry = build_journal_entry(case.invoice, self._vendor_name(case))

    def _resolve(self, case: InvoiceCase, predicate, resolution: str, by: str,
                 status: ExceptionStatus = ExceptionStatus.RESOLVED) -> list[str]:
        resolved = []
        for exc in case.exceptions:
            if exc.is_open and predicate(exc):
                exc.status, exc.resolution, exc.resolved_by, exc.resolved_at = status, resolution, by, datetime.now()
                resolved.append(exc.id)
        return resolved

    # ------------------------------------------------------------- processing

    def process_document(self, document: str, source: str = "upload") -> InvoiceCase:
        """Intake path 1: document text -> AI extraction -> full pipeline."""
        invoice_id = self.repo.next_invoice_id()
        self._audit(invoice_id, ActorType.SYSTEM, "Intake", "Invoice received", source=source)

        try:
            extraction = extract(self.provider, document)
        except AIProviderError as e:
            self._audit(invoice_id, ActorType.AI, self.provider.name, "Extraction failed", error=str(e))
            raise WorkflowError(f"Invoice extraction failed: {e}") from e
        self._audit(invoice_id, ActorType.AI, self.provider.name, "Invoice extracted",
                    vendor=extraction.vendor_name, invoice_number=extraction.invoice_number,
                    total=str(extraction.total), confidence=extraction.confidence)
        return self._process(invoice_id, extraction, document, source, IntakeMethod.AI_EXTRACTION)

    def process_structured(self, extraction: InvoiceExtraction, entered_by: str, source: str = "manual",
                           intake: IntakeMethod = IntakeMethod.MANUAL_ENTRY) -> InvoiceCase:
        """Intake path 2: invoice details typed in by a user or uploaded as JSON.

        No AI extraction happens, but every downstream control runs exactly as for a document:
        arithmetic, vendor identification, AI coding + master-data checks, PO matching,
        duplicates, routing and validation."""
        try:
            extraction = InvoiceExtraction.model_validate(extraction.model_dump())
        except ValidationError as e:
            raise WorkflowError(f"Invalid invoice details: {e.error_count()} error(s)") from e
        invoice_id = self.repo.next_invoice_id()
        self._audit(invoice_id, ActorType.SYSTEM, "Intake", "Invoice received", source=source)
        action = ("Invoice details entered manually" if intake == IntakeMethod.MANUAL_ENTRY
                  else "Structured invoice uploaded")
        self._audit(invoice_id, ActorType.HUMAN, entered_by, action, vendor=extraction.vendor_name,
                    invoice_number=extraction.invoice_number, total=str(extraction.total))
        return self._process(invoice_id, extraction, render_details(extraction), source, intake)

    def _process(self, invoice_id: str, extraction: InvoiceExtraction, document: str, source: str,
                 intake: IntakeMethod) -> InvoiceCase:
        vendor_match = identify_vendor(extraction, self.reference, self.provider)
        invoice = build_invoice(extraction, invoice_id, vendor_match.vendor_id, source)
        case = InvoiceCase(invoice=invoice, document=document, extraction=extraction, vendor_match=vendor_match,
                           intake=intake, ai_provider=self.provider.name)
        self._transition(case, WorkflowState.EXTRACTED)

        if vendor_match.ai_suggestion:
            self._audit(case.id, ActorType.AI, self.provider.name, "Vendor suggested",
                        vendor_id=vendor_match.ai_suggestion, reason=vendor_match.ai_suggestion_reason)
        self._audit(case.id, ActorType.SYSTEM, "Vendor Matching",
                    "Vendor identified" if vendor_match.status == VendorMatchStatus.MATCHED else "Vendor not identified",
                    status=vendor_match.status, method=vendor_match.method, vendor_id=vendor_match.vendor_id)

        for issue in invoice.arithmetic_issues():
            self._add_exception(case, ExceptionType.INVOICE_ARITHMETIC_ERROR,
                                "Invoice does not foot.", {"issue": issue})

        if vendor_match.status != VendorMatchStatus.MATCHED:
            self._add_exception(case, ExceptionType.VENDOR_AMBIGUITY, vendor_match.message, {
                "extracted_vendor_name": extraction.vendor_name,
                "extracted_tax_id": extraction.vendor_tax_id,
                "candidates": [c.model_dump() for c in vendor_match.candidates],
                "ai_suggestion": vendor_match.ai_suggestion,
                "summary": vendor_match.ai_suggestion_reason or vendor_match.message,
            })
            return self._route_and_save(case, po_matched=None)

        vendor = self.reference.vendor(vendor_match.vendor_id)
        self._classify(case, vendor)
        po_matched = self._match_po(case, vendor)
        self._check_duplicates(case)
        return self._route_and_save(case, po_matched)

    def _classify(self, case: InvoiceCase, vendor) -> None:
        invoice = case.invoice
        context = build_context(invoice, vendor, self.reference, ship_to=case.extraction.ship_to)
        try:
            proposal = self.provider.classify_invoice(invoice, context)
            proposal = ClassificationProposal.model_validate(proposal.model_dump() | {"source": "AI"})
        except AIProviderError as e:
            self._audit(case.id, ActorType.AI, self.provider.name, "Classification failed", error=str(e))
            self._add_exception(case, ExceptionType.LOW_CODING_CONFIDENCE,
                                "AI classification unavailable; manual coding required.",
                                {"error": str(e), "confidence": "0%"})
            self._transition(case, WorkflowState.CLASSIFIED)
            return

        case.classification = case.ai_classification = proposal
        self._audit(case.id, ActorType.AI, self.provider.name, "GL account proposed",
                    gl_account=proposal.gl_account, department=proposal.department,
                    location=proposal.location, confidence=proposal.confidence, reason=proposal.reason)
        apply_coding(invoice, proposal)

        issues = validate_coding(proposal, self.reference)
        case.coding_issues = [msg for _, msg, _ in issues]
        for type_, msg, evidence in issues:
            self._add_exception(case, type_, msg, evidence)

        band = confidence_band(proposal.confidence, self.settings.confidence)
        if band == ConfidenceBand.EXCEPTION:
            self._add_exception(
                case, ExceptionType.LOW_CODING_CONFIDENCE,
                f"AI coding confidence {proposal.confidence:.0%} is below the "
                f"{self.settings.confidence.review:.0%} review threshold.",
                {"confidence": f"{proposal.confidence:.0%}", "proposed_account": proposal.gl_account,
                 "reason": proposal.reason})
        self._transition(case, WorkflowState.CLASSIFIED)

    def _match_po(self, case: InvoiceCase, vendor) -> bool | None:
        invoice = case.invoice
        if invoice.po_number:
            po = self.reference.purchase_order(invoice.po_number)
            if po is None:
                self._add_exception(case, ExceptionType.MISSING_PO,
                                    f"Referenced PO {invoice.po_number} does not exist.",
                                    {"po_number": invoice.po_number})
                return False
            result = match_invoice(invoice, po, self.reference, self.settings.tolerances)
            case.match_result = result
            self._transition(case, WorkflowState.MATCHED)
            self._audit(case.id, ActorType.SYSTEM, "PO Matching", "PO matching completed",
                        po_number=po.po_number, match_type=po.match_type, passed=result.passed,
                        price_variance=str(result.total_price_variance), tolerance=str(result.price_tolerance))
            for issue in result.issues:
                self._add_exception(case, ExceptionType(issue.type), issue.message, issue.evidence)
            return result.passed
        if vendor.po_required:
            self._add_exception(case, ExceptionType.MISSING_PO,
                                f"{vendor.name} is a PO-required vendor but the invoice has no PO number.",
                                {"vendor_id": vendor.id, "po_required": True, "amount": str(invoice.total)})
            return False
        self._audit(case.id, ActorType.SYSTEM, "PO Matching", "Non-PO invoice — PO match not required",
                    vendor_id=vendor.id)
        return None

    def _check_duplicates(self, case: InvoiceCase) -> None:
        others = [c.invoice for c in self.repo.list_cases()]
        candidates = find_duplicates(case.invoice, others, self.settings.duplicate_window_days)
        case.duplicates = candidates
        self._audit(case.id, ActorType.SYSTEM, "Duplicate Detection", "Duplicate check completed",
                    candidates=len(candidates))
        if candidates:
            top = candidates[0]
            self._add_exception(
                case, ExceptionType.DUPLICATE_INVOICE,
                f"Possible duplicate of {top.invoice_id} ({top.invoice_number}) — {top.confidence} confidence.",
                {"confidence": top.confidence, "original_invoice_id": top.invoice_id,
                 "original_invoice_number": top.invoice_number, "original_status": top.status,
                 "amount": str(top.total), "reasons": top.reasons,
                 "other_candidates": [c.invoice_id for c in candidates[1:]]},
                severity=DUPLICATE_SEVERITY[top.confidence],
                blocking=top.confidence == DuplicateConfidence.HIGH)

    def _route_and_save(self, case: InvoiceCase, po_matched: bool | None) -> InvoiceCase:
        if case.open_exceptions:
            self._transition(case, WorkflowState.EXCEPTION)
        confidence = case.classification.confidence if case.classification else None
        route = route_invoice(case.invoice, case.open_exceptions, confidence, po_matched,
                              self.settings.approvals, self.settings.confidence)
        case.approval_route = route
        self._refresh_journal_entry(case)
        if case.journal_entry:
            self._audit(case.id, ActorType.SYSTEM, "Accounting", "Journal entry proposed",
                        journal_entry_id=case.journal_entry.id, amount=str(case.invoice.total))

        if route.auto_approval_eligible:
            self._transition(case, WorkflowState.APPROVED)
            self._audit(case.id, ActorType.SYSTEM, "Approval Engine", "Auto-approved per policy",
                        rationale=route.rationale)
            case.straight_through = self._validate(case).passed
        else:
            self._transition(case, WorkflowState.PENDING_APPROVAL)
            self._audit(case.id, ActorType.SYSTEM, "Approval Engine",
                        "Approval routed to " + " → ".join(s.role for s in route.steps),
                        rationale=route.rationale)
        self.repo.save_case(case)
        return case

    def _validate(self, case: InvoiceCase) -> ValidationReport:
        report = validate_for_posting(case.invoice, case.journal_entry, case.approval_route, case.exceptions,
                                      self.reference, self.ledger.has_posting_for(case.id))
        case.validation = report
        if report.passed:
            case.journal_entry.status = JournalEntryStatus.VALIDATED
            self._resolve(case, lambda e: e.type == ExceptionType.ACCOUNTING_VALIDATION_ERROR,
                          "Validation passed", "SYSTEM")
            self._transition(case, WorkflowState.VALIDATED)
            self._audit(case.id, ActorType.SYSTEM, "Accounting Validation", "Journal entry validated",
                        journal_entry_id=case.journal_entry.id)
        else:
            failures = [f"{c.rule}: {c.detail}" for c in report.failures]
            self._audit(case.id, ActorType.SYSTEM, "Accounting Validation", "Posting blocked — validation failed",
                        failures=failures)
            if not any(e.is_open and e.type == ExceptionType.ACCOUNTING_VALIDATION_ERROR for e in case.exceptions):
                self._add_exception(case, ExceptionType.ACCOUNTING_VALIDATION_ERROR,
                                    "Journal entry failed accounting validation.", {"failures": failures})
        return report

    # ----------------------------------------------------------- human actions

    def approve(self, invoice_id: str, user: User, comment: str | None = None) -> InvoiceCase:
        case = self.repo.get_case(invoice_id)
        if case.status != WorkflowState.PENDING_APPROVAL:
            raise WorkflowError(f"Invoice {invoice_id} is {case.status}; it is not awaiting approval.")
        step = case.approval_route.next_step
        if step is None:
            raise WorkflowError(f"Invoice {invoice_id} has no pending approval step.")
        if step.role not in user.roles:
            raise WorkflowError(f"{user.label} is not authorized to approve as {step.role}.")
        if case.blocking_exceptions:
            names = ", ".join(f"{e.type} ({e.id})" for e in case.blocking_exceptions)
            raise WorkflowError(f"Approval blocked by unresolved exceptions: {names}. "
                                "Correct the issue, dismiss with a reason, or reject the invoice.")

        step.status, step.decided_by, step.decided_at, step.comment = (
            StepStatus.APPROVED, user.name, datetime.now(), comment)
        resolved = self._resolve(case, lambda e: e.route_to == step.role,
                                 f"Accepted by {step.role} ({user.name})", user.name)
        self._audit(case.id, ActorType.HUMAN, user.name, f"Approved as {step.role}",
                    comment=comment, resolved_exceptions=resolved)

        if case.approval_route.is_complete:
            self._transition(case, WorkflowState.APPROVED, actor=user.name)
            self._audit(case.id, ActorType.SYSTEM, "Approval Engine", "Invoice approved — all approvals complete")
            self._refresh_journal_entry(case)
            self._validate(case)
        self.repo.save_case(case)
        return case

    def reject(self, invoice_id: str, user: User, reason: str) -> InvoiceCase:
        case = self.repo.get_case(invoice_id)
        if case.status in TERMINAL_STATES:
            raise WorkflowError(f"Invoice {invoice_id} is {case.status} and cannot be rejected.")
        if not reason or not reason.strip():
            raise WorkflowError("A rejection reason is required.")
        step = case.approval_route.next_step if case.approval_route else None
        if not (user.can_post or (step and step.role in user.roles)):
            raise WorkflowError(f"{user.label} is not authorized to reject this invoice.")
        self._transition(case, WorkflowState.REJECTED, actor=user.name)
        if step:
            step.status, step.decided_by, step.decided_at, step.comment = (
                StepStatus.REJECTED, user.name, datetime.now(), reason)
        self._resolve(case, lambda e: True, f"Invoice rejected: {reason}", user.name)
        self._audit(case.id, ActorType.HUMAN, user.name, "Invoice rejected", reason=reason)
        self.repo.save_case(case)
        return case

    def correct_coding(self, invoice_id: str, user: User, gl_account: str, department: str,
                       location: str) -> InvoiceCase:
        case = self.repo.get_case(invoice_id)
        if case.status not in (WorkflowState.PENDING_APPROVAL, WorkflowState.APPROVED):
            raise WorkflowError(f"Coding cannot be changed while the invoice is {case.status}.")
        if not (ApproverRole.AP_REVIEW_QUEUE in user.roles or ApproverRole.CONTROLLER in user.roles):
            raise WorkflowError(f"{user.label} is not authorized to change invoice coding.")
        proposal = ClassificationProposal(
            gl_account=gl_account, department=department, location=location, confidence=1.0,
            reason=f"Coding confirmed/corrected by {user.name}.", source="HUMAN", corrected_by=user.name)
        issues = validate_coding(proposal, self.reference)
        if issues:  # human input is held to the same deterministic controls as AI output
            raise WorkflowError("Invalid coding: " + "; ".join(msg for _, msg, _ in issues))

        before = case.classification.model_dump(include={"gl_account", "department", "location"}) \
            if case.classification else None
        case.classification = proposal
        case.coding_issues = []
        apply_coding(case.invoice, proposal)
        resolved = self._resolve(case, lambda e: e.type in CODING_EXCEPTIONS,
                                 f"Coding corrected by {user.name}", user.name)
        self._refresh_journal_entry(case)
        self._audit(case.id, ActorType.HUMAN, user.name, "Coding corrected", before=before,
                    after={"gl_account": gl_account, "department": department, "location": location},
                    resolved_exceptions=resolved)
        if case.status == WorkflowState.APPROVED:
            self._validate(case)
        self.repo.save_case(case)
        return case

    def dismiss_exception(self, invoice_id: str, exception_id: str, user: User, reason: str) -> InvoiceCase:
        case = self.repo.get_case(invoice_id)
        exc = next((e for e in case.exceptions if e.id == exception_id), None)
        if exc is None or not exc.is_open:
            raise WorkflowError(f"Exception {exception_id} is not open on {invoice_id}.")
        if exc.type != ExceptionType.DUPLICATE_INVOICE:
            raise WorkflowError(f"{exc.type} cannot be dismissed; correct the underlying issue or reject.")
        if ApproverRole.AP_EXCEPTION_QUEUE not in user.roles:
            raise WorkflowError(f"{user.label} is not authorized to dismiss duplicate flags.")
        if not reason or not reason.strip():
            raise WorkflowError("A documented reason is required to dismiss a duplicate flag.")
        self._resolve(case, lambda e: e.id == exception_id, f"Not a duplicate: {reason}", user.name,
                      status=ExceptionStatus.DISMISSED)
        self._audit(case.id, ActorType.HUMAN, user.name, "Duplicate flag dismissed",
                    exception_id=exception_id, reason=reason)
        self.repo.save_case(case)
        return case

    def post(self, invoice_id: str, user: User) -> InvoiceCase:
        case = self.repo.get_case(invoice_id)
        if not user.can_post:
            raise WorkflowError(f"{user.label} is not authorized to post journal entries.")
        if case.status != WorkflowState.VALIDATED:
            self._audit(case.id, ActorType.SYSTEM, "Posting", "Posting blocked",
                        reason=f"Invoice status is {case.status}", requested_by=user.name)
            raise PostingBlockedError(
                f"POSTING BLOCKED: invoice {invoice_id} is {case.status}; only VALIDATED invoices can post.",
                case.validation)

        # Defense in depth: re-run every accounting control immediately before posting.
        report = validate_for_posting(case.invoice, case.journal_entry, case.approval_route, case.exceptions,
                                      self.reference, self.ledger.has_posting_for(case.id))
        case.validation = report
        if not report.passed:
            self.repo.save_case(case)
            self._audit(case.id, ActorType.SYSTEM, "Posting", "Posting blocked",
                        failures=[f"{c.rule}: {c.detail}" for c in report.failures])
            raise PostingBlockedError("POSTING BLOCKED: accounting validation failed.", report)

        self._audit(case.id, ActorType.HUMAN, user.name, "Posting requested")
        receipt = self.ledger.post(case.journal_entry, posted_by=user.name)
        case.journal_entry.status = JournalEntryStatus.POSTED
        case.journal_entry.posted_at = receipt.posted_at
        self._transition(case, WorkflowState.POSTED, actor=user.name)
        self._audit(case.id, ActorType.SYSTEM, receipt.ledger, "Journal entry posted",
                    journal_entry_id=receipt.journal_entry_id, posting_date=str(receipt.posting_date),
                    amount=str(case.invoice.total))
        self.repo.save_case(case)
        return case

    # ------------------------------------------------------------------ queries

    def get(self, invoice_id: str) -> InvoiceCase:
        return self.repo.get_case(invoice_id)

    def list(self) -> list[InvoiceCase]:
        return self.repo.list_cases()

    def pending_for(self, user: User) -> list[InvoiceCase]:
        """The user's approval inbox: invoices whose *next* approval step belongs to one of their roles."""
        return [c for c in self.list()
                if c.status == WorkflowState.PENDING_APPROVAL and c.approval_route
                and c.approval_route.next_step and c.approval_route.next_step.role in user.roles]

    def ready_to_post(self) -> list[InvoiceCase]:
        return [c for c in self.list() if c.status == WorkflowState.VALIDATED]


def create_workflow_service(database_url: str | None = None, reset: bool = False,
                            settings: Settings | None = None,
                            provider: LLMProvider | None = None) -> APWorkflowService:
    """Build a service against a database, seeding master data when the DB is new or reset."""
    settings = settings or load_settings()
    engine = make_engine(database_url or settings.database_url)
    init_db(engine, reset=reset)
    repo = Repository(make_session_factory(engine))
    if not repo.load_reference().vendors:
        seed_repository(repo)
    return APWorkflowService(repo, provider or create_provider(settings), settings)
