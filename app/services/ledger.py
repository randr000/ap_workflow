"""Ledger / ERP boundary.

`LedgerGateway` is the intentional architectural seam where an ERP integration
(NetSuite, Oracle, OpenSumma, ...) would plug in. The MVP ships an internal,
append-only ledger. Only the workflow service calls `post`, and only after
deterministic validation has passed — the AI layer has no reference to it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.db.repository import Repository
from app.domain.accounting import JournalEntry, PostingReceipt


class LedgerGateway(Protocol):
    name: str

    def post(self, je: JournalEntry, posted_by: str) -> PostingReceipt: ...

    def has_posting_for(self, invoice_id: str) -> bool: ...


class InternalLedger:
    name = "Internal Ledger (SQLite)"

    def __init__(self, repo: Repository):
        self._repo = repo

    def post(self, je: JournalEntry, posted_by: str) -> PostingReceipt:
        if self.has_posting_for(je.invoice_id):
            raise ValueError(f"Invoice {je.invoice_id} is already posted")
        posted_at = datetime.now()
        self._repo.add_ledger_entries(je, posting_date=je.date, posted_at=posted_at, posted_by=posted_by)
        return PostingReceipt(journal_entry_id=je.id, invoice_id=je.invoice_id, posting_date=je.date,
                              posted_at=posted_at, ledger=self.name, reference=je.id)

    def has_posting_for(self, invoice_id: str) -> bool:
        return self._repo.ledger_has_invoice(invoice_id)
