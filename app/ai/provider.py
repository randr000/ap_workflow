"""LLM provider abstraction.

The AI layer is deliberately narrow: it can only *return proposals* as typed,
validated Pydantic objects. It has no access to the repository, the workflow
service, or the ledger — so it cannot change state or post entries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from app.domain.accounting import ClassificationContext, ClassificationProposal
from app.domain.exception import ExceptionRecord
from app.domain.invoice import Invoice, InvoiceExtraction
from app.domain.vendor import Vendor

if TYPE_CHECKING:
    from app.config import Settings


class AIProviderError(Exception):
    """The provider failed or returned output that did not validate."""


class VendorSuggestion(BaseModel):
    vendor_id: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=300)


class LLMProvider(Protocol):
    name: str

    def extract_invoice(self, document: str) -> InvoiceExtraction: ...

    def suggest_vendor(self, vendor_name: str, candidates: list[Vendor]) -> VendorSuggestion: ...

    def classify_invoice(self, invoice: Invoice, context: ClassificationContext) -> ClassificationProposal: ...

    def explain_exception(self, exception: ExceptionRecord) -> str: ...


def create_provider(settings: Settings) -> LLMProvider:
    """Mock (deterministic) by default. The OpenAI-compatible provider is used only
    when explicitly configured AND an API key is present."""
    from app.ai.mock_provider import MockLLMProvider

    if settings.llm_provider == "openai" and settings.openai_api_key:
        from app.ai.openai_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key=settings.openai_api_key, base_url=settings.openai_base_url, model=settings.openai_model)
    return MockLLMProvider()
