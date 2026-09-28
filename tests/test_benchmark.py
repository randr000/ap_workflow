from demo.benchmark import MIX, generate, run_benchmark


def test_generator_is_deterministic_and_sized():
    a, _ = generate()
    b, _ = generate()
    assert len(a) == sum(MIX.values()) == 100
    assert [i.spec.invoice_number for i in a] == [i.spec.invoice_number for i in b]
    # Every duplicate is processed after its original.
    for idx, inv in enumerate(a):
        if inv.duplicate_of is not None:
            original_number = inv.spec.invoice_number.removesuffix("-R")
            assert any(o.spec.invoice_number == original_number for o in a[:idx])


def test_benchmark_metrics():
    m = run_benchmark()
    assert m["total_invoices"] == 100
    assert m["expected_outcome_agreement"] == 1.0
    assert m["duplicate_detection_rate"] == 1.0 and m["false_duplicate_flags"] == 0
    assert m["straight_through_invoices"] == MIX["STRAIGHT_THROUGH"] + MIX["PRICE_WITHIN_TOLERANCE"]
    assert 0.9 <= m["classification_accuracy"] < 1.0  # vague invoices are genuinely hard
    assert "Synthetic benchmark" in m["disclaimer"]
