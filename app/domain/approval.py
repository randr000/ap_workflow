"""Approval routes, approver roles and demo users."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ApproverRole(StrEnum):
    AP_EXCEPTION_QUEUE = "AP Exception Queue"
    AP_REVIEW_QUEUE = "AP Review Queue"
    PROCUREMENT_MANAGER = "Procurement Manager"
    DEPARTMENT_MANAGER = "Department Manager"
    CONTROLLER = "Controller"


# Order in which approval steps are worked (AP triage first, financial authority last).
ROLE_ORDER = [
    ApproverRole.AP_EXCEPTION_QUEUE,
    ApproverRole.AP_REVIEW_QUEUE,
    ApproverRole.PROCUREMENT_MANAGER,
    ApproverRole.DEPARTMENT_MANAGER,
    ApproverRole.CONTROLLER,
]


class StepStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalStep(BaseModel):
    role: ApproverRole
    reason: str
    status: StepStatus = StepStatus.PENDING
    decided_by: str | None = None
    decided_at: datetime | None = None
    comment: str | None = None


class ApprovalRoute(BaseModel):
    auto_approval_eligible: bool
    steps: list[ApprovalStep] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)

    @property
    def next_step(self) -> ApprovalStep | None:
        return next((s for s in self.steps if s.status == StepStatus.PENDING), None)

    @property
    def is_complete(self) -> bool:
        return all(s.status == StepStatus.APPROVED for s in self.steps)


class User(BaseModel):
    """A demo user. Roles gate which approval steps they can act on and whether
    they may post to the ledger (workflow permissions are deterministic)."""

    name: str
    title: str
    roles: list[ApproverRole]
    can_post: bool = False

    @property
    def label(self) -> str:
        return f"{self.name} ({self.title})"

    @property
    def role_label(self) -> str:
        return f"{self.title} — {self.name}"


# One persona per role. Each persona can only act on approval steps for its own role(s).
DEMO_USERS: list[User] = [
    User(name="Dana Brooks", title="AP Specialist",
         roles=[ApproverRole.AP_REVIEW_QUEUE, ApproverRole.AP_EXCEPTION_QUEUE], can_post=True),
    User(name="Priya Shah", title="Procurement Manager", roles=[ApproverRole.PROCUREMENT_MANAGER]),
    User(name="Marcus Lee", title="Department Manager", roles=[ApproverRole.DEPARTMENT_MANAGER]),
    User(name="Elena Torres", title="Controller", roles=[ApproverRole.CONTROLLER], can_post=True),
]


def get_user(name: str) -> User:
    for user in DEMO_USERS:
        if user.name == name:
            return user
    raise KeyError(f"Unknown user: {name}")
