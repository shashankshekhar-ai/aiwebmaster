"""
aiwebmaster_users — one table, raw SQL DDL/queries (same pattern as
db/audit.py: no ORM needed for a table this small).
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from core.config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS aiwebmaster_users (
    id SERIAL PRIMARY KEY,
    email VARCHAR NOT NULL UNIQUE,
    password_hash VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    session_epoch INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE aiwebmaster_users ADD COLUMN IF NOT EXISTS session_epoch INTEGER NOT NULL DEFAULT 0;
"""

ROLES = {"docker_ops", "ui_editor", "infra_admin", "super_admin"}


def init_users_table() -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def count_users() -> int:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM aiwebmaster_users")
            return cur.fetchone()[0]
    finally:
        conn.close()


def create_user(*, email: str, password_hash: str, role: str) -> dict[str, Any]:
    """Create or update-by-email. Every password (re)set bumps session_epoch,
    which invalidates any existing session cookies for that user — this is
    the mechanism for revoking a compromised session: reset their password."""
    if role not in ROLES:
        raise ValueError(f"Unknown role '{role}'; must be one of {sorted(ROLES)}")
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO aiwebmaster_users (email, password_hash, role, session_epoch)
                   VALUES (%s, %s, %s, 0)
                   ON CONFLICT (email) DO UPDATE SET
                       password_hash = EXCLUDED.password_hash,
                       role = EXCLUDED.role,
                       session_epoch = aiwebmaster_users.session_epoch + 1
                   RETURNING id, email, role, created_at""",
                (email, password_hash, role),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict[str, Any] | None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, password_hash, role, session_epoch FROM aiwebmaster_users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def list_users() -> list[dict[str, Any]]:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, email, role, created_at FROM aiwebmaster_users ORDER BY created_at")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, role, session_epoch FROM aiwebmaster_users WHERE id = %s", (user_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()
