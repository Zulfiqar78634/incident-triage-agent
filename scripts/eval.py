"""Offline golden-set evaluation.

Run: python -m scripts.eval

This checks that agent behavior remains stable across prompt, model, or tool changes.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

GOLDEN_SET = [
    {
        "alert_id": "ALERT-001",
        "expected_status": "awaiting_approval",
        "expected_action": "restart_service",
        "min_confidence": 0.7,
    }
]


def evaluate() -> None:
    passed = 0
    for case in GOLDEN_SET:
        started = client.post("/api/incidents", json={"alert_id": case["alert_id"]}).json()
        run = client.get(f"/api/runs/{started['run_id']}").json()
        checks = [
            run["status"] == case["expected_status"],
            run["state"]["proposed_action"] == case["expected_action"],
            run["state"]["confidence"] >= case["min_confidence"],
        ]
        result = "PASS" if all(checks) else "FAIL"
        passed += all(checks)
        print(f"{result} {case['alert_id']} status={run['status']} action={run['state']['proposed_action']}")
    print(f"\n{passed}/{len(GOLDEN_SET)} scenarios passed")


if __name__ == "__main__":
    evaluate()
