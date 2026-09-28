"""Vendor identification.

    1. Exact vendor ID (tax ID printed on the invoice)
    2. Exact normalized vendor name
    3. Fuzzy name match; the AI may *recommend* a vendor, but the application
       verifies the recommendation exists and never auto-accepts an ambiguous match.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from app.ai.provider import AIProviderError, LLMProvider
from app.domain.invoice import InvoiceExtraction
from app.domain.reference import ReferenceData
from app.domain.vendor import (
    VendorCandidate,
    VendorMatch,
    VendorMatchMethod,
    VendorMatchStatus,
    normalize_vendor_name,
)

FUZZY_CANDIDATE_THRESHOLD = 0.80
FUZZY_ACCEPT_THRESHOLD = 0.88
FUZZY_MARGIN = 0.10


def identify_vendor(extraction: InvoiceExtraction, reference: ReferenceData,
                    provider: LLMProvider | None = None) -> VendorMatch:
    active = [v for v in reference.vendors if v.active]

    # 1. Exact vendor identifier
    if extraction.vendor_tax_id:
        by_tax = [v for v in active if v.tax_id == extraction.vendor_tax_id.strip()]
        if len(by_tax) == 1:
            v = by_tax[0]
            return VendorMatch(status=VendorMatchStatus.MATCHED, method=VendorMatchMethod.TAX_ID,
                               vendor_id=v.id, confidence=1.0,
                               candidates=[VendorCandidate(vendor_id=v.id, name=v.name, score=1.0)],
                               message=f"Matched on tax ID {v.tax_id}.")

    # 2. Exact normalized name
    norm = normalize_vendor_name(extraction.vendor_name)
    exact = [v for v in active if v.normalized_name == norm]
    if len(exact) == 1:
        v = exact[0]
        return VendorMatch(status=VendorMatchStatus.MATCHED, method=VendorMatchMethod.NORMALIZED_NAME,
                           vendor_id=v.id, confidence=0.98,
                           candidates=[VendorCandidate(vendor_id=v.id, name=v.name, score=1.0)],
                           message=f"Matched on normalized name '{norm}'.")

    # 3. Fuzzy candidates
    if exact:
        scored = [(1.0, v) for v in exact]
    else:
        scored = sorted(((SequenceMatcher(None, norm, v.normalized_name).ratio(), v) for v in active),
                        key=lambda t: (-t[0], t[1].id))
        scored = [(s, v) for s, v in scored if s >= FUZZY_CANDIDATE_THRESHOLD]
    candidates = [VendorCandidate(vendor_id=v.id, name=v.name, score=round(s, 3)) for s, v in scored]

    if scored and not exact:
        score, v = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if score >= FUZZY_ACCEPT_THRESHOLD and score - runner_up >= FUZZY_MARGIN:
            return VendorMatch(status=VendorMatchStatus.MATCHED, method=VendorMatchMethod.FUZZY,
                               vendor_id=v.id, confidence=round(score, 2), candidates=candidates,
                               message=f"Fuzzy name match to {v.name} ({score:.0%}).")

    if not scored:
        return VendorMatch(status=VendorMatchStatus.NOT_FOUND, method=VendorMatchMethod.NONE,
                           message=f"No vendor in the vendor master resembles '{extraction.vendor_name}'.")

    # Ambiguous: ask the AI for a recommendation, validate it, but do not accept it.
    suggestion_id, suggestion_reason = None, None
    if provider is not None:
        try:
            s = provider.suggest_vendor(extraction.vendor_name, [v for _, v in scored])
            if s.vendor_id and any(c.vendor_id == s.vendor_id for c in candidates):
                suggestion_id, suggestion_reason = s.vendor_id, s.reason
        except AIProviderError:
            pass
    return VendorMatch(
        status=VendorMatchStatus.AMBIGUOUS, method=VendorMatchMethod.FUZZY if not exact else VendorMatchMethod.NORMALIZED_NAME,
        confidence=0.0, candidates=candidates, ai_suggestion=suggestion_id,
        ai_suggestion_reason=suggestion_reason,
        message=f"Multiple possible vendors identified for '{extraction.vendor_name}'. Human review required.")
