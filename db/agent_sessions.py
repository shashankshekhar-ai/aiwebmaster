"""
Persistent sessions for the interactive Agent Terminal page (/agent) — one
row per continuing claude-agent/codex-agent sandbox thread, plus a full
event log (prompts/output/diffs/errors) so a page reload or reconnect can
replay history instead of losing it. Same raw-SQL-per-call pattern as
db/chat_sessions.py — no ORM needed for two small tables.
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from core.config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS aiwebmaster_agent_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES aiwebmaster_users(id) ON DELETE CASCADE,
    tool VARCHAR,
    cli_session_id VARCHAR,
    title VARCHAR NOT NULL DEFAULT 'New agent session',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS aiwebmaster_agent_events (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES aiwebmaster_agent_sessions(id) ON DELETE CASCADE,
    type VARCHAR NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_events_session ON aiwebmaster_agent_events(session_id, id);
"""


def init_agent_tables() -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def list_sessions(user_id: int) -> list[dict[str, Any]]:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, tool, title, created_at, updated_at FROM aiwebmaster_agent_sessions "
                "WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def create_session(user_id: int, title: str = "New agent session") -> dict[str, Any]:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO aiwebmaster_agent_sessions (user_id, title) VALUES (%s, %s) "
                "RETURNING id, tool, title, created_at, updated_at",
                (user_id, title),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def get_session(session_id: int, user_id: int) -> dict[str, Any] | None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, tool, cli_session_id, title, created_at, updated_at "
                "FROM aiwebmaster_agent_sessions WHERE id = %s AND user_id = %s",
                (session_id, user_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def rename_session(session_id: int, user_id: int, title: str) -> bool:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE aiwebmaster_agent_sessions SET title = %s, updated_at = now() "
                "WHERE id = %s AND user_id = %s",
                (title, session_id, user_id),
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def delete_session(session_id: int, user_id: int) -> bool:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM aiwebmaster_agent_sessions WHERE id = %s AND user_id = %s",
                (session_id, user_id),
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def set_session_tool(session_id: int, tool: str) -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE aiwebmaster_agent_sessions SET tool = %s, updated_at = now() WHERE id = %s",
                (tool, session_id),
            )
        conn.commit()
    finally:
        conn.close()


def set_cli_session_id(session_id: int, cli_session_id: str) -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE aiwebmaster_agent_sessions SET cli_session_id = %s WHERE id = %s",
                (cli_session_id, session_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_events(session_id: int) -> list[dict[str, Any]]:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT type, content, created_at FROM aiwebmaster_agent_events "
                "WHERE session_id = %s ORDER BY id", (session_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_event(session_id: int, type_: str, content: str) -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO aiwebmaster_agent_events (session_id, type, content) VALUES (%s, %s, %s)",
                (session_id, type_, content),
            )
            cur.execute(
                "UPDATE aiwebmaster_agent_sessions SET updated_at = now() WHERE id = %s", (session_id,)
            )
        conn.commit()
    finally:
        conn.close()
