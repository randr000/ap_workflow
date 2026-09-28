"""Shared logic for real LLM backends (OpenAI-compatible APIs and Ollama).

Subclasses only implement the transport (`_chat_json`). Prompts, JSON schemas
and output validation live here, so every backend is held to the same
contracts: the response must be JSON, and it must validate against the same
Pydantic models the mock provider returns. Anything else raises AIProviderError
and is never passed through. Downstream, deterministic services validate the
content again (vendor exists, account active, dimensions valid, arithmetic foots).
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError

from app.ai.provider import AIProviderError, VendorSuggestion
from app.domain.accounting import ClassificationContext, ClassificationProposal
from app.domain.exception import ExceptionRecord
from app.domain.invoice import Invoice, InvoiceExtraction
from app.domain.vendor import Vendor

SYSTEM_PROMPT = (
    "You are an accounts-payable assistant. You only propose; deterministic software validates "
    "and decides. Respond with a single JSON object matching the requested schema. Do not include "
    "explanations of your reasoning beyond the short 'reason' field when one is requested."
)

_NULLABLE_STR = {"type": ["string", "null"]}

# Written out by hand (rather than generated from the Pydantic model) so local models see plain
# number/string types instead of Decimal's anyOf. The response is still validated by InvoiceExtraction.
EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "vendor_name": {"type": "string"},
        "vendor_tax_id": _NULLABLE_STR,
        "invoice_number": {"type": "string"},
        "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
        "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "po_number": _NULLABLE_STR,
        "currency": {"type": "string"},
        "ship_to": _NULLABLE_STR,
        "subtotal": {"type": "number"},
        "tax": {"type": "number"},
        "total": {"type": "number"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "amount": {"type": "number"},
                },
                "required": ["description", "quantity", "unit_price", "amount"],
            },
        },
        "confidence": {"type": "number", "description": "0 to 1"},
    },
    "required": ["vendor_name", "invoice_number", "invoice_date", "subtotal", "tax", "total", "lines",
                 "confidence"],
}


class CodingOutput(BaseModel):
    gl_account: str
    department: str
    location: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class ExplanationOutput(BaseModel):
    explanation: str


def _normalize_confidence(data: dict) -> dict:
    """Local models often answer 95 instead of 0.95. Rescale values that are clearly percentages;
    leave anything else (e.g. 1.7) for schema validation to reject."""
    c = data.get("confidence")
    if isinstance(c, (int, float)) and 10 <= c <= 100:
        data["confidence"] = c / 100
    return data


class JSONChatProvider:
    name: str

    def _chat_json(self, instruction: str, payload: str, schema: dict) -> dict:
        raise NotImplementedError

    def _ask(self, instruction: str, payload: str, schema: dict) -> dict:
        prompt = f"{instruction}\n\nRespond with JSON matching this schema:\n{json.dumps(schema)}"
        data = self._chat_json(prompt, payload, schema)
        if not isinstance(data, dict):
            raise AIProviderError("LLM response was not a JSON object")
        return _normalize_confidence(data)

    @staticmethod
    def _validated(model_cls, data: dict):
        try:
            return model_cls.model_validate(data)
        except ValidationError as e:
            fields = ", ".join(".".join(str(p) for p in err["loc"]) for err in e.errors()[:5])
            raise AIProviderError(f"LLM output failed schema validation ({e.error_count()} errors: {fields})") from e

    # --- LLMProvider interface -------------------------------------------------

    def extract_invoice(self, document: str) -> InvoiceExtraction:
        data = self._ask(
            "Extract this invoice. Use ISO dates (YYYY-MM-DD), plain numbers without currency symbols or "
            "thousands separators, and null for fields that are absent (for example 'PO Number: N/A' is null). "
            "One entry in 'lines' per line item. Set 'confidence' (0 to 1) to your extraction confidence.",
            document, EXTRACTION_SCHEMA)
        return self._validated(InvoiceExtraction, data)

    def suggest_vendor(self, vendor_name: str, candidates: list[Vendor]) -> VendorSuggestion:
        options = [{"vendor_id": v.id, "name": v.name, "tax_id": v.tax_id} for v in candidates]
        data = self._ask(
            "Pick the vendor record that best matches the invoice vendor. Use null for vendor_id if none "
            "plausibly match. Keep 'reason' under 200 characters.",
            json.dumps({"invoice_vendor_name": vendor_name, "candidates": options}),
            VendorSuggestion.model_json_schema())
        return self._validated(VendorSuggestion, data)

    def classify_invoice(self, invoice: Invoice, context: ClassificationContext) -> ClassificationProposal:
        ctx = {
            "vendor": context.vendor_name,
            "vendor_default_gl": context.vendor_default_gl,
            "line_descriptions": context.descriptions,
            "ship_to": context.ship_to,
            "historical_coding": [h.model_dump() for h in context.history],
            "po_coding": context.po_coding,
            "accounts": [{"number": a.number, "name": a.name} for a in context.accounts],
            "departments": context.departments,
            "locations": context.locations,
        }
        data = self._ask(
            "Propose GL coding for this AP invoice using ONLY the listed account numbers, departments and "
            "locations. 'confidence' is 0 to 1. 'reason' is one short sentence.",
            json.dumps(ctx), CodingOutput.model_json_schema())
        coding = self._validated(CodingOutput, data)
        return ClassificationProposal(**coding.model_dump(), source="AI")

    def explain_exception(self, exception: ExceptionRecord) -> str:
        data = self._ask(
            "Explain this AP exception to an AP clerk in at most two sentences.",
            json.dumps({"type": exception.type, "message": exception.message, "evidence": exception.evidence},
                       default=str),
            ExplanationOutput.model_json_schema())
        text = self._validated(ExplanationOutput, data).explanation.strip()
        return text[:400] or exception.message
