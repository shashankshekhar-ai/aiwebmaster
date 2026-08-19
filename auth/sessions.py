from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from core.config import settings

COOKIE_NAME = "aiwebmaster_session"
_MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="aiwebmaster-session")


def create_session_token(user_id: int, session_epoch: int) -> str:
    return _serializer().dumps({"user_id": user_id, "epoch": session_epoch})


def read_session_token(token: str) -> dict | None:
    """Returns {"user_id", "epoch"} or None. Callers must still compare
    "epoch" against the user's current session_epoch (auth/deps.py) —
    resetting a password bumps it, invalidating every existing cookie."""
    try:
        data = _serializer().loads(token, max_age=_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    if "user_id" not in data or "epoch" not in data:
        return None
    return data
