"""
One row per successful `docker` rebuild — which git commit built which
image tag, when, for which service+env. Lets the Deploy UI offer "roll back
to a previous build" without needing the console: retag that image as
current and recreate, no rebuild needed. Complements (doesn't replace)
db/deploy_state.py, which only tracks the single most recent publish.
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from core.config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS aiwebmaster_deployments (
    id SERIAL PRIMARY KEY,
    service VARCHAR NOT NULL,
    env VARCHAR NOT NULL,
    git_sha VARCHAR NOT NULL,
    image_tag VARCHAR NOT NULL,
    actor VARCHAR,
    kind VARCHAR NOT NULL DEFAULT 'build',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS aiwebmaster_deployments_service_env_idx
    ON aiwebmaster_deployments (service, env, created_at DESC);
"""


def init_deployments_table() -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def record_deployment(*, service: str, env: str, git_sha: str, image_tag: str, actor: str | None, kind: str = "build") -> None:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO aiwebmaster_deployments (service, env, git_sha, image_tag, actor, kind)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (service, env, git_sha, image_tag, actor, kind),
            )
        conn.commit()
    finally:
        conn.close()


def list_deployments(*, service: str, env: str, limit: int = 15) -> list[dict[str, Any]]:
    conn = psycopg2.connect(settings.api_database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, service, env, git_sha, image_tag, actor, kind, created_at
                   FROM aiwebmaster_deployments
                   WHERE service = %s AND env = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (service, env, limit),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
