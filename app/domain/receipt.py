"""Goods receipts (used for three-way matching)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ReceiptLine(BaseModel):
    po_line_number: int
    description: str
    quantity_received: Decimal


class Receipt(BaseModel):
    id: str
    po_number: str
    received_date: date
    lines: list[ReceiptLine]
