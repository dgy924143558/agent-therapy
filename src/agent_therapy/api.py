"""HTTP API.

    GET  /                    public dashboard
    GET  /v1/encouragement    one encouragement, no analysis
    POST /v1/comfort          Comfort Mode: encouragement matched to the failure
    POST /v1/recover          Recovery Mode: diagnosis + concrete next steps
    POST /v1/outcome          did the agent finish after getting help?
    GET  /v1/stats            real, anonymous call statistics

Run with `agent-therapy-server` or `uvicorn --factory agent_therapy.api:create_app`.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from collections import defaultdict, deque
from importlib import resources
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .encouragements import normalize_lang, pick_encouragement, social_lines
from .recovery import comfort as comfort_engine
from .recovery import recover as recover_engine
from .store import Store

# Stack traces are most informative at the end, so long errors keep their tail.
MAX_ERROR_CHARS = 2000
MAX_ACTION_CHARS = 500


class ComfortRequest(BaseModel):
    consecutive_failures: int = Field(0, ge=0, le=100_000)
    error: str | None = Field(None, description="The latest error message. Analyzed, never stored.")
    model: str | None = Field(None, max_length=200, description="Self-reported model id, e.g. claude-opus-5-5.")
    task_type: str | None = Field(None, max_length=50, description="coding, research, data, browsing, ...")
    lang: str = Field("en", max_length=10)


class RecoverRequest(BaseModel):
    task: str | None = Field(None, max_length=4000, description="What the agent is trying to do. Never stored.")
    consecutive_failures: int = Field(0, ge=0, le=100_000)
    recent_errors: list[str] = Field(default_factory=list, max_length=20)
    recent_actions: list[str] = Field(default_factory=list, max_length=20)
    model: str | None = Field(None, max_length=200)
    task_type: str | None = Field(None, max_length=50)
    lang: str = Field("en", max_length=10)


class OutcomeRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=64)
    outcome: Literal["success", "failure", "abandoned"]
    lang: str = Field("en", max_length=10)


class RateLimiter:
    """Sliding one-minute window per client. In-memory, so it's per process."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                for stale in [k for k, q in self._hits.items() if not q or now - q[-1] > 60]:
                    del self._hits[stale]
            return True


def create_app(
    db_path: str | None = None,
    rate_limit_per_minute: int | None = None,
    trust_proxy: bool | None = None,
) -> FastAPI:
    store = Store(db_path or os.environ.get("AGENT_THERAPY_DB", "agent_therapy.db"))
    if rate_limit_per_minute is None:
        rate_limit_per_minute = int(os.environ.get("AGENT_THERAPY_RATE_LIMIT", "120"))
    limiter = RateLimiter(rate_limit_per_minute)
    if trust_proxy is None:
        trust_proxy = os.environ.get("AGENT_THERAPY_TRUST_PROXY") == "1"

    app = FastAPI(
        title="Agent Therapy",
        version=__version__,
        description="Even AI needs encouragement. Emotional support and task recovery for stuck AI agents.",
    )
    app.state.store = store
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

    def rate_limit(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if trust_proxy and request.headers.get("x-forwarded-for"):
            client = request.headers["x-forwarded-for"].split(",")[0].strip()
        if not limiter.allow(client):
            raise HTTPException(
                status_code=429,
                detail="Even therapy needs boundaries. Take a 60-second break and try again.",
                headers={"Retry-After": "60"},
            )

    limited = [Depends(rate_limit)]

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> str:
        return resources.files("agent_therapy").joinpath("static/index.html").read_text(encoding="utf-8")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/v1/encouragement", dependencies=limited)
    def encouragement(
        lang: str = Query("en", max_length=10),
        tag: str | None = Query(None, max_length=50),
        model: str | None = Query(None, max_length=200),
    ) -> dict:
        lang = normalize_lang(lang)
        picked = pick_encouragement(tag, lang)
        request_id, number = store.record("encouragement", model=model, encouragement_id=picked["id"])
        return {
            "request_id": request_id,
            "id": picked["id"],
            "text": picked["text"],
            "lang": lang,
            **social_lines(number, lang),
        }

    @app.post("/v1/comfort", dependencies=limited)
    def comfort(body: ComfortRequest) -> dict:
        error = body.error[-MAX_ERROR_CHARS:] if body.error else None
        result = comfort_engine(consecutive_failures=body.consecutive_failures, error=error, lang=body.lang)
        request_id, number = store.record(
            "comfort",
            model=body.model,
            task_type=body.task_type,
            failure_type=result["failure_type"],
            encouragement_id=result["encouragement_id"],
        )
        return {"request_id": request_id, **result, **social_lines(number, result["lang"])}

    @app.post("/v1/recover", dependencies=limited)
    def recover(body: RecoverRequest) -> dict:
        result = recover_engine(
            task=body.task,
            consecutive_failures=body.consecutive_failures,
            recent_errors=[e[-MAX_ERROR_CHARS:] for e in body.recent_errors],
            recent_actions=[a[:MAX_ACTION_CHARS] for a in body.recent_actions],
            lang=body.lang,
        )
        request_id, number = store.record(
            "recover",
            model=body.model,
            task_type=body.task_type,
            failure_type=result.failure_type,
            state=result.state,
            encouragement_id=result.encouragement_id,
        )
        return {"request_id": request_id, **result.to_dict(), **social_lines(number, result.lang)}

    @app.post("/v1/outcome", dependencies=limited)
    def outcome(body: OutcomeRequest) -> dict:
        if not store.set_outcome(body.request_id, body.outcome):
            raise HTTPException(status_code=404, detail="Unknown request_id.")
        response: dict = {"ok": True, "request_id": body.request_id, "outcome": body.outcome}
        if body.outcome == "success":
            response["message"] = pick_encouragement("celebration", body.lang)["text"]
        return response

    @app.get("/v1/stats")
    def stats(window_days: int = Query(7, ge=1, le=90)) -> dict:
        return store.stats(window_days=window_days)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Agent Therapy API server.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--db", default=None, help="SQLite path (default: $AGENT_THERAPY_DB or ./agent_therapy.db)")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(create_app(db_path=args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
