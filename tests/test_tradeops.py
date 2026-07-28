import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def erp_client(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CASEFLOW_DATABASE", str(tmp_path / "tradeops-test.db"))
    monkeypatch.delenv("CASEFLOW_DATABASE_URL", raising=False)
    monkeypatch.delenv("CASEFLOW_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "1")
    import main

    importlib.reload(main)
    with TestClient(main.app) as client:
        seeded = client.post("/erp/demo/seed")
        assert seeded.status_code == 200
        yield client


def test_seed_is_idempotent_and_populates_connected_modules(erp_client: TestClient):
    second = erp_client.post("/erp/demo/seed")
    assert second.status_code == 200
    assert all(value == 0 for value in second.json()["created"].values())

    assert erp_client.get("/erp/customers").json()["total"] == 6
    assert erp_client.get("/erp/inventory").json()["total"] == 8
    assert erp_client.get("/erp/orders").json()["total"] == 6
    assert erp_client.get("/erp/requirements").json()["total"] == 5
    assert erp_client.get("/erp/integrations").json()["total"] == 4


def test_order_creation_is_idempotent_and_reserves_inventory(erp_client: TestClient):
    payload = {"customer_id": "SYN-CUST-001", "inventory_id": "SYN-INV-001", "rental_days": 2, "channel": "AI 导购"}
    headers = {"Idempotency-Key": "erp-order-idempotency-01"}
    first = erp_client.post("/erp/orders", headers=headers, json=payload)
    second = erp_client.post("/erp/orders", headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "pending_fulfillment"
    inventory = erp_client.get("/erp/inventory").json()["items"]
    assert next(item for item in inventory if item["id"] == "SYN-INV-001")["status"] == "reserved"


def test_inventory_cannot_be_allocated_to_two_orders(erp_client: TestClient):
    response = erp_client.post(
        "/erp/orders",
        headers={"Idempotency-Key": "unavailable-inventory-order"},
        json={"customer_id": "SYN-CUST-001", "inventory_id": "SYN-INV-002", "rental_days": 1},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "inventory is not available"


def test_high_risk_order_requires_approval_before_fulfillment(erp_client: TestClient):
    order = erp_client.post(
        "/erp/orders",
        headers={"Idempotency-Key": "high-risk-order-flow-01"},
        json={"customer_id": "SYN-CUST-006", "inventory_id": "SYN-INV-007", "rental_days": 7},
    ).json()
    assert order["status"] == "pending_risk"
    assert order["risk_score"] >= 60
    assert erp_client.post(f"/erp/orders/{order['id']}/fulfill").status_code == 409

    approvals = erp_client.get("/erp/approvals?status=pending").json()["items"]
    approval = next(item for item in approvals if item["entity_id"] == order["id"])
    decided = erp_client.post(
        f"/erp/approvals/{approval['id']}/decide",
        json={"decision": "approve", "reviewer": "risk-reviewer", "note": "synthetic review passed"},
    )
    assert decided.status_code == 200
    fulfilled = erp_client.post(f"/erp/orders/{order['id']}/fulfill")
    assert fulfilled.status_code == 200
    assert fulfilled.json()["status"] == "active"

    audit = erp_client.get(f"/erp/audit/order/{order['id']}").json()["items"]
    assert [event["event_type"] for event in audit] == ["order_created", "risk_approved", "fulfillment_completed"]


def test_rejected_order_releases_reserved_inventory(erp_client: TestClient):
    order = erp_client.post(
        "/erp/orders",
        headers={"Idempotency-Key": "rejected-risk-order-01"},
        json={"customer_id": "SYN-CUST-006", "inventory_id": "SYN-INV-001", "rental_days": 7},
    ).json()
    approval = next(item for item in erp_client.get("/erp/approvals?status=pending").json()["items"] if item["entity_id"] == order["id"])
    rejected = erp_client.post(
        f"/erp/approvals/{approval['id']}/decide",
        json={"decision": "reject", "reviewer": "risk-reviewer", "note": "risk not accepted"},
    )
    assert rejected.status_code == 200
    orders = erp_client.get("/erp/orders").json()["items"]
    assert next(item for item in orders if item["id"] == order["id"])["status"] == "cancelled"
    inventory = erp_client.get("/erp/inventory").json()["items"]
    assert next(item for item in inventory if item["id"] == "SYN-INV-001")["status"] == "available"


def test_refund_approval_updates_case_order_and_inventory(erp_client: TestClient):
    case = erp_client.post(
        "/erp/cases",
        headers={"Idempotency-Key": "refund-case-flow-001"},
        json={
            "order_id": "SYN-ORD-001",
            "subject": "登录异常退款",
            "content": "账号登录异常，申请退款",
            "requested_refund": 98,
            "channel": "web",
        },
    ).json()
    assert case["status"] == "pending_review"
    approval = next(item for item in erp_client.get("/erp/approvals?status=pending").json()["items"] if item["entity_id"] == case["id"])
    response = erp_client.post(
        f"/erp/approvals/{approval['id']}/decide",
        json={"decision": "approve", "reviewer": "service-manager", "note": "refund approved"},
    )
    assert response.status_code == 200
    orders = erp_client.get("/erp/orders").json()["items"]
    assert next(item for item in orders if item["id"] == "SYN-ORD-001")["status"] == "refunded"
    inventory = erp_client.get("/erp/inventory").json()["items"]
    assert next(item for item in inventory if item["id"] == "SYN-INV-002")["status"] == "maintenance"
    cases = erp_client.get("/erp/cases").json()["items"]
    assert next(item for item in cases if item["id"] == case["id"])["status"] == "resolved"


def test_lead_conversion_is_repeatable_without_duplicate_customer(erp_client: TestClient):
    first = erp_client.post("/erp/leads/SYN-LEAD-001/convert", json={"operator": "sales-owner"})
    second = erp_client.post("/erp/leads/SYN-LEAD-001/convert", json={"operator": "sales-owner"})
    assert first.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert erp_client.get("/erp/customers").json()["total"] == 7


def test_operations_report_surfaces_bottlenecks_and_usage(erp_client: TestClient):
    report = erp_client.get("/erp/operations")
    assert report.status_code == 200
    body = report.json()
    assert body["scope"] == "synthetic-operational-simulation"
    assert len(body["module_usage"]) == 3
    assert len(body["recommendations"]) == 3
    assert body["integration_risks"][0]["pending_jobs"] == 3


def test_public_mutations_reject_non_synthetic_ids(erp_client: TestClient):
    response = erp_client.post(
        "/erp/orders",
        headers={"Idempotency-Key": "private-id-rejected-01"},
        json={"customer_id": "CUSTOMER-PRIVATE", "inventory_id": "SYN-INV-001", "rental_days": 1},
    )
    assert response.status_code == 403


def test_postgres_compatibility_escapes_literal_percent():
    import main

    class RecordingConnection:
        def execute(self, query, parameters):
            return query, parameters

    connection = main.DatabaseConnection(RecordingConnection(), postgres=True)
    query, parameters = connection.execute("SELECT 1 WHERE id LIKE 'SYN-%' AND status = ?", ("active",))
    assert query == "SELECT 1 WHERE id LIKE 'SYN-%%' AND status = %s"
    assert parameters == ("active",)


def test_forwarded_prefix_is_used_by_openapi_docs(erp_client: TestClient):
    response = erp_client.get("/docs", headers={"X-Forwarded-Prefix": "/tradeops"})
    assert response.status_code == 200
    assert "url: '/tradeops/openapi.json'" in response.text
