"""OpenAI-compatible provider (works with any /chat/completions endpoint that
supports JSON mode).

Every response is parsed as JSON and validated against the same Pydantic
contracts the mock provider uses. Invalid output raises AIProviderError; it is
never passed through. Downstream, deterministic services validate the content
again (vendor exists, account active, dimensions valid, arithmetic foots).
"""

from __future__ import annotations

import json

import httpx
from pydantic import ValidationError

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


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        self.name = f"OpenAI-compatible ({model})"
        self._model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {api_key}"})

    def _chat_json(self, instruction: str, payload: str) -> dict:
        try:
            resp = self._client.post("/chat/completions", json={
                "model": self._model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"{instruction}\n\n---\n{payload}"},
                ],
            })
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as e:
            raise AIProviderError(f"LLM request failed: {e}") from e

    def _validated(self, model_cls, data: dict):
        try:
            return model_cls.model_validate(data)
        except ValidationError as e:
            raise AIProviderError(f"LLM output failed schema validation ({e.error_count()} errors)") from e

    def extract_invoice(self, document: str) -> InvoiceExtraction:
        schema = json.dumps(InvoiceExtraction.model_json_schema())
        data = self._chat_json(
            "Extract the invoice into JSON matching this JSON schema. Use ISO dates, numbers without "
            f"currency symbols, and null for absent fields. Set 'confidence' to your extraction confidence.\n{schema}",
            document)
        return self._validated(InvoiceExtraction, data)

    def suggest_vendor(self, vendor_name: str, candidates: list[Vendor]) -> VendorSuggestion:
        options = [{"vendor_id": v.id, "name": v.name, "tax_id": v.tax_id} for v in candidates]
        data = self._chat_json(
            'Pick the vendor record that best matches the invoice vendor. Return {"vendor_id": str|null, '
            '"confidence": 0..1, "reason": short string}. Return null if none plausibly match.',
            json.dumps({"invoice_vendor_name": vendor_name, "candidates": options}))
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
        data = self._chat_json(
            'Propose GL coding for this AP invoice using ONLY the listed accounts and dimensions. Return '
            '{"gl_account": str, "department": str, "location": str, "confidence": 0..1, '
            '"reason": one sentence}.',
            json.dumps(ctx))
        data.update(source="AI", corrected_by=None)
        return self._validated(ClassificationProposal, data)

    def explain_exception(self, exception: ExceptionRecord) -> str:
        data = self._chat_json(
            'Explain this AP exception to an AP clerk in at most two sentences. Return {"explanation": str}.',
            json.dumps({"type": exception.type, "message": exception.message, "evidence": exception.evidence},
                       default=str))
        text = str(data.get("explanation", "")).strip()
        return text[:400] or exception.message
