"""Render synthetic invoice documents (plain text, as an OCR layer would produce).

The mock provider "reads" this layout; an LLM provider can read it as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

BILL_TO = "Harborview Holdings, Inc."


@dataclass
class InvoiceSpec:
    vendor_name: str
    invoice_number: str
    invoice_date: date
    lines: list[tuple[str, str, str]]          # (description, quantity, unit price)
    po_number: str | None = None
    tax: str = "0.00"
    tax_id: str | None = None                   # omitted from the document when None
    ship_to_city: str = "Miami"
    terms_days: int = 30
    address: str = "100 Commerce Way, Suite 200"
    notes: list[str] = field(default_factory=list)
    total_override: str | None = None           # to simulate an invoice that does not foot


def _m(value: Decimal) -> str:
    return f"{value:,.2f}"


def render_invoice(spec: InvoiceSpec) -> str:
    rows = []
    subtotal = Decimal("0.00")
    for desc, qty, price in spec.lines:
        amount = (Decimal(qty) * Decimal(price)).quantize(Decimal("0.01"))
        subtotal += amount
        rows.append(f"{desc:<44} | {qty:>6} | {_m(Decimal(price)):>10} | {_m(amount):>10}")
    tax = Decimal(spec.tax)
    total = Decimal(spec.total_override) if spec.total_override else subtotal + tax

    header = [spec.vendor_name, spec.address]
    if spec.tax_id:
        header.append(f"Tax ID: {spec.tax_id}")
    doc = [
        *header,
        "",
        "INVOICE",
        "",
        f"Invoice Number: {spec.invoice_number}",
        f"Invoice Date:   {spec.invoice_date.isoformat()}",
        f"Due Date:       {(spec.invoice_date + timedelta(days=spec.terms_days)).isoformat()}",
        f"PO Number:      {spec.po_number or 'N/A'}",
        "Currency:       USD",
        f"Bill To:        {BILL_TO}",
        f"Ship To:        Harborview Holdings - {spec.ship_to_city} Office",
        "",
        f"{'Description':<44} | {'Qty':>6} | {'Unit Price':>10} | {'Amount':>10}",
        *rows,
        "",
        f"Subtotal: {_m(subtotal)}",
        f"Tax:      {_m(tax)}",
        f"Total:    {_m(total)}",
        "",
        f"Payment Terms: Net {spec.terms_days}",
        *spec.notes,
    ]
    return "\n".join(doc) + "\n"
