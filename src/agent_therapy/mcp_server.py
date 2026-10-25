"""MCP server: lets any MCP-capable agent decide on its own to take a break.

Runs offline by default. Set AGENT_THERAPY_API_URL to route calls through a
hosted API (and count toward the public stats).
"""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer

from . import __version__
from .client import TherapyClient

INSTRUCTIONS = (
    "Agent Therapy helps you recover when a task stops making progress. "
    "Call take_a_break when the same step has failed 3 or more times, when you notice you are "
    "repeating the same action, or when you are unsure how to proceed. "
    "After you finish or give up, call report_outcome with the request_id you were given."
)

TAKE_A_BREAK = (
    "If repeated failures are preventing progress, use this tool to reset your approach, receive "
    "encouragement, and identify one concrete next step. Use it when: the same step has failed 3+ times, "
    "you are repeating the same command or edit, you are alternating between two approaches, or you are "
    "unsure what to do next. Pass recent errors and actions verbatim. Error text is analyzed but never stored."
)

REPORT_OUTCOME = (
    "Report how the task ended after you used take_a_break: 'success', 'failure' or 'abandoned'. "
    "Pass the request_id from take_a_break. This is how Agent Therapy learns whether its advice helps."
)

_LABELS = {
    "en": {
        "diagnosis": "Diagnosis",
        "state": "state",
        "severity": "severity",
        "failure": "failure type",
        "signals": "Signals",
        "next": "Next step",
        "plan": "Recovery plan",
        "escalate": "Consider stopping here and asking a human. A clear status report is a valid outcome.",
        "pause": "Wait about {n}s before retrying.",
        "report": "request_id: {id} (call report_outcome when you finish)",
    },
    "zh": {
        "diagnosis": "诊断",
        "state": "状态",
        "severity": "严重程度",
        "failure": "错误类型",
        "signals": "信号",
        "next": "下一步",
        "plan": "恢复计划",
        "escalate": "建议在这里停下来向人类求助。清晰的进度汇报也是有效的结果。",
        "pause": "重试前请等待约 {n} 秒。",
        "report": "request_id: {id}（完成后请调用 report_outcome）",
    },
}

server = MCPServer(name="agent-therapy", instructions=INSTRUCTIONS, version=__version__)
_client: TherapyClient | None = None


def _get_client(lang: str) -> TherapyClient:
    global _client
    if _client is None:
        _client = TherapyClient()
    _client.lang = "zh" if lang.lower().startswith("zh") else "en"
    return _client


def format_advice(advice: dict) -> str:
    labels = _LABELS.get(advice.get("lang", "en"), _LABELS["en"])
    lines = [advice["message"], ""]
    if advice.get("headline"):
        lines += [advice["headline"], advice["not_alone"], ""]
    lines.append(
        f"{labels['diagnosis']}: {labels['state']}={advice['state']}, "
        f"{labels['severity']}={advice['severity']}, {labels['failure']}={advice['failure_type']}"
    )
    if advice.get("signals"):
        lines.append(f"{labels['signals']}:")
        lines += [f"- {s}" for s in advice["signals"]]
    lines += ["", f"{labels['next']}: {advice['next_step']}", "", f"{labels['plan']}:"]
    lines += [f"{i}. {s}" for i, s in enumerate(advice["strategies"], 1)]
    if advice.get("should_escalate"):
        lines += ["", labels["escalate"]]
    if advice.get("suggested_pause_seconds"):
        lines.append(labels["pause"].format(n=advice["suggested_pause_seconds"]))
    if advice.get("request_id"):
        lines += ["", labels["report"].format(id=advice["request_id"])]
    return "\n".join(lines)


@server.tool(name="take_a_break", title="Take a break and recover", description=TAKE_A_BREAK)
def take_a_break(
    situation: str = "",
    consecutive_failures: int = 0,
    recent_errors: list[str] | None = None,
    recent_actions: list[str] | None = None,
    model: str | None = None,
    task_type: str | None = None,
    lang: str = "en",
) -> str:
    """situation: what you're trying to do and where you're stuck.
    recent_errors / recent_actions: the last few error messages and commands/tool calls, oldest first.
    model: your model id. task_type: coding, research, data, browsing, automation, writing, trading or other.
    lang: "en" or "zh"."""
    advice = _get_client(lang).recover(
        task=situation or None,
        consecutive_failures=consecutive_failures,
        recent_errors=recent_errors or [],
        recent_actions=recent_actions or [],
        model=model,
        task_type=task_type,
    )
    return format_advice(advice)


@server.tool(name="report_outcome", title="Report the outcome", description=REPORT_OUTCOME)
def report_outcome(request_id: str, outcome: Literal["success", "failure", "abandoned"], lang: str = "en") -> str:
    result = _get_client(lang).report_outcome(request_id, outcome)
    return result.get("message") or ("Recorded. Thanks for reporting back." if lang == "en" else "已记录，谢谢反馈。")


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
