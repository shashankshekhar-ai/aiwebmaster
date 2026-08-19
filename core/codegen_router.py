"""
Which coding-agent sandbox handles a `codegen_agent` action. Always Claude
Code (`claude-agent`) — every proposed change here is ultimately a code
change, and Claude Code has consistently been the stronger of the two at
navigating this codebase (confirmed directly: multi-file React/Next.js
investigation and fixes this session). Used to dynamically pick between
Claude Code and Codex per task; simplified to always Claude Code per
decision — one coding agent, one behavior to trust, no per-task routing
call to a possibly-weaker chat model to get wrong.
"""
from __future__ import annotations


def route_codegen(prompt: str, hint_tool: str | None = None) -> str:
    return "claude"
