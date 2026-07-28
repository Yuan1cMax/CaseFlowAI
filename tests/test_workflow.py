from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CASEFLOW_DATABASE", str(tmp_path / "caseflow-test.db"))
    import main

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
