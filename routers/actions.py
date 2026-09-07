from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_docker_env, require_permission
from auth.rate_limit import codegen_limiter
from core.aiwebmaster_agent import EXECUTABLE_TYPES
from core.executors import EXECUTORS, ExecutionError
from db.audit import log_event
from db.memory import add_memory

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

    # user_management's set_enabled/reset_password ops act on another
    # account's login gate/credentials — never allow targeting your own id
    # here (locking yourself out, or resetting your own password blind and
    # losing the generated value, both self-inflicted and unrecoverable
    # without a second super_admin). Checked with the acting user's real id
    # from the session, not anything client-supplied, so this can't be
    # bypassed by a crafted request body.
    if action_type == "user_management" and payload.get("op") in ("set_enabled", "reset_password"):
        if payload.get("user_id") == request.state.user["id"]:
            raise HTTPException(status_code=400, detail="You can't change your own account status or password here.")

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

    # The executor already ran with the real payload above — this redacted
    # copy is only for what gets persisted to the audit trail. A raw
    # password (create/update account, reset password) has no business
    # sitting in plaintext in a table other admins/infra_admin (sql
    # permission) can query indefinitely; every other field is left as-is
    # since the audit trail's whole point is showing exactly what ran.
    audit_payload = dict(payload)
    if "password" in audit_payload:
        audit_payload["password"] = "***REDACTED***"

    log_event(
        event="executed",
        actor=request.state.user["email"],
        action_type=action_type,
        action_id=action_id,
        payload=audit_payload,
        result=result,
        ok=ok,
    )

    # Auto-write to the chat model's own operational memory (db/memory.py,
    # folded into every turn by core/context.py) on real failure only — a
    # successful action needs no reminder, but a failure is exactly the
    # kind of thing a human operator would remember without being told
    # twice ("that failed last time because X"). Never touches password
    # fields (same redacted copy used for the audit row above).
    if not ok:
        error_detail = str(result.get("error", "unknown error"))[:500]
        add_memory(
            action_type=action_type,
            summary=f"A '{action_type}' action failed: {error_detail[:200]}",
            detail=f"Full payload: {audit_payload}\nFull result: {result}"[:2000],
        )

    if not ok:
        raise HTTPException(status_code=422, detail=result)
    return result
