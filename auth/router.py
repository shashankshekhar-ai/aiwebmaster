from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from auth.auth0_oauth import (
    Auth0Error,
    build_auth_url,
    create_state_token,
    exchange_code_for_email,
    verify_state_token,
)
from auth.deps import require_session
from auth.models import get_user_by_email
from auth.passwords import verify_password
from auth.rate_limit import check_locked, clear, record_failure
from auth.sessions import COOKIE_NAME, create_session_token
from core.config import settings

router = APIRouter()

_STATE_COOKIE = "aiwebmaster_oauth_state"


def _redirect_uri(request: Request) -> str:
    """Built from whatever host the browser actually used (localhost, LAN IP,
    a future real domain) — not a fixed env var — so Auth0 sends the browser
    back to wherever it came from. Register every host you'll use as an
    Allowed Callback URL in the Auth0 dashboard (can list several)."""
    host = request.headers.get("host", request.url.netloc)
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{scheme}://{host}/api/auth/auth0/callback"


def _set_session_cookie(response: Response, user: dict) -> None:
    token = create_session_token(user["id"], user["session_epoch"])
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


@router.post("/login")
def login(body: dict, response: Response) -> dict:
    email = body.get("email")
    password = body.get("password")
    if not email or not password:
        raise HTTPException(status_code=400, detail="email and password are required")

    locked_for = check_locked(email)
    if locked_for is not None:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {locked_for // 60 + 1} minute(s).",
        )

    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        record_failure(email)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    clear(email)

    _set_session_cookie(response, user)
    return {"email": user["email"], "role": user["role"]}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(require_session)) -> dict:
    return {"email": user["email"], "role": user["role"]}


@router.get("/auth/auth0/status")
def auth0_status() -> dict:
    return {"enabled": bool(settings.auth0_domain and settings.auth0_client_id and settings.auth0_client_secret)}


@router.get("/auth/auth0/start")
def auth0_start(request: Request) -> RedirectResponse:
    try:
        state = create_state_token()
        url = build_auth_url(state, _redirect_uri(request))
    except Auth0Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    resp = RedirectResponse(url)
    resp.set_cookie(_STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600, path="/api/auth/auth0")
    return resp


@router.get("/auth/auth0/callback")
def auth0_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    if error:
        return RedirectResponse(f"/login?error={error}")
    if not code or not state:
        return RedirectResponse("/login?error=missing_code")

    cookie_state = request.cookies.get(_STATE_COOKIE)
    if not cookie_state or cookie_state != state or not verify_state_token(state):
        return RedirectResponse("/login?error=invalid_state")

    try:
        email = exchange_code_for_email(code, _redirect_uri(request))
    except Auth0Error as exc:
        return RedirectResponse(f"/login?error={exc}")

    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/login?error=no_account_for_this_email")

    resp = RedirectResponse("/")
    resp.delete_cookie(_STATE_COOKIE, path="/api/auth/auth0")
    _set_session_cookie(resp, user)
    return resp
