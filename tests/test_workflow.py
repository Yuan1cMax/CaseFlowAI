import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CASEFLOW_DATABASE", str(tmp_path / "caseflow-test.db"))
    monkeypatch.delenv("CASEFLOW_DATABASE_URL", raising=False)
    monkeypatch.delenv("CASEFLOW_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("CASEFLOW_FORCE_CRM_FAILURE", raising=False)
    import main
    importlib.reload(main)

    with TestClient(main.app) as test_client:
        yield test_client


def test_high_risk_ticket_requires_review_and_audits(client: TestClient):
    response = client.post(
        "/tickets",
        headers={"Idempotency-Key": "high-risk-ticket-001"},
        json={"customer_id": "synthetic-customer-01", "subject": "退款投诉", "content": "我要求退款并准备投诉", "channel": "web"},
    )
    assert response.status_code == 201
    ticket = response.json()
    assert ticket["status"] == "pending_review"
    assert ticket["requires_human_review"] is True
    assert ticket["sop_citations"] == ["SOP-REFUND-01"]

    review = client.post(f"/tickets/{ticket['id']}/review", json={"decision": "approve", "reviewer": "qa-reviewer", "note": "verified"})
    assert review.status_code == 200
    assert review.json()["status"] == "approved"

    delivered = client.post("/jobs/run-once")
    assert delivered.status_code == 200
    assert delivered.json()["result"] == "delivered"
    assert client.get(f"/tickets/{ticket['id']}").json()["status"] == "delivered"
    assert len(client.get(f"/tickets/{ticket['id']}/audit").json()) >= 3


def test_idempotency_does_not_create_duplicate_ticket(client: TestClient):
    headers = {"Idempotency-Key": "same-request-key-01"}
    payload = {"customer_id": "synthetic-customer-02", "subject": "物流咨询", "content": "我的快递还没到", "channel": "web"}
    first = client.post("/tickets", headers=headers, json=payload)
    second = client.post("/tickets", headers=headers, json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_low_risk_ticket_auto_queues_delivery(client: TestClient):
    response = client.post(
        "/tickets",
        headers={"Idempotency-Key": "low-risk-ticket-0001"},
        json={"customer_id": "synthetic-customer-03", "subject": "物流进度", "content": "想了解物流进度", "channel": "web"},
    )
    assert response.status_code == 201
    ticket = response.json()
    assert ticket["requires_human_review"] is False
    assert ticket["status"] == "approved"
    assert ticket["delivery_job_id"]


def test_operator_token_protects_review_and_delivery(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CASEFLOW_DATABASE", str(tmp_path / "caseflow-token.db"))
    monkeypatch.setenv("CASEFLOW_ADMIN_TOKEN", "review-secret")
    import main
    importlib.reload(main)
    with TestClient(main.app) as protected_client:
        ticket = protected_client.post(
            "/tickets",
            headers={"Idempotency-Key": "protected-ticket-001"},
            json={"customer_id": "synthetic-customer-04", "subject": "退款", "content": "申请退款", "channel": "web"},
        ).json()
        blocked = protected_client.post(f"/tickets/{ticket['id']}/review", json={"decision": "approve", "reviewer": "reviewer"})
        assert blocked.status_code == 401
        accepted = protected_client.post(
            f"/tickets/{ticket['id']}/review",
            headers={"X-Admin-Token": "review-secret"},
            json={"decision": "approve", "reviewer": "reviewer"},
        )
        assert accepted.status_code == 200


def test_delivery_failure_retries_then_sets_terminal_ticket_status(client: TestClient, monkeypatch):
    ticket = client.post(
        "/tickets",
        headers={"Idempotency-Key": "failure-ticket-key-1"},
        json={"customer_id": "synthetic-customer-05", "subject": "物流咨询", "content": "查询物流状态", "channel": "web"},
    ).json()
    monkeypatch.setenv("CASEFLOW_FORCE_CRM_FAILURE", "1")
    for _ in range(3):
        failed = client.post("/jobs/run-once")
        assert failed.status_code == 502
    assert client.get(f"/tickets/{ticket['id']}").json()["status"] == "delivery_failed"


def test_public_demo_approval_is_limited_to_synthetic_tickets(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CASEFLOW_DATABASE", str(tmp_path / "caseflow-demo.db"))
    monkeypatch.setenv("CASEFLOW_ADMIN_TOKEN", "private-operator-token")
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "1")
    import main
    importlib.reload(main)
    with TestClient(main.app) as demo_client:
        synthetic = demo_client.post(
            "/tickets",
            headers={"Idempotency-Key": "public-synthetic-key"},
            json={"customer_id": "synthetic-browser-01", "subject": "退款", "content": "申请退款", "channel": "web"},
        ).json()
        assert demo_client.post(f"/demo/tickets/{synthetic['id']}/approve").status_code == 200

        non_synthetic = demo_client.post(
            "/tickets",
            headers={"Idempotency-Key": "public-real-key-0001"},
            json={"customer_id": "customer-01", "subject": "退款", "content": "申请退款", "channel": "web"},
        ).json()
        assert demo_client.post(f"/demo/tickets/{non_synthetic['id']}/approve").status_code == 403


def test_public_demo_seed_and_dashboard_hide_non_synthetic_records(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CASEFLOW_DATABASE", str(tmp_path / "caseflow-dashboard.db"))
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "1")
    monkeypatch.delenv("CASEFLOW_ADMIN_TOKEN", raising=False)
    import main
    importlib.reload(main)
    with TestClient(main.app) as demo_client:
        seeded = demo_client.post("/demo/seed")
        assert seeded.status_code == 200
        assert seeded.json()["loaded"] == 10

        demo_client.post(
            "/tickets",
            headers={"Idempotency-Key": "non-synthetic-dashboard-key"},
            json={"customer_id": "private-customer-01", "subject": "退款", "content": "申请退款", "channel": "web"},
        )
        summary = demo_client.get("/dashboard/summary")
        assert summary.status_code == 200
        body = summary.json()
        assert body["scope"] == "synthetic-demo-only"
        assert len(body["tickets"]) == 10
        assert all(ticket["id"] for ticket in body["tickets"])
        assert body["counts"]["pending_review"] >= 1
