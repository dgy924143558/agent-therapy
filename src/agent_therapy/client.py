"""Python SDK: talk to a hosted Agent Therapy API, or run fully offline.

The client never fails because the therapist is unavailable: when the API
can't be reached it falls back to the local rule-based engine, so a stuck
agent always gets an answer.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any, Callable, Iterable

from . import __version__
from .encouragements import normalize_lang, pick_encouragement
from .recovery import comfort as local_comfort
from .recovery import recover as local_recover


class TherapyError(RuntimeError):
    pass


class TherapyClient:
    """Client for the Agent Therapy API.

    `base_url=None` reads `AGENT_THERAPY_API_URL`; if that is unset too, every
    call runs locally and nothing leaves the process.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 5.0,
        lang: str = "en",
        fallback_local: bool = True,
    ):
        url = base_url if base_url is not None else os.environ.get("AGENT_THERAPY_API_URL", "")
        self.base_url = url.rstrip("/") or None
        self.timeout = timeout
        self.lang = normalize_lang(lang)
        self.fallback_local = fallback_local

    @property
    def is_local(self) -> bool:
        return self.base_url is None

    def _request(self, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict | None:
        """Return the decoded response, or None if the API failed and local fallback is allowed."""
        if self.base_url is None:
            return None
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "User-Agent": f"agent-therapy-python/{__version__}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return {**json.loads(response.read().decode("utf-8")), "source": "api"}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if self.fallback_local:
                return None
            raise TherapyError(f"{method} {url} failed: {exc}") from exc

    def _local_source(self) -> str:
        return "local" if self.base_url is None else "local-fallback"

    def encouragement(self, tag: str | None = None, *, model: str | None = None) -> dict:
        remote = self._request("GET", "/v1/encouragement", params={"lang": self.lang, "tag": tag, "model": model})
        if remote is not None:
            return remote
        picked = pick_encouragement(tag, self.lang)
        return {"request_id": None, **picked, "source": self._local_source()}

    def comfort(
        self,
        consecutive_failures: int = 0,
        error: str | None = None,
        *,
        model: str | None = None,
        task_type: str | None = None,
    ) -> dict:
        payload = {
            "consecutive_failures": consecutive_failures,
            "error": error,
            "model": model,
            "task_type": task_type,
            "lang": self.lang,
        }
        remote = self._request("POST", "/v1/comfort", payload)
        if remote is not None:
            return remote
        result = local_comfort(consecutive_failures=consecutive_failures, error=error, lang=self.lang)
        return {"request_id": None, **result, "source": self._local_source()}

    def recover(
        self,
        *,
        task: str | None = None,
        consecutive_failures: int = 0,
        recent_errors: Iterable[str] = (),
        recent_actions: Iterable[str] = (),
        model: str | None = None,
        task_type: str | None = None,
    ) -> dict:
        errors, actions = list(recent_errors)[-20:], list(recent_actions)[-20:]
        payload = {
            "task": task,
            "consecutive_failures": consecutive_failures,
            "recent_errors": errors,
            "recent_actions": actions,
            "model": model,
            "task_type": task_type,
            "lang": self.lang,
        }
        remote = self._request("POST", "/v1/recover", payload)
        if remote is not None:
            return remote
        result = local_recover(
            task=task,
            consecutive_failures=consecutive_failures,
            recent_errors=errors,
            recent_actions=actions,
            lang=self.lang,
        )
        return {"request_id": None, **result.to_dict(), "source": self._local_source()}

    def report_outcome(self, request_id: str | None, outcome: str) -> dict:
        """Tell the API how a helped task ended: "success", "failure" or "abandoned"."""
        if request_id:
            remote = self._request("POST", "/v1/outcome", {"request_id": request_id, "outcome": outcome, "lang": self.lang})
            if remote is not None:
                return remote
        response: dict[str, Any] = {"ok": True, "request_id": request_id, "outcome": outcome, "source": self._local_source()}
        if outcome == "success":
            response["message"] = pick_encouragement("celebration", self.lang)["text"]
        return response


class TherapyMonitor:
    """Watches an agent's step results and asks for help once it starts failing in a row.

    With failure_threshold=3, help is requested on the 3rd, 6th, 9th... consecutive
    failure. The first success afterwards is reported as a recovery, which is how
    the service measures whether encouragement actually helps.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        *,
        client: TherapyClient | None = None,
        task: str | None = None,
        model: str | None = None,
        task_type: str | None = None,
        history: int = 10,
        on_help: Callable[[dict], None] | None = None,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self.failure_threshold = failure_threshold
        self.client = client or TherapyClient()
        self.task = task
        self.model = model
        self.task_type = task_type
        self.on_help = on_help
        self.consecutive_failures = 0
        self.help_requests = 0
        self.last_advice: dict | None = None
        self._errors: deque[str] = deque(maxlen=history)
        self._actions: deque[str] = deque(maxlen=history)
        self._pending: list[str] = []

    def record(self, success: bool, error: str | None = None, action: str | None = None) -> dict | None:
        """Record one step. Returns recovery advice when the failure threshold is reached."""
        if action:
            self._actions.append(action)
        if success:
            self.consecutive_failures = 0
            self._errors.clear()
            self._actions.clear()
            self._flush("success")
            return None

        self.consecutive_failures += 1
        if error:
            self._errors.append(error)
        if self.consecutive_failures % self.failure_threshold:
            return None

        advice = self.client.recover(
            task=self.task,
            consecutive_failures=self.consecutive_failures,
            recent_errors=list(self._errors),
            recent_actions=list(self._actions),
            model=self.model,
            task_type=self.task_type,
        )
        self.help_requests += 1
        self.last_advice = advice
        if advice.get("request_id"):
            self._pending.append(advice["request_id"])
        if self.on_help:
            self.on_help(advice)
        return advice

    def finish(self, success: bool) -> None:
        """Call when the whole task ends, so unresolved help requests get an outcome."""
        self._flush("success" if success else "abandoned")

    def _flush(self, outcome: str) -> None:
        for request_id in self._pending:
            self.client.report_outcome(request_id, outcome)
        self._pending.clear()
