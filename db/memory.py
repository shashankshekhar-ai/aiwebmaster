"""
Auto-written operational memory — distinct from AGENT_CONTEXT.md (static,
hand-edited) and aiwebmaster_audit (complete raw log of every action, not
meant to be read turn-by-turn). This table exists so the chat model
actually *learns from working*: every time a real action fails, a short
plain-English entry is appended here automatically (routers/actions.py),
and the most recent entries are folded into every chat turn's context
(core/context.py::build_site_context), the same way a human operator
would remember "oh, that failed last time because X" without needing to
be told again.

Best-effort by design, same as db/audit.py's audit logging: a failure to
write or read memory must never block the actual action or the chat
turn it's attached to.
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg2
import psycopg2.extras

from core.config import settings

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS aiwebmaster_memory (
    id SERIAL PRIMARY KEY,
    action_type VARCHAR NOT NULL,
    summary VARCHAR NOT NULL,
    detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def init_memory_table() -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def add_memory(*, action_type: str, summary: str, detail: str | None = None) -> None:
    """Best-effort — a broken memory write must never break the action it's
    describing, so any DB error here is logged and swallowed, not raised."""
    try:
        conn = psycopg2.connect(settings.api_database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO aiwebmaster_memory (action_type, summary, detail) VALUES (%s, %s, %s)",
                    (action_type, summary, detail),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("failed to write aiwebmaster_memory entry for action_type=%s", action_type, exc_info=True)


def recent_memories(limit: int = 8) -> list[dict[str, Any]]:
    try:
        conn = psycopg2.connect(settings.api_database_url)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT action_type, summary, detail, created_at FROM aiwebmaster_memory ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        logger.warning("failed to read aiwebmaster_memory", exc_info=True)
        return []
