"""Synthetic benchmark — 100 generated invoices with a controlled scenario mix.

    python -m demo.benchmark
    python -m demo.benchmark --json

SYNTHETIC BENCHMARK FOR DEMONSTRATION PURPOSES. The invoices, vendors and
ground truth are generated; the mock provider is rule-based. These numbers
show that the pipeline behaves as designed on a known mix of cases — they are
NOT a measure of real-world AP automation performance.
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.ai.mock_provider import MockLLMProvider
from app.config import Settings
from app.db.database import init_db, make_engine, make_session_factory
from app.db.repository import Repository
from app.db.seed import load_reference_from_files
from app.domain.exception import ExceptionType as X
from app.domain.purchase_order import MatchType, POLine, PurchaseOrder
from app.domain.receipt import Receipt, ReceiptLine
from app.services.workflow_service import APWorkflowService
from demo.documents import InvoiceSpec, render_invoice

SEED = 2026
DISCLAIMER = "Synthetic benchmark for demonstration purposes. Not representative of real-world AP performance."

# Controlled distribution (sums to 100).
MIX = {
    "STRAIGHT_THROUGH": 35,
    "PRICE_WITHIN_TOLERANCE": 6,
    "APPROVAL_REQUIRED": 15,
    "PRICE_EXCEPTION": 10,
    "QUANTITY_EXCEPTION": 10,
    "NON_PO": 8,
    "LOW_CONFIDENCE": 8,
    "DUPLICATE": 8,
}

EXPECTED_EXCEPTIONS = {
    "STRAIGHT_THROUGH": set(), "PRICE_WITHIN_TOLERANCE": set(), "APPROVAL_REQUIRED": set(),
    "PRICE_EXCEPTION": {X.PRICE_VARIANCE}, "QUANTITY_EXCEPTION": {X.QUANTITY_VARIANCE},
    "NON_PO": {X.MISSING_PO}, "LOW_CONFIDENCE": {X.LOW_CODING_CONFIDENCE}, "DUPLICATE": {X.DUPLICATE_INVOICE},
}

VENDORS = {
    "V001": ("Acme Office Supplies", "59-1234567"), "V002": ("Nimbus Cloud Services", "91-2345678"),
    "V003": ("Summit Legal Partners LLP", "13-3456789"), "V004": ("Brightpath Consulting Group", "36-4567890"),
    "V005": ("Metro Power & Light", "59-5678901"), "V006": ("Coastal Properties LLC", "59-6789012"),
    "V007": ("Pinnacle Industrial Supply", "75-7890123"), "V008": ("CloudDesk Software", "94-8901234"),
    "V009": ("Skyway Travel Management", "13-9012345"), "V013": ("Horizon Services Group", "13-7778889"),
}
ACME_ITEMS = [("Copy paper, letter, 10-ream case", "6100"), ("Toner cartridge, black", "6100"),
              ("Whiteboard markers, 12-pack", "6100"), ("Stapler, heavy duty", "6100")]
PINNACLE_ITEMS = [("Industrial shelving unit", "1300"), ("Pallet stretch wrap roll", "1300")]
VAGUE = [("Q3 support services - miscellaneous", "6300"), ("Facilities support - misc", "6300"),
         ("Assorted breakroom items", "6100"), ("General services rendered", "6300")]


@dataclass
class GeneratedInvoice:
    spec: InvoiceSpec
    category: str
    true_gl: str
    po_backed: bool
    duplicate_of: int | None = None


class Generator:
    def __init__(self, seed: int = SEED):
        self.rng = random.Random(seed)
        self.pos: list[PurchaseOrder] = []
        self.receipts: list[Receipt] = []
        self.used_amounts: set[Decimal] = set()
        self.n = 0

    def _next(self) -> int:
        self.n += 1
        return self.n

    def _date(self) -> date:
        return date(2026, 9, 1) + timedelta(days=self.rng.randint(0, 27))

    def _price(self, lo: int, hi: int) -> Decimal:
        # Unique amounts so unrelated invoices never look like same-amount duplicates.
        while True:
            p = Decimal(self.rng.randint(lo * 100, hi * 100)) / 100
            if p not in self.used_amounts:
                self.used_amounts.add(p)
                return p

    def _spec(self, vid: str, number: str, lines, po=None, city="Miami") -> InvoiceSpec:
        name, tax = VENDORS[vid]
        return InvoiceSpec(vendor_name=name, tax_id=tax, invoice_number=number, invoice_date=self._date(),
                           po_number=po, lines=[(d, str(q), str(p)) for d, q, p in lines], ship_to_city=city)

    def _po(self, vid, desc, gl, qty, price, match_type, dept="Operations", loc="Miami", received=None):
        n = self._next()
        po_no = f"PO-8{n:03d}"
        self.pos.append(PurchaseOrder(
            id=po_no, po_number=po_no, vendor_id=vid, po_date=date(2026, 8, 20), match_type=match_type,
            lines=[POLine(line_number=1, description=desc, quantity=Decimal(qty), unit_price=price,
                          amount=(Decimal(qty) * price).quantize(Decimal("0.01")), gl_account=gl,
                          department=dept, location=loc)]))
        if match_type == MatchType.THREE_WAY:
            self.receipts.append(Receipt(id=f"RCV-8{n:03d}", po_number=po_no, received_date=date(2026, 8, 30),
                                         lines=[ReceiptLine(po_line_number=1, description=desc,
                                                            quantity_received=Decimal(received if received is not None else qty))]))
        return po_no, n

    # --- categories -----------------------------------------------------------

    def straight_through(self) -> GeneratedInvoice:
        kind = self.rng.choice(["acme_po", "acme_po", "clouddesk", "power", "nimbus", "skyway"])
        if kind == "acme_po":
            desc, gl = self.rng.choice(ACME_ITEMS)
            qty, price = self.rng.randint(5, 30), self._price(8, 30)
            po, n = self._po("V001", desc, gl, qty, price, MatchType.THREE_WAY)
            return GeneratedInvoice(self._spec("V001", f"INV-{9000 + n}", [(desc, qty, price)], po), "", gl, True)
        n = self._next()
        if kind == "clouddesk":
            seats, price = self.rng.randint(3, 20), self._price(20, 45)
            return GeneratedInvoice(self._spec("V008", f"CD-B{n:04d}",
                                               [("Helpdesk software subscription - seats", seats, price)]), "", "6200", False)
        if kind == "power":
            return GeneratedInvoice(self._spec("V005", f"MPL-B{n:04d}",
                                               [("Electricity service - branch office", 1, self._price(200, 950))]), "", "6500", False)
        if kind == "nimbus":
            return GeneratedInvoice(self._spec("V002", f"NCS-B{n:04d}",
                                               [("Cloud compute usage - dev environment", 1, self._price(150, 950))]), "", "6600", False)
        return GeneratedInvoice(self._spec("V009", f"SKY-B{n:04d}", [("Sales team travel - airfare", 1, self._price(250, 950))],
                                           city="New York"), "", "6400", False)

    def price_within_tolerance(self) -> GeneratedInvoice:
        desc, gl = self.rng.choice(ACME_ITEMS)
        qty, price = self.rng.randint(10, 25), self._price(15, 30)
        po, n = self._po("V001", desc, gl, qty, price, MatchType.THREE_WAY)
        bumped = (price * Decimal("1.01")).quantize(Decimal("0.01"))  # ~1%: inside $25 / 5%
        return GeneratedInvoice(self._spec("V001", f"INV-{9000 + n}", [(desc, qty, bumped)], po), "", gl, True)

    def approval_required(self) -> GeneratedInvoice:
        kind = self.rng.choice(["rent", "legal", "nimbus", "consulting"])
        n = self._next()
        if kind == "rent":
            return GeneratedInvoice(self._spec("V006", f"CP-B{n:04d}", [("Office rent - branch lease", 1, self._price(2000, 9000))]),
                                    "", "6700", False)
        if kind == "legal":
            return GeneratedInvoice(self._spec("V003", f"SLP-B{n:04d}", [("Legal advisory services", 1, self._price(1500, 25000))],
                                               city="New York"), "", "6300", False)
        if kind == "nimbus":
            return GeneratedInvoice(self._spec("V002", f"NCS-B{n:04d}", [("Cloud hosting - production", 1, self._price(1200, 6000))]),
                                    "", "6600", False)
        hours, rate = self.rng.randint(10, 40), self._price(140, 190)
        po, n = self._po("V004", "Consulting services (hours)", "6300", hours, rate, MatchType.TWO_WAY, loc="Dallas")
        return GeneratedInvoice(self._spec("V004", f"BCG-B{n:04d}", [("Consulting services (hours)", hours, rate)], po, "Dallas"),
                                "", "6300", True)

    def price_exception(self) -> GeneratedInvoice:
        desc, gl = self.rng.choice(ACME_ITEMS)
        qty, price = self.rng.randint(20, 40), self._price(20, 30)
        po, n = self._po("V001", desc, gl, qty, price, MatchType.THREE_WAY)
        bumped = (price * Decimal(self.rng.choice(["1.08", "1.10", "1.15"]))).quantize(Decimal("0.01"))
        return GeneratedInvoice(self._spec("V001", f"INV-{9000 + n}", [(desc, qty, bumped)], po), "", gl, True)

    def quantity_exception(self) -> GeneratedInvoice:
        if self.rng.random() < 0.5:  # over-billed vs ordered (two-way, services)
            hours, rate = self.rng.randint(10, 30), self._price(140, 190)
            po, n = self._po("V004", "Consulting services (hours)", "6300", hours, rate, MatchType.TWO_WAY, loc="Dallas")
            return GeneratedInvoice(self._spec("V004", f"BCG-B{n:04d}",
                                               [("Consulting services (hours)", hours + self.rng.randint(2, 8), rate)], po, "Dallas"),
                                    "", "6300", True)
        desc, gl = self.rng.choice(PINNACLE_ITEMS)  # billed more than received (three-way)
        qty, price = self.rng.randint(40, 100), self._price(10, 25)
        po, n = self._po("V007", desc, gl, qty, price, MatchType.THREE_WAY, loc="Dallas", received=qty - self.rng.randint(5, 20))
        return GeneratedInvoice(self._spec("V007", f"PIS-B{n:04d}", [(desc, qty, price)], po, "Dallas"), "", gl, True)

    def non_po(self) -> GeneratedInvoice:
        n = self._next()
        if self.rng.random() < 0.5:
            desc, gl = self.rng.choice(ACME_ITEMS)
            return GeneratedInvoice(self._spec("V001", f"INV-{9000 + n}", [(desc, self.rng.randint(5, 20), self._price(10, 30))]),
                                    "", gl, False)
        desc, gl = self.rng.choice(PINNACLE_ITEMS)
        return GeneratedInvoice(self._spec("V007", f"PIS-B{n:04d}", [(desc, self.rng.randint(10, 50), self._price(10, 25))],
                                           city="Dallas"), "", gl, False)

    def low_confidence(self) -> GeneratedInvoice:
        n = self._next()
        desc, gl = self.rng.choice(VAGUE)
        return GeneratedInvoice(self._spec("V013", f"HSG-B{n:04d}", [(desc, 1, self._price(200, 950))]), "", gl, False)


def generate(seed: int = SEED) -> tuple[list[GeneratedInvoice], Generator]:
    g = Generator(seed)
    makers = {
        "STRAIGHT_THROUGH": g.straight_through, "PRICE_WITHIN_TOLERANCE": g.price_within_tolerance,
        "APPROVAL_REQUIRED": g.approval_required, "PRICE_EXCEPTION": g.price_exception,
        "QUANTITY_EXCEPTION": g.quantity_exception, "NON_PO": g.non_po, "LOW_CONFIDENCE": g.low_confidence,
    }
    originals: list[GeneratedInvoice] = []
    for category, count in MIX.items():
        if category == "DUPLICATE":
            continue
        for _ in range(count):
            inv = makers[category]()
            inv.category = category
            originals.append(inv)
    g.rng.shuffle(originals)

    # Duplicates: re-submissions of earlier clean invoices, inserted somewhere after the original.
    ordered = list(originals)
    clean = [i for i, inv in enumerate(originals) if inv.category in ("STRAIGHT_THROUGH", "APPROVAL_REQUIRED")]
    for k, src_idx in enumerate(g.rng.sample(clean, MIX["DUPLICATE"])):
        src = originals[src_idx]
        number = src.spec.invoice_number if k % 2 == 0 else f"{src.spec.invoice_number}-R"  # HIGH / MEDIUM
        dup = GeneratedInvoice(InvoiceSpec(**{**src.spec.__dict__, "invoice_number": number}),
                               "DUPLICATE", src.true_gl, src.po_backed, duplicate_of=src_idx)
        pos = ordered.index(src)
        ordered.insert(g.rng.randint(pos + 1, len(ordered)), dup)
    return ordered, g


def run_benchmark(seed: int = SEED) -> dict:
    invoices, gen = generate(seed)
    ref = load_reference_from_files()
    ref.purchase_orders.extend(gen.pos)
    ref.receipts.extend(gen.receipts)
    engine = make_engine("sqlite://")
    init_db(engine)
    repo = Repository(make_session_factory(engine))
    repo.add_reference(ref)
    svc = APWorkflowService(repo, MockLLMProvider(), Settings())

    rows = []
    for inv in invoices:
        case = svc.process_document(render_invoice(inv.spec), source=f"benchmark:{inv.category}")
        detected = {e.type for e in case.exceptions}
        rows.append({
            "category": inv.category, "detected": detected, "expected": EXPECTED_EXCEPTIONS[inv.category],
            "straight_through": case.straight_through, "human_review": bool(case.approval_route and case.approval_route.steps),
            "po_backed": inv.po_backed, "po_matched": bool(case.match_result and case.match_result.passed),
            "true_gl": inv.true_gl, "proposed_gl": case.ai_classification.gl_account if case.ai_classification else None,
            "is_duplicate": inv.duplicate_of is not None,
        })

    total = len(rows)
    dups = [r for r in rows if r["is_duplicate"]]
    po_backed = [r for r in rows if r["po_backed"] and not r["is_duplicate"]]
    classified = [r for r in rows if not r["is_duplicate"] and r["proposed_gl"]]
    agreement = sum(1 for r in rows if r["detected"] == r["expected"])
    return {
        "disclaimer": DISCLAIMER,
        "seed": seed,
        "mix": MIX,
        "total_invoices": total,
        "straight_through_invoices": sum(r["straight_through"] for r in rows),
        "straight_through_rate": sum(r["straight_through"] for r in rows) / total,
        "exception_rate": sum(1 for r in rows if r["detected"]) / total,
        "human_review_rate": sum(r["human_review"] for r in rows) / total,
        "duplicate_detection_rate": sum(1 for r in dups if X.DUPLICATE_INVOICE in r["detected"]) / len(dups),
        "false_duplicate_flags": sum(1 for r in rows if not r["is_duplicate"] and X.DUPLICATE_INVOICE in r["detected"]),
        "po_match_rate": sum(r["po_matched"] for r in po_backed) / len(po_backed),
        "classification_accuracy": sum(r["proposed_gl"] == r["true_gl"] for r in classified) / len(classified),
        "expected_outcome_agreement": agreement / total,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    m = run_benchmark()
    if "--json" in argv:
        print(json.dumps(m, indent=2))
        return 0
    pct = lambda v: f"{v:.1%}"  # noqa: E731
    print("AP AUTOMATION — SYNTHETIC BENCHMARK")
    print(f"({DISCLAIMER})\n")
    print("Scenario mix: " + ", ".join(f"{k.replace('_', ' ').lower()} {v}" for k, v in MIX.items()) + "\n")
    lines = [
        ("Total invoices", m["total_invoices"]),
        ("Straight-through invoices", f"{m['straight_through_invoices']} ({pct(m['straight_through_rate'])})"),
        ("Exception rate", pct(m["exception_rate"])),
        ("Human-review rate", pct(m["human_review_rate"])),
        ("Duplicate detection rate", f"{pct(m['duplicate_detection_rate'])} ({m['false_duplicate_flags']} false flags)"),
        ("PO-match rate (PO-backed invoices)", pct(m["po_match_rate"])),
        ("Classification accuracy (GL)", pct(m["classification_accuracy"])),
        ("Expected-outcome agreement", pct(m["expected_outcome_agreement"])),
    ]
    for label, value in lines:
        print(f"  {label:<38}{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
