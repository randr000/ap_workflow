"""Money helpers. All monetary values are Decimal — never float."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal | int | str) -> Decimal:
    """Quantize to cents using commercial rounding. Rejects floats outright."""
    if isinstance(value, float):
        raise TypeError("Floats are not allowed for monetary values; use Decimal or str.")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_money(value: Decimal | None, currency: str = "USD") -> str:
    if value is None:
        return "—"
    symbol = "$" if currency == "USD" else f"{currency} "
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(money(value)):,.2f}"
