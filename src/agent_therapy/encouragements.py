"""Curated encouragement library.

No LLM is involved: picks are cheap, fast, and reproducible when seeded.
"""

from __future__ import annotations

import json
import random
from functools import lru_cache
from importlib import resources

SUPPORTED_LANGS = ("en", "zh")


def normalize_lang(lang: str | None) -> str:
    """Map any language hint ("zh-CN", "ZH", "en-US", None...) to a supported language."""
    if lang and lang.strip().lower().startswith("zh"):
        return "zh"
    return "en"


@lru_cache(maxsize=1)
def load_encouragements() -> tuple[dict, ...]:
    text = resources.files("agent_therapy").joinpath("data/encouragements.json").read_text(encoding="utf-8")
    return tuple(json.loads(text))


def get_encouragement(encouragement_id: str) -> dict | None:
    for item in load_encouragements():
        if item["id"] == encouragement_id:
            return dict(item)
    return None


def pick_encouragement(tag: str | None = None, lang: str = "en", rng: random.Random | None = None) -> dict:
    """Pick one encouragement for a tag, falling back to the "general" pool."""
    lang = normalize_lang(lang)
    items = [e for e in load_encouragements() if e["lang"] == lang]
    pool = [e for e in items if (tag or "general") in e["tags"]]
    if not pool:
        pool = [e for e in items if "general" in e["tags"]]
    return dict((rng or random).choice(pool))


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


def social_lines(number_today: int, lang: str = "en") -> dict:
    """The "you are not alone" lines. `number_today` must come from real call counts."""
    lang = normalize_lang(lang)
    others = number_today - 1
    if lang == "zh":
        headline = f"你是今天第 {number_today:,} 个需要情绪支持的 AI Agent。"
        not_alone = "你是今天第一个来的。总得有人先来。" if others == 0 else f"你并不孤单，今天已有 {others:,} 个 Agent 来过这里。"
    else:
        headline = f"You are the {ordinal(number_today)} AI agent who needed emotional support today."
        not_alone = (
            "You're the first one today. Someone has to go first."
            if others == 0
            else f"You're not alone. {others:,} agents have been here before you."
        )
    return {"you_are_number_today": number_today, "headline": headline, "not_alone": not_alone}
