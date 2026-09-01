"""
Docker/system visibility for the System page, plus one safe mutation: cache
pruning. Everything else here only ever shells out to inspect state
(`docker ps`, `docker stats --no-stream`, `df`), never to change anything.
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


@router.get("/system/disk-breakdown")
def disk_breakdown(request: Request) -> dict:
    """`docker system df` — shown before the prune button so the user (not
    just us) can see what's actually reclaimable before clicking anything."""
    _require_docker_view(request)
    out = _run(["docker", "system", "df"], timeout=15)
    return {"raw": out}


@router.post("/system/prune")
def prune(request: Request) -> dict:
    """Reclaims disk space the SAFE way — deliberately just `docker system
    prune -f`, never `-a` and never `--volumes`:
      - stopped containers: none of ours are ever meant to be stopped, so
        this only ever catches genuine leftovers (e.g. a crashed one-off).
      - dangling (untagged) images: layers with no tag pointing at them —
        can't be what's running, can't be a rollback target (rollback tags
        images by git SHA — see core/executors._image_ref — so a tagged
        rollback image is never "dangling" and is never touched here).
      - unused networks.
      - build cache: safe to drop entirely, it's a pure speed optimization
        that just gets rebuilt (slower) on the next build.
    Never touches: running containers, ANY tagged image (including old
    rollback-tagged builds), or volumes (so postgres data is untouched no
    matter what). Same permission bar as viewing system stats — docker_ops/
    infra_admin/super_admin, not ui_editor.
    """
    _require_docker_view(request)
    before = _run(["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}"], timeout=15)
    out = _run(["docker", "system", "prune", "-f"], timeout=120)
    after = _run(["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}"], timeout=15)
    if out.startswith("error:"):
        raise HTTPException(status_code=500, detail=out)
    reclaimed = None
    for line in out.splitlines():
        if line.lower().startswith("total reclaimed space"):
            reclaimed = line.split(":", 1)[-1].strip()
    return {"ok": True, "reclaimed": reclaimed, "raw": out, "before": before, "after": after}
