"""
Read-only git visibility for the Git page — status + recent log. The actual
commit/push still goes through the existing `git` action (routers/actions.py
-> core/executors.run_git), same propose/RBAC/audit path as everything else;
this router only ever shells out to inspect state, never to change it.
"""
from __future__ import annotations

import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_permission
from core.config import settings

router = APIRouter(dependencies=[Depends(require_session)])


def _require_git_view(request: Request) -> None:
    try:
        require_permission(request.state.user, "git")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _run(args: list[str], timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            args, cwd=settings.repo_path, capture_output=True, text=True, timeout=timeout, check=False
        )
        return result.stdout if result.returncode == 0 else f"error: {result.stderr[:300]}"
    except Exception as exc:
        return f"error: {exc}"


@router.get("/git/status")
def git_status(request: Request) -> dict:
    _require_git_view(request)
    branch = _run(["git", "branch", "--show-current"]).strip()
    porcelain = _run(["git", "status", "--porcelain"])
    files = []
    for line in porcelain.splitlines():
        if not line.strip() or line.startswith("error:"):
            continue
        files.append({"status": line[:2].strip(), "path": line[3:]})
    ahead_behind = _run(["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"]).strip()
    return {"branch": branch, "files": files, "clean": len(files) == 0, "ahead_behind": ahead_behind}


@router.get("/git/log")
def git_log(request: Request, limit: int = 20) -> dict:
    _require_git_view(request)
    fmt = "%H%x1f%an%x1f%ad%x1f%s"
    out = _run(["git", "log", f"-{limit}", f"--pretty=format:{fmt}", "--date=relative"])
    commits = []
    if not out.startswith("error:"):
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append({"hash": parts[0][:7], "author": parts[1], "date": parts[2], "message": parts[3]})
    return {"commits": commits}
