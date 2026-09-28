"""Invoice intake: AI extraction from document text, or structured details
entered by a user / uploaded as JSON. Either way the result is validated into
the same InvoiceExtraction contract and then the Invoice domain model."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from app.ai.provider import AIProviderError, LLMProvider
from app.domain.invoice import Invoice, InvoiceExtraction, InvoiceLine
from app.domain.money import fmt_money, money


class IntakeError(ValueError):
    """Structured invoice data is missing required fields or has invalid values."""


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


def _dec(value, field: str) -> Decimal:
    try:
        return Decimal(str(value).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError) as e:
        raise IntakeError(f"{field}: '{value}' is not a valid number") from e


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def extraction_from_fields(data: dict) -> InvoiceExtraction:
    """Build an extraction from human-entered or uploaded (JSON) invoice fields — no AI involved.

    Only *missing* figures are computed (line amount = qty x price, subtotal = sum of lines,
    total = subtotal + tax). Figures that ARE supplied are kept as-is, so an invoice whose
    printed total does not foot is still caught by the deterministic arithmetic check.
    """
    if not isinstance(data, dict):
        raise IntakeError("Invoice data must be a JSON object")
    fields = dict(data)
    raw_lines = fields.get("lines") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise IntakeError("At least one line item is required")

    lines = []
    for i, ln in enumerate(raw_lines, start=1):
        if not isinstance(ln, dict) or _blank(ln.get("description")):
            raise IntakeError(f"Line {i}: description is required")
        qty = _dec(ln.get("quantity", 1), f"Line {i} quantity")
        price = _dec(ln.get("unit_price"), f"Line {i} unit price") if not _blank(ln.get("unit_price")) else None
        if price is None:
            raise IntakeError(f"Line {i}: unit price is required")
        if qty <= 0 or price < 0:
            raise IntakeError(f"Line {i}: quantity must be positive and unit price non-negative")
        amount = money(qty * price) if _blank(ln.get("amount")) else _dec(ln["amount"], f"Line {i} amount")
        lines.append({"description": str(ln["description"]).strip(), "quantity": qty,
                      "unit_price": price, "amount": amount})

    tax = Decimal("0.00") if _blank(fields.get("tax")) else _dec(fields["tax"], "tax")
    subtotal = (money(sum((ln["amount"] for ln in lines), Decimal("0")))
                if _blank(fields.get("subtotal")) else _dec(fields["subtotal"], "subtotal"))
    total = money(subtotal + tax) if _blank(fields.get("total")) else _dec(fields["total"], "total")
    fields.update(lines=lines, subtotal=subtotal, tax=tax, total=total, confidence=1.0)
    for key in ("po_number", "vendor_tax_id", "ship_to", "due_date"):
        if _blank(fields.get(key)):
            fields[key] = None

    try:
        return InvoiceExtraction.model_validate(fields)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
        raise IntakeError(f"Invalid invoice data — {problems}") from e


def render_details(ext: InvoiceExtraction) -> str:
    """Readable text version of structured invoice details (shown as the 'source document')."""
    out = [ext.vendor_name]
    if ext.vendor_tax_id:
        out.append(f"Tax ID: {ext.vendor_tax_id}")
    out += ["", f"Invoice Number: {ext.invoice_number}", f"Invoice Date:   {ext.invoice_date}",
            f"Due Date:       {ext.due_date or '—'}", f"PO Number:      {ext.po_number or 'N/A'}",
            f"Currency:       {ext.currency}", f"Ship To:        {ext.ship_to or '—'}", "",
            f"{'Description':<44} | {'Qty':>8} | {'Unit Price':>12} | {'Amount':>12}"]
    out += [f"{ln.description[:44]:<44} | {ln.quantity:>8} | {fmt_money(ln.unit_price):>12} | {fmt_money(ln.amount):>12}"
            for ln in ext.lines]
    out += ["", f"Subtotal: {fmt_money(ext.subtotal)}", f"Tax:      {fmt_money(ext.tax)}",
            f"Total:    {fmt_money(ext.total)}"]
    return "\n".join(out) + "\n"
