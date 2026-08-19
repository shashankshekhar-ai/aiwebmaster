from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, WebSocket

from auth.models import get_user_by_id
from auth.sessions import COOKIE_NAME, SessionInvalid, read_session_token


def require_session(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    try:
        data = read_session_token(token)
        user = get_user_by_id(data["user_id"])
        if not user or user["session_epoch"] != data["epoch"]:
            raise SessionInvalid("revoked", "Your session was signed out remotely (e.g. a password change) — please log in again.")
    except SessionInvalid as exc:
        raise HTTPException(status_code=401, detail=exc.message) from exc
    request.state.user = user
    return user


async def require_session_ws(websocket: WebSocket) -> dict[str, Any] | None:
    """WebSocket equivalent of require_session — same cookie/token/epoch
    checks, but a WebSocket route can't raise HTTPException, so this closes
    the socket (with the reason in the close frame, which the client can
    read off the CloseEvent) and returns None on failure instead. Caller
    must check for None and return without further use of the socket."""
    token = websocket.cookies.get(COOKIE_NAME)
    try:
        data = read_session_token(token)
        user = get_user_by_id(data["user_id"])
        if not user or user["session_epoch"] != data["epoch"]:
            raise SessionInvalid("revoked", "Your session was signed out remotely (e.g. a password change) — please log in again.")
    except SessionInvalid as exc:
        # Close reason is capped at 123 UTF-8 bytes by the WebSocket spec —
        # every message above is well under that, but truncate defensively.
        await websocket.close(code=4401, reason=exc.message[:123])
        return None
    return user
