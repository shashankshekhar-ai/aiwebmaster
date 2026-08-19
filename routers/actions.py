from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_permission
from core.aiwebmaster_agent import EXECUTABLE_TYPES
from core.executors import EXECUTORS, ExecutionError
from db.audit import log_event

router = APIRouter(dependencies=[Depends(require_session)])


@router.post("/actions/run")
def run_action(body: dict, request: Request) -> dict:
    action_id = body.get("id")
    action_type = body.get("type")
    payload = body.get("payload") or {}

    if not action_id or not action_type:
        raise HTTPException(status_code=400, detail="id and type are required")
    if action_type not in EXECUTABLE_TYPES:
        raise HTTPException(status_code=400, detail=f"'{action_type}' is draft-only and cannot be run")

    try:
        require_permission(request.state.user, action_type)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    executor = EXECUTORS.get(action_type)
    if executor is None:
        raise HTTPException(status_code=400, detail=f"No executor for type '{action_type}'")

    try:
        result = executor(payload)
        ok = bool(result.get("ok", True))
    except ExecutionError as exc:
        result = {"error": str(exc)}
        ok = False

    log_event(
        event="executed",
        actor=request.state.user["email"],
        action_type=action_type,
        action_id=action_id,
        payload=payload,
        result=result,
        ok=ok,
    )

    if not ok:
        raise HTTPException(status_code=422, detail=result)
    return result
