"""
Persistent per-user chat sessions — so conversation history survives a page
reload/browser restart, and a user can keep multiple named threads instead
of one ephemeral client-side array. Raw SQL, same pattern as auth/models.py
and db/audit.py — no ORM needed for two small tables.
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from core.config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS aiwebmaster_chat_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES aiwebmaster_users(id) ON DELETE CASCADE,
    title VARCHAR NOT NULL DEFAULT 'New chat',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS aiwebmaster_chat_messages (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES aiwebmaster_chat_sessions(id) ON DELETE CASCADE,
    role VARCHAR NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON aiwebmaster_chat_messages(session_id, id);
"""


def init_chat_tables() -> None:
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
                "SELECT id, title, created_at, updated_at FROM aiwebmaster_chat_sessions "
                "WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def create_session(user_id: int, title: str = "New chat") -> dict[str, Any]:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO aiwebmaster_chat_sessions (user_id, title) VALUES (%s, %s) "
                "RETURNING id, title, created_at, updated_at",
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
                "SELECT id, title, created_at, updated_at FROM aiwebmaster_chat_sessions "
                "WHERE id = %s AND user_id = %s",
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
                "UPDATE aiwebmaster_chat_sessions SET title = %s, updated_at = now() "
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
                "DELETE FROM aiwebmaster_chat_sessions WHERE id = %s AND user_id = %s",
                (session_id, user_id),
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def get_messages(session_id: int) -> list[dict[str, Any]]:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT role, content, created_at FROM aiwebmaster_chat_messages "
                "WHERE session_id = %s ORDER BY id", (session_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_message(session_id: int, role: str, content: str) -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO aiwebmaster_chat_messages (session_id, role, content) VALUES (%s, %s, %s)",
                (session_id, role, content),
            )
            cur.execute(
                "UPDATE aiwebmaster_chat_sessions SET updated_at = now() WHERE id = %s", (session_id,)
            )
        conn.commit()
    finally:
        conn.close()
