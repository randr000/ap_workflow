"""Deterministic journal-entry proposal for an AP invoice.

    Dr  <expense / asset account per line>   (with department & location)
    Dr  <expense account of largest line>    sales tax, if any
        Cr  2000 Accounts Payable                  invoice total
"""

from __future__ import annotations

from app.domain.accounting import JournalEntry, JournalEntryLine
from app.domain.invoice import Invoice
from app.domain.money import ZERO, money
from app.domain.reference import AP_CONTROL_ACCOUNT


def je_id_for(invoice_id: str) -> str:
    return f"JE-{int(invoice_id.removeprefix('AP-')):06d}"


def build_journal_entry(invoice: Invoice, vendor_name: str, created_by: str = "SYSTEM") -> JournalEntry:
    lines: list[JournalEntryLine] = []
    for ln in invoice.lines:
        lines.append(JournalEntryLine(
            account=ln.gl_account, department=ln.department, location=ln.location,
            debit=money(ln.amount), credit=ZERO, memo=ln.description))

    if invoice.tax and money(invoice.tax) != ZERO and invoice.lines:
        largest = max(invoice.lines, key=lambda ln: ln.amount)
        lines.append(JournalEntryLine(
            account=largest.gl_account, department=largest.department, location=largest.location,
            debit=money(invoice.tax), credit=ZERO, memo="Sales tax (non-recoverable)"))

    lines.append(JournalEntryLine(
        account=AP_CONTROL_ACCOUNT, debit=ZERO, credit=money(invoice.total),
        memo=f"{vendor_name} — invoice {invoice.invoice_number}"))

    return JournalEntry(
        id=je_id_for(invoice.id), invoice_id=invoice.id, date=invoice.invoice_date,
        description=f"AP invoice {invoice.invoice_number} — {vendor_name}",
        lines=lines, created_by=created_by)
