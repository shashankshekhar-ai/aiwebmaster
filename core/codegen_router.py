"""
Decides which coding-agent sandbox handles a `codegen_agent` action —
Claude Code (`claude-agent`) or Codex (`codex-agent`). Deliberately a
separate, narrow decision from the main chat turn (core/aiwebmaster_agent.py):
the chat provider defaults to gemini-flash-latest, fast/cheap but not a model
you want making a nuanced tool-choice call buried inside a long conversation
schema. This module makes exactly one small structured_call whose only job is
picking a tool, using whatever AI provider is already configured in Settings.
"""
from __future__ import annotations

from core.ai_provider import AIProviderError, structured_call

VALID_TOOLS = {"claude", "codex"}

_ROUTER_SYSTEM_PROMPT = """You route a coding task to exactly one coding-agent CLI sandbox.
Pick "claude" (Claude Code CLI) or "codex" (OpenAI Codex CLI) — nothing else.

Guidance (a starting heuristic, not a hard rule):
- Prefer "claude" for: multi-file changes, anything touching React/Next.js/TypeScript
  under apps/web or apps/cms, changes that need broad context across the frontend,
  or when the task is ambiguous and benefits from careful multi-step reasoning.
- Prefer "codex" for: narrower, more mechanical changes, backend Python work under
  apps/api or apps/aiwebmaster, or scripted/CLI-style tasks.

Always call the pick_tool tool with your choice. Do not explain in prose."""

_TOOL_NAME = "pick_tool"
_TOOL_DESCRIPTION = "Pick which coding-agent CLI sandbox should handle this task."
_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": ["claude", "codex"]},
    },
    "required": ["tool"],
}


def _keyword_fallback(prompt: str) -> str:
    """Rule-based fallback if the router's own LLM call fails — must never
    hard-block the codegen_agent action just because routing itself broke."""
    lowered = prompt.lower()
    if any(hint in lowered for hint in ("apps/api", "apps/aiwebmaster", "python", "fastapi", "sql")):
        return "codex"
    return "claude"


def route_codegen(prompt: str, hint_tool: str | None = None) -> str:
    """Returns "claude" or "codex". `hint_tool`, if the chat model already
    set one on the payload, is advisory only — this function is authoritative."""
    try:
        result = structured_call(
            system_prompt=_ROUTER_SYSTEM_PROMPT,
            user_message=prompt,
            tool_name=_TOOL_NAME,
            tool_description=_TOOL_DESCRIPTION,
            input_schema=_TOOL_SCHEMA,
            max_tokens=256,
        )
        tool = (result.get("proposal") or {}).get("tool")
        if tool in VALID_TOOLS:
            return tool
    except AIProviderError:
        pass
    return _keyword_fallback(prompt)
