"""
DB-backed AI provider/model/key config — lets a super_admin switch providers
from the Settings UI instead of editing env vars + redeploying. Single active
row; falls back to env vars (core/config.py) when no row exists, so existing
env-only setups keep working untouched.
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from core.config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS aiwebmaster_ai_settings (
    id SERIAL PRIMARY KEY,
    provider VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    api_key VARCHAR NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# claude_cli: routes through the claude-agent sandbox's persisted Claude
# Code CLI login (a Claude subscription) instead of a billed API key — see
# core/ai_provider.py::_call_claude_cli.
PROVIDERS = {"anthropic", "openai", "gemini", "claude_cli"}


def init_ai_settings_table() -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def get_active_ai_settings() -> dict[str, Any] | None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT provider, model, api_key FROM aiwebmaster_ai_settings ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def save_ai_settings(*, provider: str, model: str, api_key: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'; must be one of {sorted(PROVIDERS)}")
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM aiwebmaster_ai_settings")
            cur.execute(
                "INSERT INTO aiwebmaster_ai_settings (provider, model, api_key) VALUES (%s, %s, %s)",
                (provider, model, api_key),
            )
        conn.commit()
    finally:
        conn.close()
