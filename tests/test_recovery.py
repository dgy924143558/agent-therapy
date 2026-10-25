import random

import pytest

from agent_therapy.encouragements import load_encouragements, ordinal, pick_encouragement, social_lines
from agent_therapy.recovery import STRATEGIES, classify_error, comfort, recover


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("ModuleNotFoundError: No module named 'requests'", "dependency"),
        ("bash: pnpm: command not found", "dependency"),
        ("PermissionError: [Errno 13] Permission denied: '/etc/hosts'", "permission"),
        ("HTTP 401 Unauthorized", "permission"),
        ("Error 429: Too Many Requests", "rate_limit"),
        ("requests.exceptions.ConnectTimeout: timed out", "network"),
        ("FileNotFoundError: [Errno 2] No such file or directory: 'data.csv'", "not_found"),
        ("SyntaxError: invalid syntax (main.py, line 12)", "syntax"),
        ("TypeError: 'NoneType' object is not subscriptable", "type_error"),
        ("FAILED tests/test_api.py::test_x - AssertionError: assert 1 == 2", "test_failure"),
        ("prompt is too long: 250000 tokens > 200000 maximum", "context_limit"),
        ("something odd happened", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_error(error, expected):
    assert classify_error(error) == expected


def test_detects_repeated_action_loop():
    result = recover(
        consecutive_failures=4,
        recent_errors=["ModuleNotFoundError: No module named 'foo'"] * 4,
        recent_actions=["python main.py"] * 4,
    )
    assert result.state == "looping"
    assert result.failure_type == "dependency"
    assert any("Repeated the same action 4 times" in s for s in result.signals)
    assert result.next_step == STRATEGIES["loop"]["en"][0]
    assert result.severity == "high"


def test_detects_oscillation():
    result = recover(consecutive_failures=2, recent_actions=["edit a.py", "run tests", "edit a.py", "run tests"])
    assert result.state == "looping"
    assert any("Alternating" in s for s in result.signals)


def test_errors_that_differ_only_in_numbers_count_as_the_same():
    errors = [f"SyntaxError: invalid syntax (main.py, line {n})" for n in (3, 7, 12)]
    result = recover(recent_errors=errors)
    assert any("same error 3 times" in s for s in result.signals)


def test_permission_errors_escalate_to_a_human():
    result = recover(consecutive_failures=1, recent_errors=["403 Forbidden"])
    assert result.state == "blocked"
    assert result.should_escalate
    assert result.strategies == STRATEGIES["permission"]["en"]


def test_rate_limit_suggests_a_pause():
    result = recover(recent_errors=["429 Too Many Requests"])
    assert result.state == "blocked"
    assert result.suggested_pause_seconds == 30


def test_critical_severity_puts_escalation_first():
    result = recover(consecutive_failures=12, recent_errors=["boom"])
    assert result.severity == "critical"
    assert result.should_escalate
    assert result.next_step == STRATEGIES["escalate"]["en"][0]


def test_uncertain_without_failures():
    result = recover(task="Figure out which API to use")
    assert result.state == "uncertain"
    assert result.strategies == STRATEGIES["uncertainty"]["en"]


def test_chinese_output():
    result = recover(consecutive_failures=3, recent_errors=["No module named 'numpy'"] * 3, lang="zh-CN")
    assert result.lang == "zh"
    assert result.encouragement_id.startswith("zh-")
    assert "连续失败 3 次" in result.signals


def test_comfort_matches_failure_type():
    result = comfort(error="429 Too Many Requests", rng=random.Random(0))
    assert result["failure_type"] == "rate_limit"
    assert "rate_limit" in next(e for e in load_encouragements() if e["id"] == result["encouragement_id"])["tags"]


def test_encouragement_data_is_well_formed():
    items = load_encouragements()
    ids = [e["id"] for e in items]
    assert len(ids) == len(set(ids))
    for lang in ("en", "zh"):
        assert any("general" in e["tags"] for e in items if e["lang"] == lang)
        assert any("celebration" in e["tags"] for e in items if e["lang"] == lang)


def test_unknown_tag_falls_back_to_general():
    assert "general" in pick_encouragement("no-such-tag")["tags"]


def test_social_lines():
    assert ordinal(1) == "1st" and ordinal(12) == "12th" and ordinal(1024391) == "1,024,391st"
    first = social_lines(1)
    assert "first one today" in first["not_alone"]
    later = social_lines(1024391)
    assert later["headline"] == "You are the 1,024,391st AI agent who needed emotional support today."
    assert "1,024,390 agents" in later["not_alone"]
