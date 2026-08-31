from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_docker_env, require_permission
from auth.rate_limit import codegen_limiter
from core.aiwebmaster_agent import EXECUTABLE_TYPES
from core.executors import EXECUTORS, ExecutionError
from db.audit import log_event

router = APIRouter(dependencies=[Depends(require_session)])


@router.post("/actions/run")
def run_action(body: dict, request: Request) -> dict:
    action_id = body.get("id")
    action_type = body.get("type")
    payload = dict(body.get("payload") or {})

    # Only meaningful to run_codegen_agent (see core/executors.py) — lets a
    # chat-proposed codegen_agent action resume the same Claude Code
    # conversation across multiple proposals in one chat thread instead of
    # starting fresh every time. Harmless no-op for every other action type
    # (they don't read these keys).
    chat_session_id = body.get("session_id")
    if action_type == "codegen_agent" and chat_session_id:
        payload["_chat_session_id"] = chat_session_id
        payload["_user_id"] = request.state.user["id"]
    if action_type == "docker":
        payload["_actor"] = request.state.user["email"]

    if not action_id or not action_type:
        raise HTTPException(status_code=400, detail="id and type are required")
    if action_type not in EXECUTABLE_TYPES:
        raise HTTPException(status_code=400, detail=f"'{action_type}' is draft-only and cannot be run")

    try:
        require_permission(request.state.user, action_type)
        if action_type == "docker":
            require_docker_env(request.state.user, payload)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if action_type == "codegen_agent":
        retry_after = codegen_limiter.check(request.state.user["id"])
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail=f"Too many sandbox runs — try again in {retry_after}s. This limit protects your Claude subscription quota from runaway use, not normal use.",
                headers={"Retry-After": str(retry_after)},
            )

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
