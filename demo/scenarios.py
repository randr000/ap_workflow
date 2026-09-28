"""Scenario runner.

    python -m demo.scenarios            # summary table
    python -m demo.scenarios -v         # include failure details and the full dataset check

Processes all 20 demo invoices into a fresh in-memory database, then checks
every invoice against its deterministic expected result and runs the 10
headline scenarios (including human actions such as approve / correct / post).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from app.ai.mock_provider import MockLLMProvider
from app.config import Settings
from app.db.database import IN_MEMORY_URL
from app.domain.approval import ApproverRole as R, get_user
from app.domain.case import InvoiceCase
from app.domain.exception import ExceptionType as X
from app.domain.workflow import WorkflowState as S
from app.services.workflow_service import (
    APWorkflowService,
    PostingBlockedError,
    WorkflowError,
    create_workflow_service,
)
from demo.dataset import load_demo_documents

AP = get_user("Dana Brooks")
PROCUREMENT = get_user("Priya Shah")
DEPT_MGR = get_user("Marcus Lee")
CONTROLLER = get_user("Elena Torres")


@dataclass(frozen=True)
class Expected:
    status: S
    exceptions: frozenset[X] = frozenset()
    route: tuple[R, ...] = ()


def E(status, exceptions=(), route=()):
    return Expected(status, frozenset(exceptions), tuple(route))


# Deterministic expected result for every invoice immediately after processing.
EXPECTED: dict[str, Expected] = {
    "01_acme_INV-1001.txt": E(S.VALIDATED),
    "02_acme_INV-1002.txt": E(S.VALIDATED),
    "03_acme_INV-1004.txt": E(S.PENDING_APPROVAL, [X.PRICE_VARIANCE], [R.PROCUREMENT_MANAGER]),
    "04_brightpath_BCG-0912.txt": E(S.PENDING_APPROVAL, [X.QUANTITY_VARIANCE], [R.PROCUREMENT_MANAGER, R.DEPARTMENT_MANAGER]),
    "05_pinnacle_PIS-33017.txt": E(S.PENDING_APPROVAL, [X.QUANTITY_VARIANCE], [R.PROCUREMENT_MANAGER, R.DEPARTMENT_MANAGER]),
    "06_pinnacle_PIS-33102.txt": E(S.PENDING_APPROVAL, [X.MISSING_PO], [R.AP_REVIEW_QUEUE, R.CONTROLLER]),
    "07_acme_INV-1001_resubmitted.txt": E(S.PENDING_APPROVAL, [X.DUPLICATE_INVOICE], [R.AP_EXCEPTION_QUEUE]),
    "08_horizon_HSG-0930.txt": E(S.PENDING_APPROVAL, [X.LOW_CODING_CONFIDENCE], [R.AP_REVIEW_QUEUE]),
    "09_summit_SLP-2026-0918.txt": E(S.PENDING_APPROVAL, [], [R.CONTROLLER]),
    "10_skyway_SKY-4471.txt": E(S.PENDING_APPROVAL, [X.INVALID_DIMENSION], [R.AP_REVIEW_QUEUE, R.DEPARTMENT_MANAGER]),
    "11_nimbus_NCS-2026-09.txt": E(S.PENDING_APPROVAL, [], [R.DEPARTMENT_MANAGER]),
    "12_metropower_MPL-778201-0926.txt": E(S.PENDING_APPROVAL, [], [R.DEPARTMENT_MANAGER]),
    "13_coastal_CP-2026-09.txt": E(S.PENDING_APPROVAL, [], [R.DEPARTMENT_MANAGER]),
    "14_clouddesk_CD-2026-0915.txt": E(S.VALIDATED),
    "15_acme_INV-1015.txt": E(S.VALIDATED),
    "16_pinnacle_PIS-33240.txt": E(S.PENDING_APPROVAL, [X.MISSING_RECEIPT], [R.PROCUREMENT_MANAGER, R.DEPARTMENT_MANAGER]),
    "17_metrosupply_MS-88120.txt": E(S.PENDING_APPROVAL, [X.VENDOR_AMBIGUITY], [R.AP_EXCEPTION_QUEUE]),
    "18_redwood_RCA-5521.txt": E(S.PENDING_APPROVAL, [X.INVALID_ACCOUNT], [R.AP_REVIEW_QUEUE, R.DEPARTMENT_MANAGER]),
    "19_clouddesk_CD-2026-0915-R.txt": E(S.PENDING_APPROVAL, [X.DUPLICATE_INVOICE], [R.AP_EXCEPTION_QUEUE]),
    "20_nimbus_NCS-LIC-2026.txt": E(S.PENDING_APPROVAL, [], [R.AP_REVIEW_QUEUE, R.DEPARTMENT_MANAGER]),
}


def check_expected(case: InvoiceCase, exp: Expected) -> list[str]:
    errors = []
    if case.status != exp.status:
        errors.append(f"status {case.status} != {exp.status}")
    got = frozenset(e.type for e in case.exceptions)
    if got != exp.exceptions:
        errors.append(f"exceptions {sorted(got)} != {sorted(exp.exceptions)}")
    route = tuple(s.role for s in case.approval_route.steps) if case.approval_route else ()
    if route != exp.route:
        errors.append(f"route {list(route)} != {list(exp.route)}")
    return errors


def run_demo_batch(svc: APWorkflowService) -> dict[str, InvoiceCase]:
    return {name: svc.process_document(text, source=f"demo:{name}") for name, text in load_demo_documents()}


# --- scenario-specific checks (run after the whole batch is processed) -------

def _expect_raises(exc_type, fn, *args) -> list[str]:
    try:
        fn(*args)
    except exc_type:
        return []
    return [f"expected {exc_type.__name__} from {fn.__name__}"]


def s1_perfect_match(svc, case):
    errs = [] if case.straight_through else ["not straight-through"]
    errs += [] if case.match_result and case.match_result.passed else ["PO match did not pass"]
    posted = svc.post(case.id, AP)
    je = posted.journal_entry
    errs += [] if posted.status == S.POSTED else [f"post -> {posted.status}"]
    errs += [] if je.total_debits == je.total_credits == Decimal("500.00") else ["JE not balanced at $500"]
    return errs


def s2_price_within_tolerance(svc, case):
    m = case.match_result
    return [] if (m.total_price_variance == Decimal("10.00") and m.price_tolerance == Decimal("25.00")
                  and not case.exceptions) else ["variance/tolerance mismatch"]


def s3_price_exception(svc, case):
    ev = case.exceptions[0].evidence
    errs = [] if (ev["variance"], ev["tolerance"]) == ("40.00", "25.00") else [f"evidence {ev}"]
    approved = svc.approve(case.id, PROCUREMENT, "Accepted price increase")
    return errs + ([] if approved.status == S.VALIDATED else [f"after approval {approved.status}"])


def s4_quantity_exception(svc, case):
    ev = case.exceptions[0].evidence
    return [] if (ev["basis"], ev["ordered"], ev["invoiced"]) == ("ordered", "40", "48") else [f"evidence {ev}"]


def s5_three_way_failure(svc, case):
    ev = case.exceptions[0].evidence
    return [] if (ev["basis"], ev["received"], ev["invoiced"]) == ("received", "80", "100") else [f"evidence {ev}"]


def s6_non_po(svc, case):
    return [] if case.match_result is None and case.invoice.po_number is None else ["unexpected PO match"]


def s7_duplicate(svc, case):
    exc = case.exceptions[0]
    errs = [] if exc.blocking and exc.evidence["confidence"] == "HIGH" else ["duplicate not HIGH/blocking"]
    errs += _expect_raises(WorkflowError, svc.approve, case.id, AP)
    errs += _expect_raises(PostingBlockedError, svc.post, case.id, AP)
    return errs


def s8_low_confidence(svc, case):
    return [] if case.classification.confidence < 0.75 else [f"confidence {case.classification.confidence}"]


def s9_high_value(svc, case):
    errs = _expect_raises(WorkflowError, svc.approve, case.id, DEPT_MGR)
    approved = svc.approve(case.id, CONTROLLER)
    return errs + ([] if approved.status == S.VALIDATED else [f"after approval {approved.status}"])


def s10_invalid_dimension(svc, case):
    errs = _expect_raises(WorkflowError, svc.approve, case.id, AP)
    svc.correct_coding(case.id, AP, "6400", "Sales", "New York")
    svc.approve(case.id, AP)
    final = svc.approve(case.id, DEPT_MGR)
    return errs + ([] if final.status == S.VALIDATED else [f"after correction {final.status}"])


@dataclass
class Scenario:
    number: int
    name: str
    filename: str
    check: Callable[[APWorkflowService, InvoiceCase], list[str]]
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors


SCENARIOS = [
    Scenario(1, "Perfect Match", "01_acme_INV-1001.txt", s1_perfect_match),
    Scenario(2, "Price Within Tolerance", "02_acme_INV-1002.txt", s2_price_within_tolerance),
    Scenario(3, "Price Exception", "03_acme_INV-1004.txt", s3_price_exception),
    Scenario(4, "Quantity Exception", "04_brightpath_BCG-0912.txt", s4_quantity_exception),
    Scenario(5, "Three-Way Match Failure", "05_pinnacle_PIS-33017.txt", s5_three_way_failure),
    Scenario(6, "Non-PO", "06_pinnacle_PIS-33102.txt", s6_non_po),
    Scenario(7, "Duplicate", "07_acme_INV-1001_resubmitted.txt", s7_duplicate),
    Scenario(8, "Low Confidence", "08_horizon_HSG-0930.txt", s8_low_confidence),
    Scenario(9, "High Value Approval", "09_summit_SLP-2026-0918.txt", s9_high_value),
    Scenario(10, "Invalid Dimension", "10_skyway_SKY-4471.txt", s10_invalid_dimension),
]


def run_scenarios() -> tuple[list[Scenario], dict[str, list[str]]]:
    svc = create_workflow_service(IN_MEMORY_URL, reset=True, settings=Settings(), provider=MockLLMProvider())
    cases = run_demo_batch(svc)
    dataset_errors = {name: check_expected(case, EXPECTED[name]) for name, case in cases.items()}
    results = []
    for sc in SCENARIOS:
        scenario = Scenario(sc.number, sc.name, sc.filename, sc.check)
        scenario.errors = list(dataset_errors[sc.filename])
        if scenario.passed:
            try:
                scenario.errors += sc.check(svc, svc.get(cases[sc.filename].id))
            except Exception as e:  # report, don't crash the runner
                scenario.errors.append(f"{type(e).__name__}: {e}")
        results.append(scenario)
    return results, dataset_errors


def main(argv: list[str] | None = None) -> int:
    verbose = "-v" in (argv if argv is not None else sys.argv[1:])
    results, dataset_errors = run_scenarios()
    print("AP AUTOMATION DEMO\n")
    for sc in results:
        print(f"Scenario {sc.number:<3}{sc.name:<27}{'PASS' if sc.passed else 'FAIL'}")
        if verbose or not sc.passed:
            for err in sc.errors:
                print(f"      - {err}")
    ok = sum(1 for errs in dataset_errors.values() if not errs)
    print(f"\nDataset check: {ok}/{len(dataset_errors)} invoices match expected results")
    if verbose or ok != len(dataset_errors):
        for name, errs in dataset_errors.items():
            if errs:
                print(f"  {name}: " + "; ".join(errs))
    all_ok = all(sc.passed for sc in results) and ok == len(dataset_errors)
    print("\nALL SCENARIOS PASSED" if all_ok else "\nSCENARIO FAILURES DETECTED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
