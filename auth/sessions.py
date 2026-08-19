from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from core.config import settings

COOKIE_NAME = "aiwebmaster_session"
_MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="aiwebmaster-session")


def create_session_token(user_id: int, session_epoch: int) -> str:
    return _serializer().dumps({"user_id": user_id, "epoch": session_epoch})


class SessionInvalid(Exception):
    """Raised by read_session_token with a reason a caller can turn into a
    real message instead of a bare 401 — "expired" (7-day max age hit),
    "invalid" (corrupt/tampered/forged cookie) and "missing" (no cookie at
    all) are all genuinely different situations for the person on the other
    end, even though they all end the same way (log in again)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


def read_session_token(token: str | None) -> dict:
    """Returns {"user_id", "epoch"}. Raises SessionInvalid otherwise.
    Callers must still compare "epoch" against the user's current
    session_epoch (auth/deps.py) — resetting a password bumps it,
    invalidating every existing cookie (SessionInvalid("revoked", ...),
    raised by the caller, not here — this function has no DB access)."""
    if not token:
        raise SessionInvalid("missing", "You're not logged in.")
    try:
        data = _serializer().loads(token, max_age=_MAX_AGE_SECONDS)
    except SignatureExpired as exc:
        raise SessionInvalid("expired", "Your session expired — please log in again.") from exc
    except BadSignature as exc:
        raise SessionInvalid("invalid", "Your session is invalid — please log in again.") from exc
    if "user_id" not in data or "epoch" not in data:
        raise SessionInvalid("invalid", "Your session is invalid — please log in again.")
    return data
