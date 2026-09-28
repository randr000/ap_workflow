"""Vendor master data and vendor-identification results."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "co", "company",
    "corp", "corporation", "plc", "the",
}


def normalize_vendor_name(name: str) -> str:
    """Deterministic normalization: lowercase, strip punctuation and legal suffixes."""
    cleaned = re.sub(r"[^a-z0-9& ]+", " ", name.lower()).replace("&", " and ")
    tokens = [t for t in cleaned.split() if t not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


class Vendor(BaseModel):
    id: str
    name: str
    normalized_name: str = ""
    tax_id: str | None = None
    default_currency: str = "USD"
    default_gl_account: str | None = None
    default_department: str | None = None
    payment_terms: str = "Net 30"
    po_required: bool = False
    active: bool = True

    def model_post_init(self, __context) -> None:
        if not self.normalized_name:
            self.normalized_name = normalize_vendor_name(self.name)


class VendorMatchStatus(StrEnum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


class VendorMatchMethod(StrEnum):
    TAX_ID = "EXACT_VENDOR_ID"
    NORMALIZED_NAME = "EXACT_NORMALIZED_NAME"
    FUZZY = "FUZZY_MATCH"
    NONE = "NONE"


class VendorCandidate(BaseModel):
    vendor_id: str
    name: str
    score: float


class VendorMatch(BaseModel):
    status: VendorMatchStatus
    method: VendorMatchMethod
    vendor_id: str | None = None
    confidence: float = 0.0
    candidates: list[VendorCandidate] = Field(default_factory=list)
    ai_suggestion: str | None = None
    ai_suggestion_reason: str | None = None
    message: str = ""
