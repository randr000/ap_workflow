"""Read-only snapshot of master data used by the deterministic services."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.accounting import Account, AccountingPeriod, AccountType, HistoricalCoding, PeriodStatus
from app.domain.purchase_order import PurchaseOrder
from app.domain.receipt import Receipt
from app.domain.vendor import Vendor

AP_CONTROL_ACCOUNT = "2000"


class ReferenceData(BaseModel):
    vendors: list[Vendor] = Field(default_factory=list)
    accounts: list[Account] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    periods: list[AccountingPeriod] = Field(default_factory=list)
    purchase_orders: list[PurchaseOrder] = Field(default_factory=list)
    receipts: list[Receipt] = Field(default_factory=list)
    coding_history: list[HistoricalCoding] = Field(default_factory=list)

    def vendor(self, vendor_id: str | None) -> Vendor | None:
        return next((v for v in self.vendors if v.id == vendor_id), None)

    def account(self, number: str | None) -> Account | None:
        return next((a for a in self.accounts if a.number == number), None)

    def active_account(self, number: str | None) -> Account | None:
        acct = self.account(number)
        return acct if acct and acct.active else None

    def codable_accounts(self) -> list[Account]:
        """Accounts an AP invoice line may be coded to (active expense/asset)."""
        return [a for a in self.accounts
                if a.active and a.type in (AccountType.EXPENSE, AccountType.ASSET)
                and a.number != "1000"]

    def purchase_order(self, po_number: str | None) -> PurchaseOrder | None:
        if not po_number:
            return None
        key = po_number.strip().upper()
        return next((p for p in self.purchase_orders if p.po_number.upper() == key), None)

    def receipts_for(self, po_number: str) -> list[Receipt]:
        return [r for r in self.receipts if r.po_number == po_number]

    def received_quantity(self, po_number: str, po_line: int) -> Decimal:
        total = Decimal("0")
        for receipt in self.receipts_for(po_number):
            for line in receipt.lines:
                if line.po_line_number == po_line:
                    total += line.quantity_received
        return total

    def history_for(self, vendor_id: str) -> list[HistoricalCoding]:
        return sorted((h for h in self.coding_history if h.vendor_id == vendor_id),
                      key=lambda h: -h.count)

    def period_for(self, d: date) -> AccountingPeriod | None:
        return next((p for p in self.periods if p.contains(d)), None)

    def period_is_open(self, d: date) -> bool:
        period = self.period_for(d)
        return bool(period and period.status == PeriodStatus.OPEN)
