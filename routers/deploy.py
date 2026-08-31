"""
Deploy control panel backend — service status for dev + staging (reuses the
same docker inspection as routers/system.py) plus a recent-activity feed
(reuses db/audit.py) so changes made anywhere — chat, browse, this page —
are reviewable in one place. All mutating actions still go through the
existing `docker` action / RBAC / audit pipeline (routers/actions.py); this
router is read-only.
"""
from __future__ import annotations

import json
import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_permission
from core.config import settings
from core.executors import _backups_sorted
from core.repo_state import db_content_hash, git_state
from db.audit import list_recent
from db.deploy_state import get_last_publish
from db.deployments import list_deployments

router = APIRouter(dependencies=[Depends(require_session)])

DEV_SERVICES = ["cms", "web", "api"]
STAGING_SERVICES = ["cms-prod", "web-prod", "api-prod"]


def _require_docker_view(request: Request) -> None:
    try:
        require_permission(request.state.user, "docker")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _run(args: list[str], timeout: int = 15) -> str:
    try:
        result = subprocess.run(args, cwd=settings.repo_path, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


@router.get("/deploy/status")
def deploy_status(request: Request) -> dict:
    _require_docker_view(request)
    out = _run(["docker", "ps", "-a", "--format", "{{json .}}"])
    by_name: dict[str, dict] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            by_name[row.get("Names", "")] = row
        except json.JSONDecodeError:
            continue

    def project_row(service: str) -> dict:
        row = by_name.get(f"{settings.compose_project}-{service}-1", {})
        return {
            "service": service,
            "status": row.get("Status", "not created"),
            "state": row.get("State", "absent"),
        }

    return {
        "dev": [project_row(s) for s in DEV_SERVICES],
        "staging": [project_row(s) for s in STAGING_SERVICES],
    }


@router.get("/deploy/diff")
def deploy_diff(request: Request) -> dict:
    """Whether a "publish" would actually change anything, and whether a
    "rollback" has anything to restore — so the chat/deploy UI can gray out
    dead-end buttons instead of letting the human run a no-op deploy."""
    _require_docker_view(request)

    last = get_last_publish()
    code_changed = True
    if last and last.get("last_publish_git_sha"):
        sha, tree_hash = git_state()
        code_changed = sha != last["last_publish_git_sha"] or tree_hash != last["last_publish_tree_hash"]

    dev_hash = db_content_hash(settings.cms_database_url)
    staging_hash = db_content_hash(settings.cms_database_url_prod)
    # If either dump failed, don't block on an unknown — assume changed
    # rather than silently disabling Publish.
    content_changed = dev_hash is None or staging_hash is None or dev_hash != staging_hash

    return {
        "code_changed": code_changed,
        "content_changed": content_changed,
        "has_changes": code_changed or content_changed,
        "has_backup": bool(_backups_sorted()),
        "last_publish_at": str(last["last_publish_at"]) if last and last.get("last_publish_at") else None,
    }


@router.get("/deploy/deployments")
def deploy_deployments(request: Request, service: str, env: str) -> dict:
    """Build history for one service — powers the "roll back to a previous
    build" picker. Each row is a real image tag still on disk (until
    pruned beyond _KEEP_IMAGE_TAGS — core/executors.py), so any of these
    can actually be redeployed, not just referenced."""
    _require_docker_view(request)
    if service not in {*DEV_SERVICES, *STAGING_SERVICES}:
        raise HTTPException(status_code=400, detail=f"Unknown service '{service}'")
    rows = list_deployments(service=service, env=env, limit=15)
    for r in rows:
        r["created_at"] = str(r["created_at"])
    return {"deployments": rows}


@router.get("/deploy/activity")
def deploy_activity(request: Request, limit: int = 30) -> dict:
    _require_docker_view(request)
    rows = list_recent(limit)
    for r in rows:
        r["created_at"] = str(r["created_at"])
    return {"activity": rows}
