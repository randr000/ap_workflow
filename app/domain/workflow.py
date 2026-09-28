"""Invoice workflow state machine and audit events.

State transitions are explicit and enforced. Any attempt to move an invoice
along an edge not listed in ALLOWED_TRANSITIONS raises InvalidTransitionError.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowState(StrEnum):
    RECEIVED = "RECEIVED"
    EXTRACTED = "EXTRACTED"
    CLASSIFIED = "CLASSIFIED"
    MATCHED = "MATCHED"
    EXCEPTION = "EXCEPTION"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    VALIDATED = "VALIDATED"
    POSTED = "POSTED"
    REJECTED = "REJECTED"


S = WorkflowState

ALLOWED_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    S.RECEIVED: frozenset({S.EXTRACTED, S.REJECTED}),
    # EXTRACTED -> EXCEPTION when the vendor cannot be identified (processing halts).
    S.EXTRACTED: frozenset({S.CLASSIFIED, S.EXCEPTION, S.REJECTED}),
    # Non-PO invoices skip MATCHED and go straight to routing / auto-approval.
    S.CLASSIFIED: frozenset({S.MATCHED, S.EXCEPTION, S.PENDING_APPROVAL, S.APPROVED, S.REJECTED}),
    S.MATCHED: frozenset({S.EXCEPTION, S.PENDING_APPROVAL, S.APPROVED, S.REJECTED}),
    S.EXCEPTION: frozenset({S.PENDING_APPROVAL, S.REJECTED}),
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.REJECTED}),
    S.APPROVED: frozenset({S.VALIDATED, S.REJECTED}),
    S.VALIDATED: frozenset({S.POSTED, S.REJECTED}),
    S.POSTED: frozenset(),
    S.REJECTED: frozenset(),
}

TERMINAL_STATES = frozenset({S.POSTED, S.REJECTED})


class InvalidTransitionError(Exception):
    pass


def can_transition(current: WorkflowState, target: WorkflowState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def assert_transition(current: WorkflowState, target: WorkflowState) -> None:
    if not can_transition(current, target):
        allowed = ", ".join(sorted(ALLOWED_TRANSITIONS[current])) or "none (terminal state)"
        raise InvalidTransitionError(
            f"Invalid transition {current} -> {target}. Allowed from {current}: {allowed}."
        )


class ActorType(StrEnum):
    SYSTEM = "SYSTEM"
    AI = "AI"
    HUMAN = "HUMAN"


class StateChange(BaseModel):
    from_state: WorkflowState
    to_state: WorkflowState
    actor: str
    at: datetime = Field(default_factory=datetime.now)


class AuditEvent(BaseModel):
    """Append-only audit record. Stores concise structured evidence only —
    never raw LLM transcripts or chain-of-thought."""

    id: int | None = None
    invoice_id: str | None
    timestamp: datetime = Field(default_factory=datetime.now)
    actor_type: ActorType
    actor: str
    action: str
    details: dict[str, Any] = Field(default_factory=dict)
