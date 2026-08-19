"""
In-memory login rate limiter — per-email lockout after repeated failures.
In-memory is fine at this scale (single-operator tool, single uvicorn
process); a restart clears it, which is an acceptable trade-off here.
"""
from __future__ import annotations

import time

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 15 * 60

_failures: dict[str, list[float]] = {}


def check_locked(email: str) -> int | None:
    """Returns seconds remaining if locked out, else None."""
    now = time.time()
    attempts = [t for t in _failures.get(email, []) if now - t < _WINDOW_SECONDS]
    _failures[email] = attempts
    if len(attempts) >= _MAX_ATTEMPTS:
        return int(_WINDOW_SECONDS - (now - attempts[0]))
    return None


def record_failure(email: str) -> None:
    _failures.setdefault(email, []).append(time.time())


def clear(email: str) -> None:
    _failures.pop(email, None)
