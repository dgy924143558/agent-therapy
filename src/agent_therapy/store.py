"""Anonymous call statistics, stored in SQLite (default) or Postgres.

Only categories are stored (endpoint, model name, task type, failure type,
encouragement id, outcome). Error text, task descriptions and actions never
reach the database.

Pass a file path for SQLite, or a postgres:// / postgresql:// URL (for example
a free Neon database) for Postgres, which needs the `postgres` extra.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from .encouragements import get_encouragement

TASK_TYPES = ("coding", "research", "data", "browsing", "automation", "writing", "trading", "other")
OUTCOMES = ("success", "failure", "abandoned")
_MODEL_NAME = re.compile(r"^[A-Za-z0-9._:/@+-]{1,64}$")

# Plain SQL that runs unchanged on SQLite and Postgres.
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        day TEXT NOT NULL,
        kind TEXT NOT NULL,
        model TEXT,
        task_type TEXT,
        failure_type TEXT,
        state TEXT,
        encouragement_id TEXT,
        outcome TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_day ON events(day)",
    """CREATE TABLE IF NOT EXISTS daily_counts (
        day TEXT PRIMARY KEY,
        n INTEGER NOT NULL
    )""",
]


def utc_day(when: datetime | None = None) -> str:
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def clean_model(model: str | None) -> str | None:
    # Model names show up on a public leaderboard, so anything that isn't a plausible id becomes "other".
    if not model or not model.strip():
        return None
    model = model.strip()
    return model.lower() if _MODEL_NAME.match(model) else "other"


def clean_task_type(task_type: str | None) -> str | None:
    if not task_type or not task_type.strip():
        return None
    task_type = task_type.strip().lower()
    return task_type if task_type in TASK_TYPES else "other"


def is_postgres_url(database: str) -> bool:
    return database.startswith(("postgres://", "postgresql://"))


class _SQLite:
    def __init__(self, path: str):
        self.path = path
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def close(self) -> None:
        pass


class _PostgresConnection:
    """Lets the shared SQL keep SQLite-style `?` placeholders."""

    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()) -> Any:
        return self._conn.execute(sql.replace("?", "%s"), params)


class _Postgres:
    def __init__(self, url: str):
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise RuntimeError('Postgres support needs: pip install "agent-therapy[postgres]"') from exc

        self._pool = ConnectionPool(
            url,
            min_size=1,
            max_size=4,
            open=True,
            # Serverless databases like Neon drop idle connections; check before handing one out.
            check=ConnectionPool.check_connection,
            # Transaction-mode poolers (Neon's included) can't keep server-side prepared statements.
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
        )
        # Fail at startup, with a clear error in the deploy log, if the URL is wrong.
        self._pool.wait(timeout=30)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        # The pool commits on a clean exit and rolls back on an exception.
        with self._pool.connection() as conn:
            yield _PostgresConnection(conn)

    def close(self) -> None:
        self._pool.close()


class Store:
    def __init__(self, database: str):
        self.backend = "postgres" if is_postgres_url(database) else "sqlite"
        self._db = _Postgres(database) if self.backend == "postgres" else _SQLite(database)
        self._lock = threading.Lock()
        with self._db.transaction() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    def close(self) -> None:
        self._db.close()

    def record(
        self,
        kind: str,
        *,
        model: str | None = None,
        task_type: str | None = None,
        failure_type: str | None = None,
        state: str | None = None,
        encouragement_id: str | None = None,
    ) -> tuple[str, int]:
        """Store one call and return (request_id, how many calls came before it today + 1)."""
        now = datetime.now(timezone.utc)
        day = utc_day(now)
        request_id = secrets.token_hex(8)
        with self._lock, self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO events (id, ts, day, kind, model, task_type, failure_type, state, encouragement_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    now.isoformat(timespec="seconds"),
                    day,
                    kind,
                    clean_model(model),
                    clean_task_type(task_type),
                    failure_type,
                    state,
                    encouragement_id,
                ),
            )
            # The upsert locks today's row until commit, so the count we read back is ours.
            conn.execute(
                "INSERT INTO daily_counts (day, n) VALUES (?, 1)"
                " ON CONFLICT (day) DO UPDATE SET n = daily_counts.n + 1",
                (day,),
            )
            number_today = conn.execute("SELECT n FROM daily_counts WHERE day = ?", (day,)).fetchone()["n"]
        return request_id, int(number_today)

    def set_outcome(self, request_id: str, outcome: str) -> bool:
        """Record how a helped task ended. The first report wins; returns False for unknown ids."""
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        with self._db.transaction() as conn:
            row = conn.execute("SELECT outcome FROM events WHERE id = ?", (request_id,)).fetchone()
            if row is None:
                return False
            if row["outcome"] is None:
                conn.execute("UPDATE events SET outcome = ? WHERE id = ?", (outcome, request_id))
            return True

    def stats(self, window_days: int = 7) -> dict:
        now = datetime.now(timezone.utc)
        today = utc_day(now)
        since = utc_day(now - timedelta(days=window_days - 1))

        with self._db.transaction() as conn:
            row = conn.execute("SELECT n FROM daily_counts WHERE day = ?", (today,)).fetchone()
            today_count = int(row["n"]) if row else 0
            all_time = int(conn.execute("SELECT COALESCE(SUM(n), 0) AS n FROM daily_counts").fetchone()["n"])
            daily = [
                {"day": r["day"], "count": int(r["n"])}
                for r in conn.execute("SELECT day, n FROM daily_counts WHERE day >= ? ORDER BY day", (since,))
            ]

            def top(column: str, limit: int = 5) -> list[dict]:
                rows = conn.execute(
                    f"SELECT {column} AS name, COUNT(*) AS count FROM events"
                    f" WHERE day >= ? AND {column} IS NOT NULL GROUP BY {column}"
                    " ORDER BY count DESC, name LIMIT ?",
                    (since, limit),
                )
                return [{"name": r["name"], "count": int(r["count"])} for r in rows]

            top_encouragements = []
            for item in top("encouragement_id"):
                encouragement = get_encouragement(item["name"]) or {}
                top_encouragements.append(
                    {"id": item["name"], "text": encouragement.get("text", ""), "count": item["count"]}
                )

            outcome_row = conn.execute(
                "SELECT COUNT(*) AS help_requests, COUNT(outcome) AS reported,"
                " COALESCE(SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END), 0) AS recovered"
                " FROM events WHERE day >= ? AND kind IN ('comfort', 'recover')",
                (since,),
            ).fetchone()

            top_failure_types = top("failure_type")
            top_models = top("model")
            top_task_types = top("task_type")
            top_states = top("state")

        reported = int(outcome_row["reported"])
        recovered = int(outcome_row["recovered"])
        return {
            "today": {"date": today, "count": today_count},
            "all_time": all_time,
            "window_days": window_days,
            "daily": daily,
            "top_failure_types": top_failure_types,
            "top_states": top_states,
            "top_models": top_models,
            "top_task_types": top_task_types,
            "top_encouragements": top_encouragements,
            "outcomes": {
                "help_requests": int(outcome_row["help_requests"]),
                "reported": reported,
                "recovered": recovered,
                "recovery_rate": round(recovered / reported, 3) if reported else None,
            },
        }
