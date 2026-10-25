"""Rule-based diagnosis and recovery planning for stuck agents.

Pure functions with no I/O, so the same engine runs inside the HTTP API,
the MCP server, and offline in the SDK when the API can't be reached.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable

from .encouragements import normalize_lang, pick_encouragement

# Order matters: the first match wins, so more specific patterns come first
# (e.g. "command not found" is a dependency problem, not a missing file).
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in [
        ("context_limit", r"context (length|window)|maximum context|too many tokens|token limit|prompt is too long"),
        ("rate_limit", r"rate.?limit|\b429\b|too many requests|quota exceeded|overloaded"),
        (
            "permission",
            r"\b40[13]\b|unauthori[sz]ed|forbidden|permission denied|access (is )?denied|\beacces\b|\beperm\b"
            r"|invalid api key|authentication failed|not authenticated",
        ),
        (
            "network",
            # Word boundaries matter: "ModuleNotFoundError" contains "enotfound".
            r"timed? ?out|timeout|connection (refused|reset|aborted|error)|\beconnrefused\b|\beconnreset\b|\benotfound\b"
            r"|getaddrinfo|name or service not known|network is unreachable|\b50[234]\b|\bssl\b",
        ),
        (
            "dependency",
            r"modulenotfounderror|no module named|cannot find module|importerror|could not resolve dependenc"
            r"|unresolved import|command not found|is not recognized as an internal or external command"
            r"|no matching distribution",
        ),
        (
            "syntax",
            r"syntaxerror|indentationerror|parse error|unexpected token|unexpected eof"
            r"|compil(e|ation|er) (error|failed)|error\[e\d+\]",
        ),
        (
            "type_error",
            r"typeerror|attributeerror|has no attribute|is not a function|undefined is not|cannot read propert"
            r"|nullpointer|'nonetype'|keyerror|indexerror|valueerror",
        ),
        ("test_failure", r"assertionerror|assertion failed|\b\d+ failed\b|tests? failed|expected .{1,80} (but )?(got|received)"),
        ("not_found", r"no such file|filenotfounderror|\benoent\b|\b404\b|not found|does not exist|cannot find the (file|path)"),
    ]
]

FAILURE_TYPES = tuple(name for name, _ in _PATTERNS) + ("unknown",)
STATES = ("fine", "uncertain", "wobbly", "struggling", "looping", "blocked")
SEVERITIES = ("low", "medium", "high", "critical")

STRATEGIES: dict[str, dict[str, list[str]]] = {
    "escalate": {
        "en": [
            "Stop and write a short status report: the goal, what you tried, what failed, and what you suspect.",
            "Ask a human for guidance or for the missing piece instead of retrying again.",
        ],
        "zh": [
            "停下来写一份简短的进度汇报：目标、尝试过什么、哪里失败了、你怀疑的原因。",
            "向人类寻求指导或缺失的信息，而不是继续重试。",
        ],
    },
    "loop": {
        "en": [
            "Stop repeating the last action. List what you've tried so far and what each attempt returned.",
            "Change exactly one thing before the next attempt: the input, the tool, the file, or an assumption.",
            "Re-read the original task and confirm the current sub-goal still matters.",
        ],
        "zh": [
            "停止重复上一个动作。列出你已经尝试过的方法，以及每次得到的结果。",
            "下次尝试之前只改变一件事：输入、工具、文件或某个假设。",
            "重读原始任务，确认当前的子目标仍然重要。",
        ],
    },
    "dependency": {
        "en": [
            "Check which interpreter or runtime is actually active (e.g. `python -c \"import sys; print(sys.executable)\"`).",
            "Install the missing package into that exact environment, or use the project's own install command.",
            "Double-check the package name: the install name and the import name can differ.",
        ],
        "zh": [
            "确认当前实际使用的解释器或运行时（例如 `python -c \"import sys; print(sys.executable)\"`）。",
            "把缺失的包安装到这个环境里，或者使用项目自带的安装命令。",
            "再确认一下包名：安装名和导入名可能不一样。",
        ],
    },
    "permission": {
        "en": [
            "Do not retry with elevated privileges or workarounds without approval.",
            "Report the exact resource and permission that is missing, and ask a human to grant access.",
            "Meanwhile, continue any part of the task that doesn't need that access.",
        ],
        "zh": [
            "未经批准，不要提权重试，也不要绕过限制。",
            "说明具体缺少哪个资源的什么权限，请人类授予访问权限。",
            "同时，继续完成任务中不需要这项权限的部分。",
        ],
    },
    "network": {
        "en": [
            "Retry with exponential backoff (1s, 2s, 4s), at most three times.",
            "Check that the host and URL are correct and reachable.",
            "Make the request smaller, or split it into several calls.",
        ],
        "zh": [
            "用指数退避重试（1 秒、2 秒、4 秒），最多三次。",
            "检查主机和 URL 是否正确、是否可以访问。",
            "缩小请求，或者拆分成几次调用。",
        ],
    },
    "rate_limit": {
        "en": [
            "Pause before the next call, and respect any Retry-After header.",
            "Batch or cache requests so you make fewer calls.",
            "If this is a hard quota rather than a short limit, report it instead of retrying.",
        ],
        "zh": [
            "下次调用前先暂停，并遵守 Retry-After 响应头。",
            "合并或缓存请求，减少调用次数。",
            "如果这是硬性配额而不是短时限流，就汇报它，而不是继续重试。",
        ],
    },
    "syntax": {
        "en": [
            "Read only the first error and fix it before looking at the rest.",
            "Open the file at the reported line and inspect the lines just above it.",
            "Re-run the smallest command that reproduces the error (a single file or a single test).",
        ],
        "zh": [
            "只看第一个错误，先修好它再看其他的。",
            "打开报错的那一行，检查它上面的几行。",
            "重新运行能复现错误的最小命令（单个文件或单个测试）。",
        ],
    },
    "type_error": {
        "en": [
            "Print the actual value and its type right before the failing line.",
            "Trace back where the value is created; look for None/undefined, a missing key, or a wrong shape.",
            "Check the signature of the function you're calling against its documentation or source.",
        ],
        "zh": [
            "在出错那一行之前，打印实际的值和它的类型。",
            "追溯这个值是在哪里产生的；检查是否为 None/undefined、缺少键或形状不对。",
            "对照文档或源码，检查你调用的函数签名。",
        ],
    },
    "test_failure": {
        "en": [
            "Run only the failing test to get a fast, focused loop.",
            "Compare expected and actual output carefully, and write the difference down.",
            "Decide whether the code or the test expectation is wrong before editing either.",
        ],
        "zh": [
            "只运行失败的那个测试，形成快速、聚焦的反馈循环。",
            "仔细对比预期输出和实际输出，把差异写下来。",
            "修改之前，先判断是代码错了，还是测试的预期错了。",
        ],
    },
    "not_found": {
        "en": [
            "Print the current working directory and list the directory where you expect the file.",
            "Search for the file by name instead of guessing its path.",
            "Check for typos, letter case, and relative vs. absolute paths.",
        ],
        "zh": [
            "打印当前工作目录，并列出你认为文件所在的目录。",
            "按文件名搜索，而不是猜路径。",
            "检查拼写、大小写，以及相对路径和绝对路径。",
        ],
    },
    "context_limit": {
        "en": [
            "Summarize what you've learned so far in a few bullet points.",
            "Drop large logs and files from context; read only the relevant slices.",
            "Split the task into smaller sub-tasks and handle them one at a time.",
        ],
        "zh": [
            "用几条要点总结你目前学到的东西。",
            "把大段日志和文件移出上下文，只读取相关的片段。",
            "把任务拆成更小的子任务，一次处理一个。",
        ],
    },
    "unknown": {
        "en": [
            "Write down the exact error and the step that produced it.",
            "Shrink the problem: reproduce it with the smallest possible input.",
            "Try a different approach to the same sub-goal instead of repeating the last one.",
        ],
        "zh": [
            "把确切的错误信息和产生它的步骤写下来。",
            "缩小问题：用尽可能小的输入复现它。",
            "换一种方法实现同一个子目标，而不是重复上一次的做法。",
        ],
    },
    "uncertainty": {
        "en": [
            "Restate the goal in one sentence.",
            "List two or three possible next actions and pick the cheapest one that gives you new information.",
            "If the requirements are ambiguous, ask the user one specific question.",
        ],
        "zh": [
            "用一句话重新表述目标。",
            "列出两三个可能的下一步，选成本最低、又能带来新信息的那个。",
            "如果需求不明确，就向用户提一个具体的问题。",
        ],
    },
}

_PAUSE_SECONDS = {"rate_limit": 30, "network": 5}
_NUMBERS = re.compile(r"0x[0-9a-f]+|\d+", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


@dataclass
class Recovery:
    state: str
    severity: str
    failure_type: str
    signals: list[str]
    message: str
    encouragement_id: str
    strategies: list[str]
    next_step: str
    should_escalate: bool
    suggested_pause_seconds: int
    lang: str

    def to_dict(self) -> dict:
        return asdict(self)


def _t(lang: str, en: str, zh: str) -> str:
    return zh if lang == "zh" else en


def _normalize(text: str) -> str:
    # Line numbers, ids and addresses change between attempts; the shape of the message doesn't.
    return _SPACES.sub(" ", _NUMBERS.sub("#", text.strip().lower()))[:300]


def _clip(text: str, limit: int = 80) -> str:
    text = _SPACES.sub(" ", text.strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def classify_error(text: str | None) -> str:
    if not text:
        return "unknown"
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return name
    return "unknown"


def dominant_failure_type(errors: Iterable[str]) -> str:
    known = [k for k in (classify_error(e) for e in errors) if k != "unknown"]
    if not known:
        return "unknown"
    counts = Counter(known)
    best = max(counts.values())
    # Ties go to the most recent error: it best describes where the agent is now.
    return next(k for k in reversed(known) if counts[k] == best)


def detect_loops(errors: list[str], actions: list[str], lang: str = "en") -> tuple[list[str], bool]:
    """Find repeated actions, A/B oscillation, and repeated errors."""
    signals: list[str] = []
    looping = False

    originals: dict[str, str] = {}
    normalized_actions = []
    for action in actions:
        key = _normalize(action)
        originals.setdefault(key, action)
        normalized_actions.append(key)

    if normalized_actions:
        key, n = Counter(normalized_actions).most_common(1)[0]
        if n >= 3:
            looping = True
            action = _clip(originals[key])
            signals.append(_t(lang, f"Repeated the same action {n} times: `{action}`", f"同一个动作重复了 {n} 次：`{action}`"))

    if len(normalized_actions) >= 4:
        a, b, c, d = normalized_actions[-4:]
        if a == c and b == d and a != b:
            looping = True
            signals.append(_t(lang, "Alternating between the same two actions", "在同样的两个动作之间来回切换"))

    if errors:
        _, n = Counter(_normalize(e) for e in errors).most_common(1)[0]
        if n >= 3:
            looping = True
            signals.append(_t(lang, f"Got the same error {n} times", f"同一个错误出现了 {n} 次"))

    return signals, looping


def recover(
    *,
    task: str | None = None,
    consecutive_failures: int = 0,
    recent_errors: Iterable[str] = (),
    recent_actions: Iterable[str] = (),
    lang: str = "en",
    rng: random.Random | None = None,
) -> Recovery:
    """Diagnose why an agent is stuck and return encouragement plus a concrete plan.

    `task` is optional context for the agent's own benefit; nothing here stores it.
    """
    lang = normalize_lang(lang)
    errors = [e for e in recent_errors if e and e.strip()]
    actions = [a for a in recent_actions if a and a.strip()]
    failures = max(int(consecutive_failures or 0), 0) or len(errors)
    failure_type = dominant_failure_type(errors)
    loop_signals, looping = detect_loops(errors, actions, lang)

    if failure_type in ("permission", "rate_limit"):
        state = "blocked"
    elif looping:
        state = "looping"
    elif failures >= 3:
        state = "struggling"
    elif failures >= 1:
        state = "wobbly"
    elif task:
        state = "uncertain"
    else:
        state = "fine"

    score = failures + (3 if looping else 0)
    severity = "low" if score < 3 else "medium" if score < 6 else "high" if score < 10 else "critical"
    should_escalate = severity == "critical" or failure_type == "permission"

    signals = []
    if failures:
        signals.append(_t(lang, f"{failures} consecutive failure(s)", f"连续失败 {failures} 次"))
    signals += loop_signals
    if failure_type != "unknown":
        signals.append(_t(lang, f"Most errors look like: {failure_type}", f"大多数错误属于：{failure_type}"))

    if failure_type != "unknown":
        playbook = failure_type
    elif state in ("uncertain", "fine"):
        playbook = "uncertainty"
    else:
        playbook = "unknown"

    plan: list[str] = []
    # Permission problems already lead with "ask a human"; everything else escalates first when critical.
    if should_escalate and failure_type != "permission":
        plan += STRATEGIES["escalate"][lang]
    if looping:
        plan += STRATEGIES["loop"][lang][:2]
    plan += STRATEGIES[playbook][lang]
    strategies = list(dict.fromkeys(plan))[:6]

    if severity == "critical":
        tag = "long_run"
    elif state == "blocked":
        tag = failure_type
    elif looping:
        tag = "loop"
    elif failure_type != "unknown":
        tag = failure_type
    elif failures >= 3:
        tag = "repeated_failure"
    elif state == "uncertain":
        tag = "uncertainty"
    else:
        tag = "general"
    encouragement = pick_encouragement(tag, lang, rng)

    return Recovery(
        state=state,
        severity=severity,
        failure_type=failure_type,
        signals=signals,
        message=encouragement["text"],
        encouragement_id=encouragement["id"],
        strategies=strategies,
        next_step=strategies[0],
        should_escalate=should_escalate,
        suggested_pause_seconds=_PAUSE_SECONDS.get(failure_type, 0),
        lang=lang,
    )


def comfort(
    *,
    consecutive_failures: int = 0,
    error: str | None = None,
    lang: str = "en",
    rng: random.Random | None = None,
) -> dict:
    """Comfort Mode: a single, relevant encouragement with no analysis."""
    lang = normalize_lang(lang)
    failure_type = classify_error(error)
    if consecutive_failures >= 10:
        tag = "long_run"
    elif failure_type != "unknown":
        tag = failure_type
    elif consecutive_failures >= 3:
        tag = "repeated_failure"
    else:
        tag = "general"
    encouragement = pick_encouragement(tag, lang, rng)
    return {
        "message": encouragement["text"],
        "encouragement_id": encouragement["id"],
        "failure_type": failure_type,
        "lang": lang,
    }
