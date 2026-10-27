from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_incident_triage_reaches_approval():
    response = client.post("/api/incidents", json={"alert_id": "ALERT-001"})
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "awaiting_approval"
    assert run["state"]["proposed_action"] == "restart_service"
    assert run["state"]["approval_id"]


def test_approval_completes_run():
    started = client.post("/api/incidents", json={"alert_id": "ALERT-001"}).json()
    run = client.get(f"/api/runs/{started['run_id']}").json()
    approval_id = run["state"]["approval_id"]

    decided = client.post(
        f"/api/approvals/{approval_id}/decision", json={"decision": "approved"}
    )
    assert decided.status_code == 200

    final = client.get(f"/api/runs/{started['run_id']}").json()
    assert final["status"] == "completed"
    assert "Restarted payments-api" in final["state"]["actions_taken"]


def test_rejection_creates_escalation():
    started = client.post("/api/incidents", json={"alert_id": "ALERT-001"}).json()
    run = client.get(f"/api/runs/{started['run_id']}").json()
    approval_id = run["state"]["approval_id"]

    decided = client.post(
        f"/api/approvals/{approval_id}/decision", json={"decision": "rejected"}
    )
    assert decided.status_code == 200

    final = client.get(f"/api/runs/{started['run_id']}").json()
    assert final["status"] == "completed"
    assert any("escalation ticket" in action.lower() for action in final["state"]["actions_taken"])
