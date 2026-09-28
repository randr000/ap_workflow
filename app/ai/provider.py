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


PROVIDER_LABELS = {
    "mock": "Mock (deterministic demo)",
    "ollama": "Ollama (local / LAN model)",
    "openai": "OpenAI-compatible API",
}


def create_provider(settings: Settings, name: str | None = None, *, ollama_base_url: str | None = None,
                    ollama_model: str | None = None) -> LLMProvider:
    """Build the provider named by `name` (default: settings.llm_provider).

    mock    deterministic, no network — the default demo mode
    ollama  Ollama's native API at settings.ollama_base_url (can be another machine on the LAN)
    openai  any OpenAI-compatible endpoint; used only when an API key is configured, else mock
    """
    from app.ai.mock_provider import MockLLMProvider

    name = (name or settings.llm_provider).lower()
    if name == "ollama":
        from app.ai.ollama_provider import OllamaProvider

        return OllamaProvider(base_url=ollama_base_url or settings.ollama_base_url,
                              model=ollama_model or settings.ollama_model,
                              timeout=settings.llm_timeout_seconds)
    if name == "openai" and settings.openai_api_key:
        from app.ai.openai_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key=settings.openai_api_key, base_url=settings.openai_base_url, model=settings.openai_model,
            timeout=settings.llm_timeout_seconds)
    return MockLLMProvider()
