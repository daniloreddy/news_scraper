"""MetricsDB: async SQLite for request and LLM token tracking."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

_DB_PATH = Path("data/metrics.db")
_lock = asyncio.Lock()


@dataclass
class RequestRecord:
    endpoint: str
    url: Optional[str]
    status: str  # "ok" | "error" | "timeout"
    duration: float
    error_msg: Optional[str] = None
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
        except Exception as e:
            logger.warning("MetricsDB record failed: %s", e)


async def get_stats(hours: int = 24) -> dict:
    since = time.time() - hours * 3600
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM requests WHERE ts >= ? ORDER BY ts DESC", (since,)
        ) as cur:
            rows = list(await cur.fetchall())

    total = len(rows)
    ok = sum(1 for r in rows if r["status"] == "ok")
    errors = sum(1 for r in rows if r["status"] in ("error", "timeout"))
    durations = [r["duration"] for r in rows if r["duration"] is not None]
    avg_dur = sum(durations) / len(durations) if durations else 0.0
    p_tok = sum(r["prompt_tokens"] or 0 for r in rows)
    c_tok = sum(r["completion_tokens"] or 0 for r in rows)

    return {
        "period_hours": hours,
        "total": total,
        "ok": ok,
        "errors": errors,
        "avg_duration_s": round(avg_dur, 2),
        "prompt_tokens": p_tok,
        "completion_tokens": c_tok,
    }


async def get_history(limit: int = 100) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM requests ORDER BY ts DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
