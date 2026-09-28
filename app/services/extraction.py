"""Invoice extraction service: AI extracts, application code validates and
normalizes into the Invoice domain model."""

from __future__ import annotations

from pydantic import ValidationError

from app.ai.provider import AIProviderError, LLMProvider
from app.domain.invoice import Invoice, InvoiceExtraction, InvoiceLine
from app.domain.money import money


def extract(provider: LLMProvider, document: str) -> InvoiceExtraction:
    if not document or not document.strip():
        raise AIProviderError("Empty document")
    raw = provider.extract_invoice(document)
    # Never trust provider output, even when it claims to be the right type: re-validate.
    try:
        return InvoiceExtraction.model_validate(raw.model_dump())
    except ValidationError as e:
        raise AIProviderError(f"Extraction failed validation: {e.error_count()} error(s)") from e


def build_invoice(extraction: InvoiceExtraction, invoice_id: str, vendor_id: str | None,
                  source: str) -> Invoice:
    lines = [
        InvoiceLine(line_number=i, description=ln.description, quantity=ln.quantity,
                    unit_price=ln.unit_price, amount=money(ln.amount))
        for i, ln in enumerate(extraction.lines, start=1)
    ]
    return Invoice(
        id=invoice_id,
        invoice_number=extraction.invoice_number.strip(),
        vendor_id=vendor_id,
        vendor_name=extraction.vendor_name,
        invoice_date=extraction.invoice_date,
        due_date=extraction.due_date,
        po_number=extraction.po_number.strip().upper() if extraction.po_number else None,
        currency=extraction.currency,
        subtotal=money(extraction.subtotal),
        tax=money(extraction.tax),
        total=money(extraction.total),
        lines=lines,
        source=source,
    )
