from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.db.database import IN_MEMORY_URL
from app.db.seed import create_seeded_repository, load_reference_from_files
from app.domain.invoice import Invoice, InvoiceLine


@pytest.fixture
def reference():
    return load_reference_from_files()


@pytest.fixture
def repo():
    return create_seeded_repository(IN_MEMORY_URL)


def make_invoice(
    *,
    id: str = "AP-9001",
    invoice_number: str = "INV-1001",
    vendor_id: str | None = "V001",
    vendor_name: str = "Acme Office Supplies",
    invoice_date: date = date(2026, 9, 5),
    po_number: str | None = "PO-5001",
    lines: list[tuple[str, str, str]] | None = None,
    tax: str = "0.00",
    gl_account: str | None = "6100",
    department: str | None = "Operations",
    location: str | None = "Miami",
    status=None,
) -> Invoice:
    """Build an invoice from (description, quantity, unit_price) tuples."""
    lines = lines or [("Copy paper, letter, 10-ream case", "20", "25.00")]
    inv_lines = []
    for i, (desc, qty, price) in enumerate(lines, start=1):
        q, p = Decimal(qty), Decimal(price)
        inv_lines.append(InvoiceLine(
            line_number=i, description=desc, quantity=q, unit_price=p,
            amount=(q * p).quantize(Decimal("0.01")), gl_account=gl_account,
            department=department, location=location))
    subtotal = sum((ln.amount for ln in inv_lines), Decimal("0.00"))
    kwargs = {}
    if status is not None:
        kwargs["status"] = status
    return Invoice(
        id=id, invoice_number=invoice_number, vendor_id=vendor_id, vendor_name=vendor_name,
        invoice_date=invoice_date, po_number=po_number, subtotal=subtotal, tax=Decimal(tax),
        total=subtotal + Decimal(tax), lines=inv_lines, **kwargs)


@pytest.fixture
def invoice_factory():
    return make_invoice
