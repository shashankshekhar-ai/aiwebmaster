"""
Read-only file access — lets chat pull a file's current content into context
before proposing a code_edit (the agent has no multi-step tool loop, so this
is a plain endpoint the UI/user can hit directly, not something the LLM calls
mid-turn). Gated to the same roles as code_edit.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_permission
from core.config import settings

router = APIRouter(dependencies=[Depends(require_session)])

_MAX_BYTES = 200_000


@router.get("/files/read")
def read_file(path: str, request: Request) -> dict:
    try:
        require_permission(request.state.user, "code_edit")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    repo_root = Path(settings.repo_path).resolve()
    candidate = (repo_root / path).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"'{path}' resolves outside the repo") from None
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"'{path}' not found")

    size = candidate.stat().st_size
    if size > _MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"'{path}' is {size} bytes, over the {_MAX_BYTES}-byte limit")

    try:
        content = candidate.read_text()
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail=f"'{path}' is not a text file") from None

    return {"path": path, "content": content}
