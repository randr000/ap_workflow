"""Integration: the full 20-invoice demo dataset and the 10 headline scenarios."""

from demo.dataset import DEMO_INVOICES
from demo.scenarios import EXPECTED, run_scenarios


def test_dataset_has_twenty_invoices_with_expected_results():
    assert len(DEMO_INVOICES) == 20
    assert set(EXPECTED) == {name for name, _ in DEMO_INVOICES}


def test_all_scenarios_pass():
    results, dataset_errors = run_scenarios()
    assert {name: errs for name, errs in dataset_errors.items() if errs} == {}
    failures = {f"{s.number} {s.name}": s.errors for s in results if not s.passed}
    assert failures == {}
    assert len(results) == 10
