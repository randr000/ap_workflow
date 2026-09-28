"""The 20-invoice demo dataset.

    python -m demo.dataset      # (re)generate data/invoices/*.txt from these specs

The specs are the source of truth; the .txt files are the "documents" the
workflow actually ingests (they are committed so reviewers can read them).
"""

from __future__ import annotations

from datetime import date

from app.config import DATA_DIR
from demo.documents import InvoiceSpec, render_invoice

INVOICE_DIR = DATA_DIR / "invoices"

D = date
ACME = dict(vendor_name="Acme Office Supplies", tax_id="59-1234567", address="1200 Biscayne Blvd, Miami, FL 33132")
NIMBUS = dict(vendor_name="Nimbus Cloud Services", tax_id="91-2345678", address="400 Cumulus Ave, Seattle, WA 98101")
PINNACLE = dict(vendor_name="Pinnacle Industrial Supply", tax_id="75-7890123",
                address="8800 Freeport Pkwy, Irving, TX 75063", ship_to_city="Dallas")
CLOUDDESK = dict(vendor_name="CloudDesk Software", tax_id="94-8901234", address="55 Market St, San Francisco, CA 94105")

DEMO_INVOICES: list[tuple[str, InvoiceSpec]] = [
    ("01_acme_INV-1001.txt", InvoiceSpec(
        **ACME, invoice_number="INV-1001", invoice_date=D(2026, 9, 5), po_number="PO-5001",
        lines=[("Copy paper, letter, 10-ream case", "20", "25.00")])),
    ("02_acme_INV-1002.txt", InvoiceSpec(
        **ACME, invoice_number="INV-1002", invoice_date=D(2026, 9, 6), po_number="PO-5002",
        lines=[("Toner cartridge, black, high yield", "40", "12.75")])),
    ("03_acme_INV-1004.txt", InvoiceSpec(
        **ACME, invoice_number="INV-1004", invoice_date=D(2026, 9, 8), po_number="PO-5003",
        ship_to_city="New York", lines=[("Ergonomic desk mat", "20", "27.00")])),
    ("04_brightpath_BCG-0912.txt", InvoiceSpec(
        vendor_name="Brightpath Consulting Group", tax_id="36-4567890", address="2100 Ross Ave, Dallas, TX 75201",
        invoice_number="BCG-0912", invoice_date=D(2026, 9, 12), po_number="PO-5004", ship_to_city="Dallas",
        lines=[("Process improvement consulting (hours)", "48", "150.00")])),
    ("05_pinnacle_PIS-33017.txt", InvoiceSpec(
        **PINNACLE, invoice_number="PIS-33017", invoice_date=D(2026, 9, 10), po_number="PO-5005",
        lines=[("Industrial shelving unit, 48 in", "100", "18.00")])),
    ("06_pinnacle_PIS-33102.txt", InvoiceSpec(
        **PINNACLE, invoice_number="PIS-33102", invoice_date=D(2026, 9, 15),
        lines=[("Replacement conveyor belt assembly", "1", "4250.00")])),
    ("07_acme_INV-1001_resubmitted.txt", InvoiceSpec(
        **ACME, invoice_number="INV-1001", invoice_date=D(2026, 9, 5), po_number="PO-5001",
        lines=[("Copy paper, letter, 10-ream case", "20", "25.00")],
        notes=["", "*** SECOND NOTICE — PLEASE REMIT ***"])),
    ("08_horizon_HSG-0930.txt", InvoiceSpec(
        vendor_name="Horizon Services Group", tax_id="13-7778889", address="77 Water St, New York, NY 10005",
        invoice_number="HSG-0930", invoice_date=D(2026, 9, 14),
        lines=[("Q3 support services - miscellaneous", "1", "850.00")])),
    ("09_summit_SLP-2026-0918.txt", InvoiceSpec(
        vendor_name="Summit Legal Partners LLP", tax_id="13-3456789", address="1 Liberty Plaza, New York, NY 10006",
        invoice_number="SLP-2026-0918", invoice_date=D(2026, 9, 18), ship_to_city="New York", terms_days=45,
        lines=[("Legal retainer - September 2026", "1", "12000.00"),
               ("Contract review - vendor agreements (hours)", "13", "500.00")])),
    ("10_skyway_SKY-4471.txt", InvoiceSpec(
        vendor_name="Skyway Travel Management", tax_id="13-9012345", address="30 Rockefeller Plz, New York, NY 10112",
        invoice_number="SKY-4471", invoice_date=D(2026, 9, 16), ship_to_city="Chicago", terms_days=15,
        lines=[("Client visit - airfare", "2", "420.00"), ("Client visit - hotel, 2 nights", "2", "200.00")])),
    ("11_nimbus_NCS-2026-09.txt", InvoiceSpec(
        **NIMBUS, invoice_number="NCS-2026-09", invoice_date=D(2026, 9, 20),
        lines=[("Cloud compute usage - September 2026", "1", "3650.00"),
               ("Object storage - September 2026", "1", "600.00")])),
    ("12_metropower_MPL-778201-0926.txt", InvoiceSpec(
        vendor_name="Metro Power & Light", tax_id="59-5678901", address="9 Energy Plaza, Miami, FL 33130",
        invoice_number="MPL-778201-0926", invoice_date=D(2026, 9, 10), terms_days=15,
        lines=[("Electricity service - Aug 2026, Miami office", "1", "1875.40")])),
    ("13_coastal_CP-2026-09.txt", InvoiceSpec(
        vendor_name="Coastal Properties LLC", tax_id="59-6789012", address="500 Brickell Key Dr, Miami, FL 33131",
        invoice_number="CP-2026-09", invoice_date=D(2026, 9, 1), terms_days=10, tax="170.00",
        lines=[("Office rent - September 2026", "1", "8500.00")])),
    ("14_clouddesk_CD-2026-0915.txt", InvoiceSpec(
        **CLOUDDESK, invoice_number="CD-2026-0915", invoice_date=D(2026, 9, 15),
        lines=[("Helpdesk software subscription - 12 seats", "12", "40.00")])),
    ("15_acme_INV-1015.txt", InvoiceSpec(
        **ACME, invoice_number="INV-1015", invoice_date=D(2026, 9, 12), po_number="PO-5006",
        lines=[("Stapler, heavy duty", "10", "18.25"), ("Printer paper, legal, 10-ream case", "10", "32.00"),
               ("Whiteboard markers, 12-pack", "20", "12.00")])),
    ("16_pinnacle_PIS-33240.txt", InvoiceSpec(
        **PINNACLE, invoice_number="PIS-33240", invoice_date=D(2026, 9, 19), po_number="PO-5007",
        lines=[("Pallet stretch wrap, 18 in roll", "120", "18.00")])),
    ("17_metrosupply_MS-88120.txt", InvoiceSpec(
        vendor_name="Metro Supply Co.", tax_id=None, address="PO Box 4410",
        invoice_number="MS-88120", invoice_date=D(2026, 9, 17),
        lines=[("Janitorial supplies - paper towels, liners", "1", "640.00")])),
    ("18_redwood_RCA-5521.txt", InvoiceSpec(
        vendor_name="Redwood Creative Agency", tax_id="36-5556667", address="18 Wynwood Walls, Miami, FL 33127",
        invoice_number="RCA-5521", invoice_date=D(2026, 9, 18),
        lines=[("Brand refresh design services", "1", "3200.00")])),
    ("19_clouddesk_CD-2026-0915-R.txt", InvoiceSpec(
        **CLOUDDESK, invoice_number="CD-2026-0915-R", invoice_date=D(2026, 9, 24),
        lines=[("Helpdesk software subscription - 12 seats", "12", "40.00")],
        notes=["", "Reissued invoice"])),
    ("20_nimbus_NCS-LIC-2026.txt", InvoiceSpec(
        **NIMBUS, invoice_number="NCS-LIC-2026", invoice_date=D(2026, 9, 22),
        lines=[("Annual monitoring software license", "1", "2400.00")])),
]


def write_invoice_files() -> list[str]:
    INVOICE_DIR.mkdir(parents=True, exist_ok=True)
    for old in INVOICE_DIR.glob("*.txt"):
        old.unlink()
    for filename, spec in DEMO_INVOICES:
        (INVOICE_DIR / filename).write_text(render_invoice(spec))
    return [f for f, _ in DEMO_INVOICES]


def load_demo_documents() -> list[tuple[str, str]]:
    """(filename, text) in processing order. Falls back to rendering in memory."""
    files = sorted(INVOICE_DIR.glob("*.txt"))
    if len(files) == len(DEMO_INVOICES):
        return [(f.name, f.read_text()) for f in files]
    return [(name, render_invoice(spec)) for name, spec in DEMO_INVOICES]


if __name__ == "__main__":
    names = write_invoice_files()
    print(f"Wrote {len(names)} invoice documents to {INVOICE_DIR}")
