"""
Audit log — every proposed action and every executed action gets a row here,
since this service can push to git, redeploy containers, and run raw SQL.
No ORM/migrations needed for one append-only table; plain psycopg2 + a
startup DDL is enough.

Also dual-written to a local JSONL file (core/config.py::audit_log_path, a
mounted volume — see docker-compose.yml) opened in append-only mode each
write: a super_admin with `sql` access could edit/delete the DB rows, but
can't retroactively rewrite lines already flushed to this file from a
different session/process. Not a substitute for real WORM storage, but a
meaningful floor above "just a normal table."
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

from core.config import settings

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS aiwebmaster_audit (
    id SERIAL PRIMARY KEY,
    event VARCHAR NOT NULL,        -- 'proposed' | 'executed'
    actor VARCHAR,                 -- user email, null for pre-auth v1 rows
    action_type VARCHAR NOT NULL,
    action_id VARCHAR NOT NULL,
    payload JSONB,
    result JSONB,
    ok BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE aiwebmaster_audit ADD COLUMN IF NOT EXISTS actor VARCHAR;
"""


def init_audit_table() -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def log_event(
    *,
    event: str,
    action_type: str,
    action_id: str,
    actor: str | None = None,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    ok: bool | None = None,
) -> None:
    now = datetime.now(timezone.utc)

    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO aiwebmaster_audit (event, actor, action_type, action_id, payload, result, ok, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    event,
                    actor,
                    action_type,
                    action_id,
                    json.dumps(payload) if payload is not None else None,
                    json.dumps(result) if result is not None else None,
                    ok,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    try:
        line = json.dumps(
            {
                "event": event,
                "actor": actor,
                "action_type": action_type,
                "action_id": action_id,
                "payload": payload,
                "result": result,
                "ok": ok,
                "created_at": now.isoformat(),
            }
        )
        path = Path(settings.audit_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(line + "\n")
    except OSError:
        logger.exception("failed to append to audit log file (DB row still written)")


def list_recent(limit: int = 50) -> list[dict[str, Any]]:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, event, actor, action_type, action_id, payload, result, ok, created_at "
                "FROM aiwebmaster_audit ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
