"""MetricsDB: async SQLite for request and LLM token tracking."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_DB_PATH = Path("data/metrics.db")
_lock = asyncio.Lock()


@dataclass
class RequestRecord:
    endpoint: str
    url: str | None
    status: str  # "ok" | "error" | "timeout"
    duration: float
    error_msg: str | None = None
    prompt_tokens: int = field(default=0)
    completion_tokens: int = field(default=0)


async def init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                REAL    NOT NULL,
                endpoint          TEXT    NOT NULL,
                url               TEXT,
                status            TEXT    NOT NULL,
                duration          REAL,
                error_msg         TEXT,
                prompt_tokens     INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0
            )
        """)
        await db.commit()
    logger.info("MetricsDB ready at %s", _DB_PATH)


async def record(rec: RequestRecord) -> None:
    async with _lock:
        try:
            async with aiosqlite.connect(_DB_PATH) as db:
                await db.execute(
                    """INSERT INTO requests
                       (ts, endpoint, url, status, duration, error_msg,
                        prompt_tokens, completion_tokens)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        time.time(),
                        rec.endpoint,
                        rec.url,
                        rec.status,
                        rec.duration,
                        rec.error_msg,
                        rec.prompt_tokens,
                        rec.completion_tokens,
                    ),
                )
                await db.commit()
        except aiosqlite.Error as e:
            logger.warning("MetricsDB record failed: %s", e)


async def get_stats(hours: int = 24) -> dict[str, Any]:
    since = time.time() - hours * 3600
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute(
            """SELECT
                   COUNT(*),
                   SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status IN ('error', 'timeout') THEN 1 ELSE 0 END),
                   AVG(duration),
                   SUM(prompt_tokens),
                   SUM(completion_tokens)
               FROM requests WHERE ts >= ?""",
            (since,),
        ) as cur:
            row = await cur.fetchone()

    total, ok, errors, avg_dur, p_tok, c_tok = row  # type: ignore[misc]

    return {
        "period_hours": hours,
        "total": total or 0,
        "ok": ok or 0,
        "errors": errors or 0,
        "avg_duration_s": round(avg_dur, 2) if avg_dur is not None else 0.0,
        "prompt_tokens": p_tok or 0,
        "completion_tokens": c_tok or 0,
    }


async def purge_old(days: int) -> None:
    """Delete request records older than `days`, so metrics.db doesn't grow forever
    on a long-running instance. Call periodically from a background task."""
    cutoff = time.time() - days * 86400
    async with _lock:
        try:
            async with aiosqlite.connect(_DB_PATH) as db:
                await db.execute("DELETE FROM requests WHERE ts < ?", (cutoff,))
                await db.commit()
        except aiosqlite.Error as e:
            logger.warning("MetricsDB purge failed: %s", e)


async def get_history(limit: int = 100) -> list[dict[str, Any]]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM requests ORDER BY ts DESC LIMIT ?", (limit,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
