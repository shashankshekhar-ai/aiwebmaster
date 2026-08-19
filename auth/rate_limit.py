"""
In-memory rate limiting — login lockout plus per-user caps on the two
paths that spend real money/quota per call (chat -> Gemini/Anthropic
tokens, codegen_agent/Agent Terminal -> the operator's own logged-in
Claude subscription usage). In-memory is fine at this scale
(single-operator tool, single uvicorn process); a restart clears it,
which is an acceptable trade-off here.
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


class SlidingWindowLimiter:
    """Per-key cap: at most `max_calls` within `window_seconds`, per key
    (a user id). Nothing here is billed per-user internally — this exists
    purely to bound worst-case cost/quota exposure from a single account,
    compromised or otherwise, since neither the Gemini/Anthropic bill nor
    the operator's Claude subscription quota care which app user triggered
    the call."""

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls: dict[int, list[float]] = {}

    def check(self, key: int) -> int | None:
        """Returns seconds remaining if the caller is over the cap, else
        None (and records this call as one of the calls in the window)."""
        now = time.time()
        calls = [t for t in self._calls.get(key, []) if now - t < self.window_seconds]
        if len(calls) >= self.max_calls:
            self._calls[key] = calls
            return int(self.window_seconds - (now - calls[0]))
        calls.append(now)
        self._calls[key] = calls
        return None


# Chat -> one Gemini/Anthropic call per message, capped at 2048 output
# tokens each (core/ai_provider.py) — generous burst allowance since normal
# back-and-forth conversation shouldn't ever feel throttled.
chat_limiter = SlidingWindowLimiter(max_calls=40, window_seconds=10 * 60)

# codegen_agent / Agent Terminal -> a full agentic sandbox turn (real bash +
# file-edit tool use, potentially many LLM round-trips per turn) against the
# operator's own logged-in Claude subscription — much more expensive per
# call than a chat message, so capped tighter.
codegen_limiter = SlidingWindowLimiter(max_calls=15, window_seconds=60 * 60)
