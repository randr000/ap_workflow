from decimal import Decimal

from app.services.journal_entries import build_journal_entry, je_id_for


def test_je_debits_expense_and_credits_ap(invoice_factory):
    inv = invoice_factory(id="AP-0011", gl_account="6600", department="Technology",
                          lines=[("Cloud compute usage", "1", "4250.00")])
    je = build_journal_entry(inv, "Nimbus Cloud Services")
    assert je.id == "JE-000011" == je_id_for("AP-0011")
    dr, cr = je.lines
    assert (dr.account, dr.debit, dr.credit, dr.department) == ("6600", Decimal("4250.00"), Decimal("0.00"), "Technology")
    assert (cr.account, cr.debit, cr.credit) == ("2000", Decimal("0.00"), Decimal("4250.00"))
    assert je.total_debits == je.total_credits == inv.total


def test_je_is_balanced_with_multiple_lines_and_tax(invoice_factory):
    inv = invoice_factory(tax="42.18", lines=[("Stapler", "10", "18.25"), ("Paper", "10", "32.00"),
                                              ("Markers", "20", "12.00")])
    je = build_journal_entry(inv, "Acme Office Supplies")
    assert len(je.lines) == 5  # 3 expense lines + tax + AP
    assert je.total_debits == je.total_credits == Decimal("784.68")
    tax_line = je.lines[3]
    assert tax_line.memo.startswith("Sales tax") and tax_line.account == "6100"


def test_je_amounts_are_decimal(invoice_factory):
    je = build_journal_entry(invoice_factory(), "Acme")
    assert all(isinstance(ln.debit, Decimal) and isinstance(ln.credit, Decimal) for ln in je.lines)
