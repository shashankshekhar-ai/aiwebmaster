"""
Read-only docker/system visibility for the System page. No exec here — this
only ever shells out to inspect state (`docker ps`, `docker stats --no-stream`,
`df`), never to change anything.
"""
from __future__ import annotations

import json
import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_permission
from core.config import settings

router = APIRouter(dependencies=[Depends(require_session)])

# Friendly name + what it actually is, keyed by exact container name where
# stable, or a prefix for dynamically-named ones (codegen_agent sandboxes
# get a per-run hash suffix). Unmatched containers still show — just with
# their raw name, not the app pretending everything is catalogued.
CONTAINER_META_EXACT = {
    "tbz-cms-1": {"label": "CMS", "tier": "dev", "description": "Payload CMS — site content."},
    "tbz-api-1": {"label": "API", "tier": "dev", "description": "FastAPI backend — leads, AI scoring, integrations."},
    "tbz-web-1": {"label": "Web", "tier": "dev", "description": "Public site frontend (Next.js)."},
    "tbz-cms-prod-1": {"label": "CMS", "tier": "staging", "description": "Payload CMS, staging tier — updated via Publish."},
    "tbz-api-prod-1": {"label": "API", "tier": "staging", "description": "FastAPI backend, staging tier — updated via Publish."},
    "tbz-web-prod-1": {"label": "Web", "tier": "staging", "description": "Public site frontend, staging tier — updated via Publish."},
    "tbz-aiwebmaster-1": {"label": "AIwebmaster", "tier": "infra", "description": "This app — ops + content agent for the site."},
    "tbz-postgres-1": {"label": "Postgres", "tier": "infra", "description": "Shared database server — tbg_api, tbg_cms, and their staging copies."},
    "hermes": {"label": "Hermes Gateway", "tier": "infra", "description": "Hermes agent — messaging platforms + cron scheduler."},
    "hermes-dashboard": {"label": "Hermes Dashboard", "tier": "infra", "description": "Web UI for the Hermes agent."},
}
CONTAINER_META_PREFIX = {
    "tbz-claude-agent-run-": {"label": "Claude Code sandbox", "tier": "sandbox", "description": "Ephemeral codegen_agent run — isolated dev-only container, cleaned up after use."},
    "tbz-codex-agent-run-": {"label": "Codex sandbox", "tier": "sandbox", "description": "Ephemeral codegen_agent run — isolated dev-only container, cleaned up after use."},
}


def _container_meta(name: str) -> dict:
    if name in CONTAINER_META_EXACT:
        return CONTAINER_META_EXACT[name]
    for prefix, meta in CONTAINER_META_PREFIX.items():
        if name.startswith(prefix):
            return meta
    return {"label": name, "tier": "other", "description": ""}


def _require_docker_view(request: Request) -> None:
    try:
        require_permission(request.state.user, "docker")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _run(args: list[str], timeout: int = 15) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout if result.returncode == 0 else f"error: {result.stderr[:300]}"
    except Exception as exc:  # subprocess/timeout — surface as text, never 500 the whole page
        return f"error: {exc}"


@router.get("/system/containers")
def containers(request: Request) -> dict:
    _require_docker_view(request)
    out = _run(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=15)
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("error:"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        row["Meta"] = _container_meta(row.get("Names", ""))
        rows.append(row)
    return {"containers": rows, "raw_error": out if out.startswith("error:") else None}


@router.get("/system/stats")
def stats(request: Request) -> dict:
    _require_docker_view(request)
    docker_stats_raw = _run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=20
    )
    stats_rows = []
    for line in docker_stats_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("error:"):
            continue
        try:
            stats_rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    disk = _run(["df", "-h", settings.repo_path], timeout=10)
    loadavg = ""
    try:
        with open("/proc/loadavg") as f:
            loadavg = f.read().strip()
    except OSError:
        pass

    return {"container_stats": stats_rows, "disk": disk, "loadavg": loadavg}
