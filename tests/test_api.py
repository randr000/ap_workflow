import pytest
from fastapi.testclient import TestClient

from app.ai.mock_provider import MockLLMProvider
from app.api.routes import app, get_service
from app.config import Settings
from app.db.database import IN_MEMORY_URL
from app.services.workflow_service import create_workflow_service
from demo.dataset import load_demo_documents


@pytest.fixture
def client():
    svc = create_workflow_service(IN_MEMORY_URL, reset=True, settings=Settings(), provider=MockLLMProvider())
    app.dependency_overrides[get_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.clear()


def docs():
    return dict(load_demo_documents())


def test_process_approve_and_post_via_api(client):
    r = client.post("/invoices", json={"document": docs()["03_acme_INV-1004.txt"]})
    assert r.status_code == 201
    body = r.json()
    assert body["invoice"]["status"] == "PENDING_APPROVAL"
    assert body["invoice"]["total"] == "540.00"  # Decimal serialized as string, never float
    inv = body["invoice"]["id"]

    blocked = client.post(f"/invoices/{inv}/post", json={"user": "Dana Brooks"})
    assert blocked.status_code == 409 and "POSTING BLOCKED" in blocked.json()["detail"]

    assert client.post(f"/invoices/{inv}/approve", json={"user": "Dana Brooks"}).status_code == 409
    assert client.post(f"/invoices/{inv}/approve", json={"user": "Priya Shah"}).json()["invoice"]["status"] == "VALIDATED"
    assert client.post(f"/invoices/{inv}/post", json={"user": "Dana Brooks"}).json()["invoice"]["status"] == "POSTED"

    metrics = client.get("/metrics").json()
    assert metrics["posted_amount"] == "540.00"
    assert any(e["action"] == "Journal entry posted" for e in client.get(f"/audit?invoice_id={inv}").json())


def test_errors_are_explicit(client):
    assert client.get("/invoices/AP-9999").status_code == 404
    client.post("/invoices", json={"document": docs()["01_acme_INV-1001.txt"]})
    assert client.post("/invoices/AP-0001/post", json={"user": "Nobody"}).status_code == 400
    assert client.post("/invoices", json={"document": "not an invoice"}).status_code == 409


def test_exception_queue(client):
    client.post("/invoices", json={"document": docs()["08_horizon_HSG-0930.txt"]})
    queue = client.get("/exceptions").json()
    assert [e["type"] for e in queue] == ["LOW_CODING_CONFIDENCE"]
    assert client.get("/health").json()["status"] == "ok"


def test_structured_intake_and_role_inbox(client):
    invoice = {"vendor_name": "Brightpath Consulting Group", "vendor_tax_id": "36-4567890",
               "invoice_number": "BCG-0913", "invoice_date": "2026-09-20", "po_number": "PO-5004",
               "lines": [{"description": "Process improvement consulting (hours)", "quantity": 48,
                          "unit_price": "150.00"}]}
    r = client.post("/invoices/structured", json={"user": "Dana Brooks", "invoice": invoice})
    assert r.status_code == 201
    body = r.json()
    assert body["intake"] == "STRUCTURED_UPLOAD" and body["invoice"]["total"] == "7200.00"
    inv = body["invoice"]["id"]

    assert [i["id"] for i in client.get("/inbox?user=Priya Shah").json()["waiting"]] == [inv]
    client.post(f"/invoices/{inv}/approve", json={"user": "Priya Shah"})
    marcus = client.get("/inbox?user=Marcus Lee").json()
    assert [i["id"] for i in marcus["waiting"]] == [inv] and marcus["ready_to_post"] == []
    client.post(f"/invoices/{inv}/approve", json={"user": "Marcus Lee"})
    assert [i["id"] for i in client.get("/inbox?user=Dana Brooks").json()["ready_to_post"]] == [inv]

    bad = client.post("/invoices/structured", json={"user": "Dana Brooks", "invoice": {"vendor_name": "x"}})
    assert bad.status_code == 422
