from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, WebSocket

from auth.models import get_user_by_id
from auth.sessions import COOKIE_NAME, read_session_token


def require_session(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    data = read_session_token(token) if token else None
    user = get_user_by_id(data["user_id"]) if data else None
    if not user or user["session_epoch"] != data["epoch"]:
        raise HTTPException(status_code=401, detail="Unauthorized")
    request.state.user = user
    return user


async def require_session_ws(websocket: WebSocket) -> dict[str, Any] | None:
    """WebSocket equivalent of require_session — same cookie/token/epoch
    checks, but a WebSocket route can't raise HTTPException, so this closes
    the socket and returns None on failure instead. Caller must check for
    None and return without further use of the socket."""
    token = websocket.cookies.get(COOKIE_NAME)
    data = read_session_token(token) if token else None
    user = get_user_by_id(data["user_id"]) if data else None
    if not user or user["session_epoch"] != data["epoch"]:
        await websocket.close(code=4401)
        return None
    return user
