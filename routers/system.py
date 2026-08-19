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
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
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
