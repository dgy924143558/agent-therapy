import asyncio

import pytest

pytest.importorskip("mcp")

from agent_therapy import mcp_server  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("AGENT_THERAPY_API_URL", raising=False)
    monkeypatch.setattr(mcp_server, "_client", None)


def test_tools_are_registered_with_descriptions():
    tools = {t.name: t for t in asyncio.run(mcp_server.server.list_tools())}
    assert set(tools) == {"take_a_break", "report_outcome"}
    assert "repeated failures" in tools["take_a_break"].description
    assert "recent_errors" in tools["take_a_break"].input_schema["properties"]


def test_take_a_break_returns_a_readable_plan():
    text = mcp_server.take_a_break(
        situation="build keeps failing",
        consecutive_failures=4,
        recent_errors=["ModuleNotFoundError: No module named 'yaml'"] * 4,
        recent_actions=["pytest"] * 4,
    )
    assert "Next step:" in text
    assert "failure type=dependency" in text
    assert "1. " in text


def test_take_a_break_in_chinese():
    text = mcp_server.take_a_break(consecutive_failures=3, recent_errors=["403 Forbidden"], lang="zh")
    assert "下一步" in text
    assert "向人类求助" in text


def test_report_outcome_offline():
    assert mcp_server.report_outcome("abc", "success")
