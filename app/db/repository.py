"""Repository: the only place that translates between domain models and rows."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    AccountRow,
    AuditEventRow,
    CodingHistoryRow,
    DimensionRow,
    InvoiceCaseRow,
    LedgerEntryRow,
    PeriodRow,
    PurchaseOrderRow,
    ReceiptRow,
    VendorRow,
)
from app.domain.accounting import Account, AccountingPeriod, HistoricalCoding, JournalEntry
from app.domain.case import InvoiceCase
from app.domain.purchase_order import PurchaseOrder
from app.domain.receipt import Receipt
from app.domain.reference import ReferenceData
from app.domain.vendor import Vendor
from app.domain.workflow import AuditEvent


class Repository:
    def __init__(self, session_factory: sessionmaker):
        self._sf = session_factory

    # --- master data -------------------------------------------------------

    def load_reference(self) -> ReferenceData:
        with self._sf() as s:
            vendors = [Vendor.model_validate(r, from_attributes=True) for r in s.scalars(select(VendorRow))]
            accounts = [Account.model_validate(r, from_attributes=True)
                        for r in s.scalars(select(AccountRow).order_by(AccountRow.number))]
            dims = list(s.scalars(select(DimensionRow).where(DimensionRow.active.is_(True))))
            periods = [AccountingPeriod.model_validate(r, from_attributes=True)
                       for r in s.scalars(select(PeriodRow).order_by(PeriodRow.period))]
            pos = [PurchaseOrder.model_validate(
                {**{c: getattr(r, c) for c in ("id", "po_number", "vendor_id", "po_date",
                                               "currency", "status", "match_type")}, "lines": r.lines})
                for r in s.scalars(select(PurchaseOrderRow))]
            receipts = [Receipt.model_validate({"id": r.id, "po_number": r.po_number,
                                                "received_date": r.received_date, "lines": r.lines})
                        for r in s.scalars(select(ReceiptRow))]
            history = [HistoricalCoding.model_validate(r, from_attributes=True)
                       for r in s.scalars(select(CodingHistoryRow))]
        return ReferenceData(
            vendors=vendors,
            accounts=accounts,
            departments=[d.name for d in dims if d.kind == "department"],
            locations=[d.name for d in dims if d.kind == "location"],
            periods=periods,
            purchase_orders=pos,
            receipts=receipts,
            coding_history=history,
        )

    def add_reference(self, ref: ReferenceData) -> None:
        """Insert master data (used by seeding and the synthetic benchmark)."""
        with self._sf.begin() as s:
            for v in ref.vendors:
                s.merge(VendorRow(**v.model_dump()))
            for a in ref.accounts:
                s.merge(AccountRow(**a.model_dump()))
            for name in ref.departments:
                s.add(DimensionRow(kind="department", name=name))
            for name in ref.locations:
                s.add(DimensionRow(kind="location", name=name))
            for p in ref.periods:
                s.merge(PeriodRow(**p.model_dump()))
            for po in ref.purchase_orders:
                data = po.model_dump(mode="json", by_alias=True)
                s.merge(PurchaseOrderRow(
                    po_number=po.po_number, id=po.id, vendor_id=po.vendor_id, po_date=po.po_date,
                    currency=po.currency, status=po.status, match_type=po.match_type,
                    lines=data["lines"]))
            for r in ref.receipts:
                s.merge(ReceiptRow(id=r.id, po_number=r.po_number, received_date=r.received_date,
                                   lines=r.model_dump(mode="json")["lines"]))
            for h in ref.coding_history:
                s.add(CodingHistoryRow(**h.model_dump()))

    # --- invoice cases -----------------------------------------------------

    def next_invoice_id(self) -> str:
        with self._sf() as s:
            count = s.scalar(select(func.count()).select_from(InvoiceCaseRow)) or 0
        return f"AP-{count + 1:04d}"

    def save_case(self, case: InvoiceCase) -> None:
        inv = case.invoice
        now = datetime.now()
        with self._sf.begin() as s:
            row = s.get(InvoiceCaseRow, inv.id)
            if row is None:
                row = InvoiceCaseRow(id=inv.id, created_at=inv.created_at)
                s.add(row)
            row.invoice_number = inv.invoice_number
            row.vendor_id = inv.vendor_id
            row.status = inv.status
            row.total = inv.total
            row.invoice_date = inv.invoice_date
            row.payload = case.model_dump_json()
            row.updated_at = now

    def get_case(self, invoice_id: str) -> InvoiceCase:
        with self._sf() as s:
            row = s.get(InvoiceCaseRow, invoice_id)
            if row is None:
                raise KeyError(f"Invoice {invoice_id} not found")
            return InvoiceCase.model_validate_json(row.payload)

    def list_cases(self) -> list[InvoiceCase]:
        with self._sf() as s:
            rows = s.scalars(select(InvoiceCaseRow).order_by(InvoiceCaseRow.id)).all()
            return [InvoiceCase.model_validate_json(r.payload) for r in rows]

    # --- audit -------------------------------------------------------------

    def add_audit(self, event: AuditEvent) -> None:
        with self._sf.begin() as s:
            s.add(AuditEventRow(
                invoice_id=event.invoice_id, timestamp=event.timestamp, actor_type=event.actor_type,
                actor=event.actor, action=event.action,
                details=event.model_dump(mode="json")["details"]))

    def list_audit(self, invoice_id: str | None = None) -> list[AuditEvent]:
        with self._sf() as s:
            q = select(AuditEventRow).order_by(AuditEventRow.id)
            if invoice_id:
                q = q.where(AuditEventRow.invoice_id == invoice_id)
            return [AuditEvent.model_validate(r, from_attributes=True) for r in s.scalars(q)]

    # --- ledger ------------------------------------------------------------

    def add_ledger_entries(self, je: JournalEntry, posting_date: date, posted_at: datetime,
                           posted_by: str) -> None:
        with self._sf.begin() as s:
            for i, line in enumerate(je.lines, start=1):
                s.add(LedgerEntryRow(
                    journal_entry_id=je.id, invoice_id=je.invoice_id, line_number=i,
                    account=line.account, department=line.department, location=line.location,
                    debit=line.debit, credit=line.credit, memo=line.memo[:300],
                    posting_date=posting_date, posted_at=posted_at, posted_by=posted_by))

    def ledger_has_invoice(self, invoice_id: str) -> bool:
        with self._sf() as s:
            return bool(s.scalar(select(func.count()).select_from(LedgerEntryRow)
                                 .where(LedgerEntryRow.invoice_id == invoice_id)))

    def list_ledger(self) -> list[dict]:
        with self._sf() as s:
            rows = s.scalars(select(LedgerEntryRow).order_by(LedgerEntryRow.id)).all()
            return [{
                "journal_entry_id": r.journal_entry_id, "invoice_id": r.invoice_id,
                "account": r.account, "department": r.department, "location": r.location,
                "debit": r.debit, "credit": r.credit, "memo": r.memo,
                "posting_date": r.posting_date, "posted_at": r.posted_at, "posted_by": r.posted_by,
            } for r in rows]
