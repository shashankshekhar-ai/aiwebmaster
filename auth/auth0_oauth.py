"""
Auth0 OAuth — an alternate login credential for an already-provisioned
aiwebmaster_users row. Not self-service signup: an unrecognized email is
rejected even with a fully valid Auth0 login. Standard authorization-code
flow against Auth0's own endpoints; email comes from Auth0's /userinfo
(no local JWT verification needed — the access token is opaque to us and
Auth0 validates it server-side when we call /userinfo with it).
"""
from __future__ import annotations

from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from core.config import settings

_STATE_MAX_AGE = 600  # 10 minutes to complete the round trip


class Auth0Error(Exception):
    pass


def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="aiwebmaster-auth0-state")


def create_state_token() -> str:
    return _state_serializer().dumps({"nonce": "aiwebmaster"})


def verify_state_token(token: str) -> bool:
    try:
        _state_serializer().loads(token, max_age=_STATE_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def build_auth_url(state: str, redirect_uri: str) -> str:
    if not settings.auth0_domain or not settings.auth0_client_id:
        raise Auth0Error("AUTH0_DOMAIN / AUTH0_CLIENT_ID are not configured")
    params = {
        "client_id": settings.auth0_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    return f"https://{settings.auth0_domain}/authorize?{urlencode(params)}"


def exchange_code_for_email(code: str, redirect_uri: str) -> str:
    """Returns the verified email for this Auth0 login, or raises Auth0Error.
    redirect_uri must be byte-identical to the one used in build_auth_url —
    Auth0 rejects the token exchange otherwise."""
    if not settings.auth0_domain or not settings.auth0_client_id or not settings.auth0_client_secret:
        raise Auth0Error("Auth0 is not configured")

    try:
        token_resp = httpx.post(
            f"https://{settings.auth0_domain}/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": settings.auth0_client_id,
                "client_secret": settings.auth0_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=15,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        userinfo_resp = httpx.get(
            f"https://{settings.auth0_domain}/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        userinfo_resp.raise_for_status()
        info = userinfo_resp.json()
    except httpx.HTTPError as exc:
        raise Auth0Error(f"Auth0 exchange failed: {exc}") from exc

    if info.get("email_verified") is False:
        raise Auth0Error("Auth0 account email is not verified")
    email = info.get("email")
    if not email:
        raise Auth0Error("Auth0 userinfo did not include an email")
    return email
