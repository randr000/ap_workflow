from app.config import ApprovalThresholds, ConfidenceThresholds
from app.domain.approval import ApproverRole
from app.domain.exception import ExceptionType
from app.services.approvals import route_invoice
from app.services.exceptions import new_exception

APPROVALS = ApprovalThresholds()
CONF = ConfidenceThresholds()


def roles(route):
    return [s.role for s in route.steps]


def exc(type_, seq=1):
    return new_exception("AP-0001", seq, type_, f"{type_} detected")


def test_low_value_clean_invoice_is_auto_approval_eligible(invoice_factory):
    route = route_invoice(invoice_factory(), [], 0.97, True, APPROVALS, CONF)
    assert route.auto_approval_eligible
    assert route.steps == []


def test_mid_value_invoice_routes_to_department_manager(invoice_factory):
    inv = invoice_factory(lines=[("Consulting", "10", "425.00")])  # $4,250
    route = route_invoice(inv, [], 0.96, None, APPROVALS, CONF)
    assert not route.auto_approval_eligible
    assert roles(route) == [ApproverRole.DEPARTMENT_MANAGER]


def test_high_value_invoice_routes_to_controller(invoice_factory):
    inv = invoice_factory(lines=[("Legal retainer", "1", "18500.00")])
    route = route_invoice(inv, [], 0.96, None, APPROVALS, CONF)
    assert roles(route) == [ApproverRole.CONTROLLER]


def test_threshold_boundaries(invoice_factory):
    at_1000 = invoice_factory(lines=[("x", "1", "1000.00")])
    at_10000 = invoice_factory(lines=[("x", "1", "10000.00")])
    assert roles(route_invoice(at_1000, [], 0.96, None, APPROVALS, CONF)) == [ApproverRole.DEPARTMENT_MANAGER]
    assert roles(route_invoice(at_10000, [], 0.96, None, APPROVALS, CONF)) == [ApproverRole.CONTROLLER]


def test_price_exception_routes_to_procurement(invoice_factory):
    inv = invoice_factory(lines=[("Ergonomic desk mat", "20", "27.00")])  # $540
    route = route_invoice(inv, [exc(ExceptionType.PRICE_VARIANCE)], 0.97, False, APPROVALS, CONF)
    assert not route.auto_approval_eligible
    assert roles(route) == [ApproverRole.PROCUREMENT_MANAGER]


def test_duplicate_routes_to_ap_exception_queue(invoice_factory):
    route = route_invoice(invoice_factory(), [exc(ExceptionType.DUPLICATE_INVOICE)], 0.97, True, APPROVALS, CONF)
    assert roles(route) == [ApproverRole.AP_EXCEPTION_QUEUE]


def test_low_confidence_routes_to_ap_review(invoice_factory):
    review_band = route_invoice(invoice_factory(), [], 0.80, None, APPROVALS, CONF)
    assert roles(review_band) == [ApproverRole.AP_REVIEW_QUEUE]
    assert not review_band.auto_approval_eligible
    low = route_invoice(invoice_factory(), [exc(ExceptionType.LOW_CODING_CONFIDENCE)], 0.55, None, APPROVALS, CONF)
    assert roles(low) == [ApproverRole.AP_REVIEW_QUEUE]


def test_missing_po_routes_to_ap_review_and_controller(invoice_factory):
    inv = invoice_factory(po_number=None, lines=[("Conveyor belt assembly", "1", "4250.00")])
    route = route_invoice(inv, [exc(ExceptionType.MISSING_PO)], 0.93, False, APPROVALS, CONF)
    # Controller supersedes Department Manager.
    assert roles(route) == [ApproverRole.AP_REVIEW_QUEUE, ApproverRole.CONTROLLER]
    assert "PO match: No" in route.rationale


def test_multiple_exceptions_ordered_ap_first_authority_last(invoice_factory):
    inv = invoice_factory(lines=[("x", "1", "7200.00")])
    route = route_invoice(inv, [exc(ExceptionType.QUANTITY_VARIANCE, 1), exc(ExceptionType.INVALID_DIMENSION, 2)],
                          0.97, False, APPROVALS, CONF)
    assert roles(route) == [ApproverRole.AP_REVIEW_QUEUE, ApproverRole.PROCUREMENT_MANAGER,
                            ApproverRole.DEPARTMENT_MANAGER]


def test_route_explains_itself(invoice_factory):
    route = route_invoice(invoice_factory(lines=[("x", "1", "4250.00")]), [], 0.96, None, APPROVALS, CONF)
    assert "Invoice amount: $4,250.00" in route.rationale
    assert "$4,250.00" in route.steps[0].reason
