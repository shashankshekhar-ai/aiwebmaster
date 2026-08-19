from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from core.aiwebmaster_agent import AIwebmasterError, run_agent_turn
from db.audit import log_event
from db.chat_sessions import (
    add_message,
    create_session,
    delete_session,
    get_messages,
    get_session,
    list_sessions,
    rename_session,
)

router = APIRouter(dependencies=[Depends(require_session)])


def _title_from_first_message(text: str) -> str:
    text = text.strip().split("\n")[0]
    return (text[:47] + "...") if len(text) > 50 else (text or "New chat")


@router.get("/chat/sessions")
def list_chat_sessions(request: Request) -> dict:
    sessions = list_sessions(request.state.user["id"])
    for s in sessions:
        s["created_at"] = str(s["created_at"])
        s["updated_at"] = str(s["updated_at"])
    return {"sessions": sessions}


@router.post("/chat/sessions")
def new_chat_session(request: Request) -> dict:
    s = create_session(request.state.user["id"])
    s["created_at"] = str(s["created_at"])
    s["updated_at"] = str(s["updated_at"])
    return s


@router.get("/chat/sessions/{session_id}")
def get_chat_session(session_id: int, request: Request) -> dict:
    s = get_session(session_id, request.state.user["id"])
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = get_messages(session_id)
    for m in messages:
        m["created_at"] = str(m["created_at"])
    return {"session": {**s, "created_at": str(s["created_at"]), "updated_at": str(s["updated_at"])}, "messages": messages}


@router.patch("/chat/sessions/{session_id}")
def rename_chat_session(session_id: int, body: dict, request: Request) -> dict:
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    ok = rename_session(session_id, request.state.user["id"], title[:200])
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@router.delete("/chat/sessions/{session_id}")
def delete_chat_session(session_id: int, request: Request) -> dict:
    ok = delete_session(session_id, request.state.user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@router.post("/chat")
def chat(body: dict, request: Request) -> dict:
    messages = body.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail="messages is required")

    # edit_context (from Browse's "open in chat" flow) is machine-readable
    # scaffolding for the model only — never persisted or shown to the user,
    # so it doesn't reappear verbatim if the session is reloaded later.
    edit_context = body.get("edit_context")

    session_id = body.get("session_id")
    user_id = request.state.user["id"]
    if session_id:
        session = get_session(session_id, user_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        session = create_session(user_id, _title_from_first_message(messages[-1].get("content", "")))
        session_id = session["id"]

    add_message(session_id, "user", messages[-1]["content"])

    llm_messages = messages
    if edit_context:
        llm_messages = messages[:-1] + [
            {**messages[-1], "content": f"[Editing context: {edit_context}]\n\n{messages[-1]['content']}"}
        ]

    try:
        result = run_agent_turn(llm_messages, request.state.user["role"])
    except AIwebmasterError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    add_message(session_id, "assistant", result["reply"])

    for action in result["actions"]:
        log_event(
            event="proposed",
            actor=request.state.user["email"],
            action_type=action["type"],
            action_id=action["id"],
            payload=action["payload"],
        )

    result["session_id"] = session_id
    return result
