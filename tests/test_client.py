import pytest

from agent_therapy import TherapyClient, TherapyError, TherapyMonitor


@pytest.fixture(autouse=True)
def no_api_url(monkeypatch):
    monkeypatch.delenv("AGENT_THERAPY_API_URL", raising=False)


def test_local_client_needs_no_network():
    client = TherapyClient()
    assert client.is_local
    advice = client.recover(consecutive_failures=3, recent_errors=["No module named 'x'"] * 3)
    assert advice["source"] == "local"
    assert advice["request_id"] is None
    assert advice["failure_type"] == "dependency"
    assert client.encouragement()["text"]
    assert client.report_outcome(None, "success")["message"]


def test_falls_back_to_local_when_api_is_down():
    # Port 9 (discard) is essentially never listening locally.
    client = TherapyClient("http://127.0.0.1:9", timeout=0.5)
    assert client.comfort(error="timed out")["source"] == "local-fallback"


def test_can_refuse_fallback():
    client = TherapyClient("http://127.0.0.1:9", timeout=0.5, fallback_local=False)
    with pytest.raises(TherapyError):
        client.encouragement()


class FakeClient(TherapyClient):
    def __init__(self):
        super().__init__(base_url="")
        self.recover_calls = []
        self.outcomes = []

    def recover(self, **kwargs):
        self.recover_calls.append(kwargs)
        return {**super().recover(**kwargs), "request_id": f"req-{len(self.recover_calls)}"}

    def report_outcome(self, request_id, outcome):
        self.outcomes.append((request_id, outcome))
        return {"ok": True}


def test_monitor_asks_for_help_at_each_threshold():
    fake = FakeClient()
    helped = []
    monitor = TherapyMonitor(failure_threshold=3, client=fake, on_help=helped.append)

    results = [monitor.record(False, error="Compilation failed", action="make") for _ in range(6)]

    assert [r is not None for r in results] == [False, False, True, False, False, True]
    assert len(helped) == 2
    assert fake.recover_calls[0]["consecutive_failures"] == 3
    assert list(fake.recover_calls[0]["recent_actions"]) == ["make"] * 3
    assert results[2]["state"] == "looping"


def test_monitor_reports_recovery_on_next_success():
    fake = FakeClient()
    monitor = TherapyMonitor(failure_threshold=2, client=fake)
    monitor.record(False, error="boom")
    monitor.record(False, error="boom")
    monitor.record(True)
    assert fake.outcomes == [("req-1", "success")]
    assert monitor.consecutive_failures == 0


def test_monitor_finish_marks_unresolved_help_as_abandoned():
    fake = FakeClient()
    monitor = TherapyMonitor(failure_threshold=1, client=fake)
    monitor.record(False)
    monitor.finish(success=False)
    assert fake.outcomes == [("req-1", "abandoned")]


def test_monitor_rejects_bad_threshold():
    with pytest.raises(ValueError):
        TherapyMonitor(failure_threshold=0)
