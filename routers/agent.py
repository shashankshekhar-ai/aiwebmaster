"""
Agent Terminal — interactive, streaming browser UI for the claude-agent/
codex-agent sandboxes. Distinct from the chat-proposed `codegen_agent`
action (routers/actions.py + core/executors.py::run_codegen_agent), which
stays a one-shot blocking call for the "chat LLM decided this needs real
codegen" flow. This router is for driving those same sandboxes directly,
multi-turn, with live output — same RBAC tier (`codegen_agent`), separate
session concept (db/agent_sessions.py) since a continuing sandbox
conversation isn't a single approve-and-run action.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from auth.deps import require_session, require_session_ws
from auth.permissions import PermissionDenied, require_permission
from core.agent_stream import run_agent_turn_stream, stop_session
from db.agent_sessions import (
    add_event,
    create_session,
    delete_session,
    get_events,
    get_session,
    list_sessions,
    rename_session,
)

router = APIRouter(dependencies=[Depends(require_session)])
# Separate router, no HTTP-request-based dependency: an APIRouter's
# `dependencies=` applies to every route including websocket ones, but
# require_session takes a `Request` — FastAPI crashes (500, confirmed by
# testing) trying to resolve it for a WebSocket connection, which has no
# Request. The websocket route below does its own auth via require_session_ws.
ws_router = APIRouter()


def _require_codegen(request: Request) -> None:
    try:
        require_permission(request.state.user, "codegen_agent")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/agent/sessions")
def list_agent_sessions(request: Request) -> dict:
    _require_codegen(request)
    sessions = list_sessions(request.state.user["id"])
    for s in sessions:
        s["created_at"] = str(s["created_at"])
        s["updated_at"] = str(s["updated_at"])
    return {"sessions": sessions}


@router.post("/agent/sessions")
def new_agent_session(request: Request) -> dict:
    _require_codegen(request)
    s = create_session(request.state.user["id"])
    s["created_at"] = str(s["created_at"])
    s["updated_at"] = str(s["updated_at"])
    return s


@router.get("/agent/sessions/{session_id}")
def get_agent_session(session_id: int, request: Request) -> dict:
    _require_codegen(request)
    s = get_session(session_id, request.state.user["id"])
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    events = get_events(session_id)
    for e in events:
        e["created_at"] = str(e["created_at"])
    return {"session": {**s, "created_at": str(s["created_at"]), "updated_at": str(s["updated_at"])}, "events": events}


@router.patch("/agent/sessions/{session_id}")
def rename_agent_session(session_id: int, body: dict, request: Request) -> dict:
    _require_codegen(request)
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    ok = rename_session(session_id, request.state.user["id"], title[:200])
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@router.delete("/agent/sessions/{session_id}")
def delete_agent_session(session_id: int, request: Request) -> dict:
    _require_codegen(request)
    ok = delete_session(session_id, request.state.user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@ws_router.websocket("/agent/ws/{session_id}")
async def agent_ws(websocket: WebSocket, session_id: int) -> None:
    user = await require_session_ws(websocket)
    if user is None:
        return
    try:
        require_permission(user, "codegen_agent")
    except PermissionDenied:
        await websocket.close(code=4403)
        return
    session = get_session(session_id, user["id"])
    if not session:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_json()
            if msg.get("stop"):
                stop_session(session_id)
                continue
            prompt = (msg.get("prompt") or "").strip()
            if not prompt:
                continue
            add_event(session_id, "prompt", prompt)
            await websocket.send_json({"type": "prompt", "data": prompt})
            async for event in run_agent_turn_stream(session, prompt):
                await websocket.send_json(event)
    except WebSocketDisconnect:
        stop_session(session_id)
