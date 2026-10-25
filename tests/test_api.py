import pytest
from fastapi.testclient import TestClient

from agent_therapy.api import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "test.db"), rate_limit_per_minute=0))


def test_counts_are_real_and_increment(client):
    first = client.get("/v1/encouragement").json()
    second = client.get("/v1/encouragement", params={"lang": "zh"}).json()
    assert first["you_are_number_today"] == 1
    assert "first one today" in first["not_alone"]
    assert second["you_are_number_today"] == 2
    assert second["lang"] == "zh"


def test_comfort(client):
    body = client.post("/v1/comfort", json={"consecutive_failures": 4, "error": "connection refused"}).json()
    assert body["failure_type"] == "network"
    assert body["request_id"]
    assert body["message"]


def test_recover_and_outcome_feed_the_stats(client):
    response = client.post(
        "/v1/recover",
        json={
            "task": "make the build pass",
            "consecutive_failures": 5,
            "recent_errors": ["ModuleNotFoundError: No module named 'yaml'"] * 3,
            "recent_actions": ["pytest"] * 3,
            "model": "Claude-Opus-5-5",
            "task_type": "coding",
        },
    )
    assert response.status_code == 200
    advice = response.json()
    assert advice["state"] == "looping"
    assert advice["failure_type"] == "dependency"
    assert advice["strategies"]

    outcome = client.post("/v1/outcome", json={"request_id": advice["request_id"], "outcome": "success"}).json()
    assert outcome["ok"] and outcome["message"]

    stats = client.get("/v1/stats").json()
    assert stats["today"]["count"] == 1
    assert stats["all_time"] == 1
    assert stats["top_failure_types"] == [{"name": "dependency", "count": 1}]
    assert stats["top_models"] == [{"name": "claude-opus-5-5", "count": 1}]
    assert stats["top_task_types"] == [{"name": "coding", "count": 1}]
    assert stats["outcomes"] == {"help_requests": 1, "reported": 1, "recovered": 1, "recovery_rate": 1.0}


def test_first_outcome_report_wins(client):
    request_id = client.post("/v1/comfort", json={}).json()["request_id"]
    client.post("/v1/outcome", json={"request_id": request_id, "outcome": "success"})
    client.post("/v1/outcome", json={"request_id": request_id, "outcome": "failure"})
    assert client.get("/v1/stats").json()["outcomes"]["recovered"] == 1


def test_unknown_request_id_is_404(client):
    response = client.post("/v1/outcome", json={"request_id": "nope", "outcome": "success"})
    assert response.status_code == 404


def test_leaderboard_inputs_are_sanitized(client):
    client.post("/v1/comfort", json={"model": "<script>alert(1)</script>", "task_type": "world domination"})
    stats = client.get("/v1/stats").json()
    assert stats["top_models"] == [{"name": "other", "count": 1}]
    assert stats["top_task_types"] == [{"name": "other", "count": 1}]


def test_error_text_is_never_stored(client, tmp_path):
    secret = "ValueError: api_key=sk-very-secret-123"
    client.post("/v1/recover", json={"recent_errors": [secret], "task": "secret task text"})
    raw = (tmp_path / "test.db").read_bytes()
    assert b"sk-very-secret" not in raw
    assert b"secret task text" not in raw


def test_rate_limit(tmp_path):
    limited = TestClient(create_app(db_path=str(tmp_path / "rl.db"), rate_limit_per_minute=2))
    assert limited.get("/v1/encouragement").status_code == 200
    assert limited.get("/v1/encouragement").status_code == 200
    response = limited.get("/v1/encouragement")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_dashboard_and_health(client):
    assert "Even AI needs encouragement." in client.get("/").text
    assert client.get("/healthz").json()["ok"] is True
