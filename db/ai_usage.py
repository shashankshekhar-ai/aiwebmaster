"""
Per-call token usage log for the chat AI provider (Anthropic/OpenAI/Gemini).
core/ai_provider.py inserts one row after every successful structured_call;
routers/settings.py aggregates rows for the Settings page's usage cards.
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from core.config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS aiwebmaster_ai_usage (
    id SERIAL PRIMARY KEY,
    provider VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_created ON aiwebmaster_ai_usage(created_at);
"""


def init_ai_usage_table() -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def log_usage(*, provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO aiwebmaster_ai_usage (provider, model, input_tokens, output_tokens) "
                "VALUES (%s, %s, %s, %s)",
                (provider, model, input_tokens, output_tokens),
            )
        conn.commit()
    finally:
        conn.close()


def usage_summary() -> dict[str, Any]:
    """Aggregate call count + tokens, split into today vs. all-time, grouped by provider."""
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT provider,
                       count(*) AS calls,
                       coalesce(sum(input_tokens), 0) AS input_tokens,
                       coalesce(sum(output_tokens), 0) AS output_tokens
                FROM aiwebmaster_ai_usage
                GROUP BY provider
                """
            )
            all_time = {row["provider"]: dict(row) for row in cur.fetchall()}

            cur.execute(
                """
                SELECT provider,
                       count(*) AS calls,
                       coalesce(sum(input_tokens), 0) AS input_tokens,
                       coalesce(sum(output_tokens), 0) AS output_tokens
                FROM aiwebmaster_ai_usage
                WHERE created_at >= date_trunc('day', now())
                GROUP BY provider
                """
            )
            today = {row["provider"]: dict(row) for row in cur.fetchall()}
        return {"all_time": all_time, "today": today}
    finally:
        conn.close()
