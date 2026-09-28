"""SQLAlchemy tables.

Money is stored as TEXT via the `Money` type so SQLite never round-trips a
monetary amount through a binary float.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.domain.money import money


class Money(TypeDecorator):
    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else str(money(value))

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class Base(DeclarativeBase):
    pass


# --- Master data -----------------------------------------------------------


class VendorRow(Base):
    __tablename__ = "vendors"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    tax_id: Mapped[str | None] = mapped_column(String(32))
    default_currency: Mapped[str] = mapped_column(String(3))
    default_gl_account: Mapped[str | None] = mapped_column(String(16))
    default_department: Mapped[str | None] = mapped_column(String(64))
    payment_terms: Mapped[str] = mapped_column(String(32))
    po_required: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AccountRow(Base):
    __tablename__ = "accounts"
    number: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DimensionRow(Base):
    __tablename__ = "dimensions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))  # "department" | "location"
    name: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PeriodRow(Base):
    __tablename__ = "accounting_periods"
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8))


class PurchaseOrderRow(Base):
    __tablename__ = "purchase_orders"
    po_number: Mapped[str] = mapped_column(String(32), primary_key=True)
    id: Mapped[str] = mapped_column(String(32))
    vendor_id: Mapped[str] = mapped_column(String(16))
    po_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16))
    match_type: Mapped[str] = mapped_column(String(16))
    lines: Mapped[list] = mapped_column(JSON)


class ReceiptRow(Base):
    __tablename__ = "receipts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    po_number: Mapped[str] = mapped_column(String(32), index=True)
    received_date: Mapped[date] = mapped_column(Date)
    lines: Mapped[list] = mapped_column(JSON)


class CodingHistoryRow(Base):
    __tablename__ = "coding_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[str] = mapped_column(String(16), index=True)
    gl_account: Mapped[str] = mapped_column(String(16))
    department: Mapped[str] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer)


# --- Transactional data ----------------------------------------------------


class InvoiceCaseRow(Base):
    """One row per invoice. Key fields are columns (for querying); the full
    workflow aggregate (proposals, exceptions, approvals, JE) is a JSON payload."""

    __tablename__ = "invoice_cases"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(64), index=True)
    vendor_id: Mapped[str | None] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    total: Mapped[Decimal] = mapped_column(Money)
    invoice_date: Mapped[date] = mapped_column(Date)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str | None] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    actor_type: Mapped[str] = mapped_column(String(8))
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(200))
    details: Mapped[dict] = mapped_column(JSON)


class LedgerEntryRow(Base):
    """Internal general ledger — the stand-in for an ERP. Append-only."""

    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journal_entry_id: Mapped[str] = mapped_column(String(16), index=True)
    invoice_id: Mapped[str] = mapped_column(String(16), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    account: Mapped[str] = mapped_column(String(16))
    department: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(64))
    debit: Mapped[Decimal] = mapped_column(Money)
    credit: Mapped[Decimal] = mapped_column(Money)
    memo: Mapped[str] = mapped_column(String(300))
    posting_date: Mapped[date] = mapped_column(Date)
    posted_at: Mapped[datetime] = mapped_column(DateTime)
    posted_by: Mapped[str] = mapped_column(String(64))
