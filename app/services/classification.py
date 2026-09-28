"""GL / dimension classification: AI proposes, master data decides.

The context passed to the AI is built deterministically. The proposal is then
checked against the chart of accounts and dimension lists; invalid values are
never silently accepted.
"""

from __future__ import annotations

from enum import StrEnum

from app.ai.provider import LLMProvider
from app.config import ConfidenceThresholds
from app.domain.accounting import ClassificationContext, ClassificationProposal
from app.domain.exception import ExceptionType
from app.domain.invoice import Invoice
from app.domain.reference import ReferenceData
from app.domain.vendor import Vendor


class ConfidenceBand(StrEnum):
    STRAIGHT_THROUGH = "STRAIGHT_THROUGH"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    EXCEPTION = "EXCEPTION"


def confidence_band(confidence: float, thresholds: ConfidenceThresholds) -> ConfidenceBand:
    if confidence >= thresholds.straight_through:
        return ConfidenceBand.STRAIGHT_THROUGH
    if confidence >= thresholds.review:
        return ConfidenceBand.HUMAN_REVIEW
    return ConfidenceBand.EXCEPTION


def build_context(invoice: Invoice, vendor: Vendor, reference: ReferenceData,
                  ship_to: str | None = None) -> ClassificationContext:
    po = reference.purchase_order(invoice.po_number)
    po_coding = []
    if po is not None and po.vendor_id == vendor.id:
        po_coding = [{"gl_account": ln.gl_account, "department": ln.department, "location": ln.location}
                     for ln in po.lines if ln.gl_account]
    return ClassificationContext(
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        vendor_default_gl=vendor.default_gl_account,
        vendor_default_department=vendor.default_department,
        descriptions=[ln.description for ln in invoice.lines],
        ship_to=ship_to,
        history=reference.history_for(vendor.id),
        po_coding=po_coding,
        accounts=reference.codable_accounts(),
        departments=reference.departments,
        locations=reference.locations,
    )


def validate_coding(proposal: ClassificationProposal, reference: ReferenceData) -> list[tuple[ExceptionType, str, dict]]:
    """Deterministic checks of an AI (or human) coding proposal against master data."""
    issues: list[tuple[ExceptionType, str, dict]] = []
    acct = reference.account(proposal.gl_account)
    codable = {a.number for a in reference.codable_accounts()}
    if acct is None:
        issues.append((ExceptionType.INVALID_ACCOUNT,
                       f"Proposed GL account {proposal.gl_account} does not exist in the chart of accounts.",
                       {"proposed_account": proposal.gl_account}))
    elif not acct.active:
        issues.append((ExceptionType.INVALID_ACCOUNT,
                       f"Proposed GL account {acct.label} is inactive.",
                       {"proposed_account": proposal.gl_account, "account_name": acct.name, "active": False}))
    elif acct.number not in codable:
        issues.append((ExceptionType.INVALID_ACCOUNT,
                       f"GL account {acct.label} cannot be used for AP invoice coding.",
                       {"proposed_account": proposal.gl_account, "account_type": acct.type}))
    if proposal.department not in reference.departments:
        issues.append((ExceptionType.INVALID_DIMENSION,
                       f"Department '{proposal.department}' is not a valid department.",
                       {"dimension": "department", "proposed": proposal.department,
                        "valid_values": reference.departments}))
    if proposal.location not in reference.locations:
        issues.append((ExceptionType.INVALID_DIMENSION,
                       f"Location '{proposal.location}' is not a valid location.",
                       {"dimension": "location", "proposed": proposal.location,
                        "valid_values": reference.locations}))
    return issues


def apply_coding(invoice: Invoice, proposal: ClassificationProposal) -> None:
    for line in invoice.lines:
        line.gl_account = proposal.gl_account
        line.department = proposal.department
        line.location = proposal.location
        line.class_name = proposal.class_name
