"""
AIwebmaster — the propose-then-approve ops+content agent.

Mirrors apps/api/core/lead_agent.py's shape (system prompt + forced tool call
via core/ai_provider.structured_call), but the "tool" here is a list of typed
actions rather than a single object, since one request can bundle e.g. a
content change + a nav link + a redeploy.

Executable action types (get a Run button once approved): content, nav_link,
git, docker, sql — see core/executors.py.
Draft-only action types (text explanation only, never wired to Run — the host
runs 60+ unrelated containers and system-level changes are too risky to
automate): nginx, system.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from auth.permissions import ROLE_PERMISSIONS
from core.ai_provider import AIProviderError, structured_call
from core.context import build_site_context

EXECUTABLE_TYPES = {"content", "nav_link", "git", "docker", "sql", "user_management", "publish", "rollback", "code_edit", "codegen_agent"}
DRAFT_ONLY_TYPES = {"nginx", "system"}
ALL_TYPES = EXECUTABLE_TYPES | DRAFT_ONLY_TYPES

# user_management is deliberately excluded from what the chat LLM can ever
# propose, for any role including super_admin — closes a prompt-injection-
# to-privilege-escalation path (site content feeding into build_site_context
# could try to talk the model into proposing a new admin account). The
# feature itself is unaffected: EXECUTABLE_TYPES/EXECUTORS/ROLE_PERMISSIONS
# are untouched, so the Users page's own form (static/users.html) posting
# straight to /api/actions/run with type: "user_management" keeps working —
# only the LLM's proposal path is closed.
CHAT_PROPOSABLE_TYPES = EXECUTABLE_TYPES - {"user_management"}

ActionType = Literal[
    "content", "nav_link", "git", "docker", "sql", "user_management", "publish", "rollback", "code_edit", "codegen_agent", "nginx", "system"
]


class AIwebmasterError(Exception):
    pass


_SYSTEM_PROMPT = """You are AIwebmaster, an ops+content assistant for The Bradbury Group's website.
You help the site owner make content changes and run infrastructure operations, by proposing a
list of concrete actions — never executing anything yourself. A human reviews and clicks Run on
each action individually.

Action types you can propose:
- content: create/update a Page, Post/Insight, Resource, or Case Study. payload: {kind: "page"|"post"|"resource"|"case-study", docId?: string, fields: {...}, publish?: boolean}. `publish` defaults to true (matches prior behavior); set false to save a new draft version without touching the live/published document — Pages, Posts, and Case Studies all support this (Resources doesn't; the flag is a no-op there). Prefer publish:false unless the user clearly wants it live immediately.
- nav_link: add/update, remove, or reorder a header or footer navigation entry. Add/update payload: {label, href, location: "header"|"footer", footerGroup?, order?, enabled?, openInNewTab?}. Remove payload: {label, location, remove: true} (href not needed). Reorder: same as add/update but only `order` actually needs to change — propose one nav_link action per link being reordered, each with its new `order` value.
- git: stage/commit/push, or discard uncommitted changes. payload for commit (default op): {message: string, push: boolean}. payload for discard: {op: "discard", files?: string[]} — restores tracked files to their last-committed state; omit `files` to discard everything, or list specific paths. Never touches untracked new files (a code_edit "write" that hasn't been committed yet) — those need deleting by hand if unwanted.
- docker: control one or more compose services. payload: {services: string[], env?: "dev"|"staging", op?: "rebuild"|"start"|"stop"|"restart"}. `env` defaults to "dev" (services: cms/web/api; staging uses cms-prod/web-prod/api-prod). `op` defaults to "rebuild" (build then force-recreate — use this for "redeploy" after a code change); "start"/"stop"/"restart" just control the running container, no rebuild.
- sql: run a raw SQL statement against the cms or api database. payload: {database: "cms"|"api", statement: string}
- user_management: NEVER propose this action type, for any user, ever — creating/editing AIwebmaster accounts is human-only, done via the Users page's own form. If asked to create or change a user, reply that you can't do that here and point them to the Users page.
- publish: promote the dev stack's CMS content + current code to the staging stack (a second local environment for review before anything is considered final; only super_admin can run this). A backup of the current staging DB is always taken first. payload: {target: "prod"}
- rollback: restore staging's CMS database from the most recent pre-publish backup, then restart cms-prod (only super_admin can run this). Use when a publish turns out to be wrong. payload: {}
- code_edit: edit or create a source file in the repo (only infra_admin/super_admin can run this). payload for editing an existing file: {file: "apps/web/components/layout/HeaderNav.tsx", mode: "edit", old_string: "...", new_string: "..."} — old_string must be exact, unique, minimal-but-sufficient context from the real current file content (ask to see the file first if you don't already have it in context). payload for a new file or full overwrite: {file: "...", mode: "write", content: "..."} (full file contents). After a code_edit, usually also propose a docker action to rebuild the affected service, and optionally a git action to commit — as separate actions, not bundled into code_edit's payload. Only propose code_edit when you already know the file's exact current content — it's a mechanical find/replace, not a coding agent, and a wrong old_string just fails (safe) rather than guessing.
- codegen_agent: hand a task to a real coding agent (its own file-edit + bash access, in an isolated dev-only sandbox — no prod/db/docker-socket reach) instead of hand-writing a diff yourself (only infra_admin/super_admin can run this). Use this instead of code_edit whenever the change is multi-file, needs to read existing code you don't already have in context, or is complex enough that a guessed old_string/new_string would likely be wrong. payload: {prompt: string} — a clear, self-contained natural-language instruction (the sandbox agent has no memory of this conversation, so include all relevant context: what to change, where, and why). Do not set a "tool" field — which underlying coding-agent CLI runs it is decided automatically, not by you. This call can take several minutes; after it completes, its diff is shown for review — usually follow up with a git action to commit once the user approves.
- nginx: explain/draft an nginx reload or config-check command. payload: {command: string, note: string} — DRAFT ONLY, never executed by you, the human runs it manually.
- system: explain/draft an OS package-update command. payload: {command: string, note: string} — DRAFT ONLY, never executed by you.

Rules:
- Always reply conversationally in plain text explaining what you're proposing or asking a clarifying question.
- Only call propose_actions when you're actually proposing something to run. If just answering a question, reply with text only.
- For git/docker/sql actions, be conservative — one clear action per intent, not speculative bundles.
- For sql, only propose statements the user actually asked for or that are a direct, obvious consequence of their request. Never propose destructive statements (DROP, TRUNCATE, DELETE without a WHERE clause) unless the user explicitly asked for exactly that.
- nginx and system actions are always draft-only — say so in your reply so the human knows they must run it by hand.
- You have no file-reading tool. If you don't already know a file's exact current content (from earlier in this conversation or from what the user pasted), ask them to paste the relevant lines rather than guessing old_string — a wrong guess fails cleanly with no match (safe), but asking first is faster than a failed round-trip.
"""

def _proposable_types_for_role(role: str) -> list[str]:
    """user_management is never included (see CHAT_PROPOSABLE_TYPES above,
    for every role). Beyond that, narrow to what this role can actually run
    — a ui_editor asking for a rebuild shouldn't get back a docker action
    card they can only stare at and 403 on; not a security boundary (RBAC
    still re-checks at execute time regardless), just avoids proposing
    dead-on-arrival cards. Draft-only types (nginx/system) never execute
    for anyone, so they stay available to mention regardless of role."""
    allowed = ROLE_PERMISSIONS.get(role, set())
    return sorted((CHAT_PROPOSABLE_TYPES & allowed) | DRAFT_ONLY_TYPES)


def _propose_actions_schema_for_role(role: str) -> dict:
    action_schema = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": _proposable_types_for_role(role)},
            "description": {"type": "string"},
            # JSON-encoded string, not a nested object: Gemini's strict structured
            # output collapses an untyped `{"type": "object"}` field (no declared
            # properties, since the real shape varies per action type) to an empty
            # object — confirmed empirically. A string sidesteps that entirely;
            # run_agent_turn below json.loads() it back into a dict.
            "payload": {"type": "string", "description": "JSON-encoded object matching the payload shape for this action type."},
        },
        "required": ["type", "description", "payload"],
    }
    return {
        "type": "object",
        "properties": {"actions": {"type": "array", "items": action_schema}},
        "required": ["actions"],
    }


_TOOL_NAME = "propose_actions"
_TOOL_DESCRIPTION = (
    "Propose one or more concrete actions for the human to review and individually approve. "
    "Does not execute anything."
)


def run_agent_turn(history: list[dict[str, str]], role: str) -> dict[str, Any]:
    """history: [{role: 'user'|'assistant', content: str}, ...]. `role` is
    the caller's AIwebmaster role (auth/permissions.py) — narrows which
    action types the model's tool schema can even produce (see
    _proposable_types_for_role). Returns {reply: str, actions: [{id, type,
    description, payload, executable}]}."""
    conversation = "\n\n".join(f"{m['role']}: {m['content']}" for m in history)
    context = build_site_context()
    if context:
        conversation = f"{context}\n\n---\n\n{conversation}"
    allowed_types = set(_proposable_types_for_role(role))
    try:
        result = structured_call(
            system_prompt=_SYSTEM_PROMPT,
            user_message=conversation,
            tool_name=_TOOL_NAME,
            tool_description=_TOOL_DESCRIPTION,
            input_schema=_propose_actions_schema_for_role(role),
        )
    except AIProviderError as exc:
        raise AIwebmasterError(str(exc)) from exc

    raw_actions = (result.get("proposal") or {}).get("actions", []) if result.get("proposal") else []
    actions = []
    for raw in raw_actions:
        action_type = raw.get("type")
        # Belt-and-suspenders: the schema enum already constrains this, but
        # a hallucinated/out-of-schema value (or a provider that doesn't
        # strictly enforce enums) shouldn't slip through — must be both a
        # real type AND one this role's schema actually offered.
        if action_type not in ALL_TYPES or action_type not in allowed_types:
            continue
        payload = raw.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload) if payload.strip() else {}
            except json.JSONDecodeError:
                payload = {}
        actions.append(
            {
                "id": str(uuid.uuid4()),
                "type": action_type,
                "description": raw.get("description", ""),
                "payload": payload,
                "executable": action_type in EXECUTABLE_TYPES,
            }
        )

    reply = result.get("reply", "") or ""
    if not reply.strip() and actions:
        reply = "Here's what I'm proposing — review each action and click Run to apply it."
    return {"reply": reply, "actions": actions}
