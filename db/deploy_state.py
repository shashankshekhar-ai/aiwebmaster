"""
Tracks the repo state (git HEAD + working-tree diff hash) at the moment of
the last successful "publish", so the Deploy/Chat UI can tell whether a new
publish would actually change anything before letting the user run one.
Single-row table — one repo, one staging target (see D9 addenda: no true
3rd environment tier).
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from core.config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS aiwebmaster_deploy_state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    last_publish_git_sha VARCHAR,
    last_publish_tree_hash VARCHAR,
    last_publish_at TIMESTAMPTZ,
    CONSTRAINT single_row CHECK (id = 1)
);
"""


def init_deploy_state_table() -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def get_last_publish() -> dict[str, Any] | None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT last_publish_git_sha, last_publish_tree_hash, last_publish_at "
                "FROM aiwebmaster_deploy_state WHERE id = 1"
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def set_last_publish(git_sha: str, tree_hash: str) -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO aiwebmaster_deploy_state (id, last_publish_git_sha, last_publish_tree_hash, last_publish_at)
                   VALUES (1, %s, %s, now())
                   ON CONFLICT (id) DO UPDATE SET
                     last_publish_git_sha = EXCLUDED.last_publish_git_sha,
                     last_publish_tree_hash = EXCLUDED.last_publish_tree_hash,
                     last_publish_at = EXCLUDED.last_publish_at""",
                (git_sha, tree_hash),
            )
        conn.commit()
    finally:
        conn.close()
