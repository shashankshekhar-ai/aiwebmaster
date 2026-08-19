from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.models import ROLES, list_users
from auth.permissions import PermissionDenied, require_permission

router = APIRouter(dependencies=[Depends(require_session)])


@router.get("/users")
def get_users(request: Request) -> dict:
    try:
        require_permission(request.state.user, "user_management")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    users = list_users()
    for u in users:
        u["created_at"] = str(u["created_at"])
    return {"users": users, "roles": sorted(ROLES)}
