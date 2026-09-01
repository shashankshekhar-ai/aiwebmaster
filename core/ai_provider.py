"""
AI provider abstraction — same pattern as apps/api/core/ai_provider.py and
apps/cms/src/lib/aiProvider.ts, kept as its own copy since this is a separate
deployable service with no shared package to import from.

Provider/model/key resolve DB-first (core/ai_settings.py, configurable from
the Settings UI) falling back to env vars (core/config.py) so an env-only
deploy keeps working untouched until someone configures via the UI.
"""
from __future__ import annotations

import json
from typing import Any

from core.ai_settings import get_active_ai_settings
from core.config import settings
from db.ai_usage import log_usage


def _log_usage_safe(*, provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
    # Usage logging is best-effort telemetry, never allowed to break a chat reply.
    try:
        log_usage(provider=provider, model=model, input_tokens=input_tokens, output_tokens=output_tokens)
    except Exception:
        pass

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-flash-latest",
    "openai": "gpt-4o",
    # Alias, not a pinned model name — the CLI resolves "haiku"/"sonnet"/
    # "opus"/"fable" to whatever's current itself. haiku default matches
    # "low effort" (fast/cheap) being the point of this provider over a
    # billed API key.
    "claude_cli": "haiku",
}

_GEMINI_TYPE_MAP = {
    "object": "OBJECT",
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "array": "ARRAY",
    "boolean": "BOOLEAN",
}


class AIProviderError(Exception):
    pass


def _active_config() -> dict[str, str]:
    """Resolves {provider, model, api_key} — DB row if configured, else env vars."""
    row = get_active_ai_settings()
    if row:
        return {"provider": row["provider"], "model": row["model"], "api_key": row["api_key"]}

    provider = settings.ai_provider.lower()
    # claude_cli needs no key at all — it authenticates via the claude-agent
    # sandbox's persisted OAuth login (a Claude subscription), not an API
    # key. openai previously fell through to gemini_api_key here by mistake
    # (dead code until openai was actually selected with no DB row) — each
    # provider now resolves its own key explicitly.
    key = {
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
        "openai": getattr(settings, "openai_api_key", ""),  # no OPENAI_API_KEY field/env wired up yet — DB-configured key (Settings UI) is the only way to use this provider today
        "claude_cli": "",
    }.get(provider, "")
    return {"provider": provider, "model": DEFAULT_MODELS.get(provider, ""), "api_key": key}


def list_models(provider: str, api_key: str) -> list[str]:
    """Live model list from the provider's API, falling back to a curated
    static list if the provider has no list-models endpoint reachable here."""
    provider = provider.lower()
    if provider == "claude_cli":
        # No key, no list-models API to call — the CLI resolves these
        # aliases to whatever's current itself.
        return ["haiku", "sonnet", "opus", "fable"]
    try:
        if provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            return [m.id for m in client.models.list(limit=50).data]
        if provider == "openai":
            import openai

            client = openai.OpenAI(api_key=api_key)
            ids = [m.id for m in client.models.list().data]
            return sorted(m for m in ids if "gpt" in m or "o1" in m or "o3" in m) or ids
        if provider == "gemini":
            from google import genai

            client = genai.Client(api_key=api_key)
            return [m.name.replace("models/", "") for m in client.models.list()]
    except Exception as exc:
        raise AIProviderError(f"Could not fetch models for {provider}: {exc}") from exc
    raise AIProviderError(f"Unknown provider '{provider}'")


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "type" in schema:
        out["type"] = _GEMINI_TYPE_MAP.get(schema["type"], str(schema["type"]).upper())
    if "enum" in schema:
        out["enum"] = schema["enum"]
    if "properties" in schema:
        out["properties"] = {k: _to_gemini_schema(v) for k, v in schema["properties"].items()}
    if "items" in schema:
        out["items"] = _to_gemini_schema(schema["items"])
    if "required" in schema:
        out["required"] = schema["required"]
    return out


def structured_call(
    *,
    system_prompt: str,
    user_message: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Returns {"reply": str, "proposal": dict | None}."""
    cfg = _active_config()
    if cfg["provider"] == "gemini":
        return _call_gemini(cfg, system_prompt, user_message, tool_name, input_schema, max_tokens)
    if cfg["provider"] == "openai":
        return _call_openai(cfg, system_prompt, user_message, tool_name, tool_description, input_schema, max_tokens)
    if cfg["provider"] == "claude_cli":
        return _call_claude_cli(cfg, system_prompt, user_message, tool_name)
    return _call_anthropic(cfg, system_prompt, user_message, tool_name, tool_description, input_schema, max_tokens)


def _call_anthropic(
    cfg: dict[str, str],
    system_prompt: str,
    user_message: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    import anthropic

    if not cfg["api_key"]:
        raise AIProviderError("No Anthropic API key configured (Settings page or ANTHROPIC_API_KEY)")

    tool = {"name": tool_name, "description": tool_description, "input_schema": input_schema}
    try:
        client = anthropic.Anthropic(api_key=cfg["api_key"])
        response = client.messages.create(
            model=cfg["model"] or DEFAULT_MODELS["anthropic"],
            max_tokens=max_tokens,
            system=system_prompt,
            tools=[tool],
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        raise AIProviderError(f"Anthropic API call failed: {exc}") from exc

    _log_usage_safe(
        provider="anthropic",
        model=cfg["model"] or DEFAULT_MODELS["anthropic"],
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    reply = ""
    proposal = None
    for block in response.content:
        if block.type == "text":
            reply += block.text
        elif block.type == "tool_use" and block.name == tool_name:
            proposal = block.input
    return {"reply": reply, "proposal": proposal}


def _normalize_proposal(tool_name: str, proposal: Any) -> dict[str, Any] | None:
    """The schema is {tool_name: {"actions": [...]}}, but a provider without
    real schema enforcement (claude_cli's free-text JSON, no forced tool-use)
    can reasonably "simplify" that to {tool_name: [...]} directly — confirmed
    live. run_agent_turn always expects the wrapped shape; normalize here so
    every _call_* returns the same shape regardless of what the model did."""
    if isinstance(proposal, list):
        return {"actions": proposal}
    return proposal


def _strip_json_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[: -3]
    return t.strip()


def _call_claude_cli(
    cfg: dict[str, str],
    system_prompt: str,
    user_message: str,
    tool_name: str,
) -> dict[str, Any]:
    """Routes through the `claude-agent` sandbox's persisted Claude Code CLI
    login (a Claude Pro/Max subscription — see infra/claude-agent/README.md
    and docker-compose.yml's comment on that service) instead of a billed
    Anthropic API key. No `structured_call`/forced-tool-use available this
    way — same technique as _call_gemini: the system prompt requires a bare
    JSON object with "reply"/tool_name keys, parsed out of the CLI's
    `--output-format json` result text. Slower than a direct API call (shells
    out to a fresh `docker compose run`) and NOT free — the CLI reports a
    real total_cost_usd per call even though it's covered by the
    subscription's included usage rather than billed separately; it just
    doesn't consume the AI provider's own $ budget the way an API key would.
    """
    import subprocess

    from core.config import settings

    model = cfg["model"] or DEFAULT_MODELS["claude_cli"]
    json_system_prompt = (
        f"{system_prompt}\n\nAlways respond with ONLY a raw JSON object (no markdown fences, no prose "
        f'outside the JSON) having two top-level keys: "reply" (your conversational text) and '
        f'"{tool_name}" (the complete proposed actions list if you\'re proposing changes, or null if '
        "you're just answering a question)."
    )
    cmd = [
        "docker", "compose", "-f", f"{settings.repo_path}/docker-compose.yml",
        "--project-directory", settings.host_repo_path,
        "-p", settings.compose_project, "run", "--rm", "-T",
        "-e", "QUERY_MODE=1",
        "-e", f"QUERY_MODEL={model}",
        "-e", "QUERY_EFFORT=low",
        "-e", f"QUERY_SYSTEM_PROMPT={json_system_prompt}",
        "claude-agent", user_message,
    ]
    try:
        result = subprocess.run(cmd, cwd=settings.repo_path, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AIProviderError("claude-agent CLI call timed out after 120s") from exc
    if result.returncode != 0:
        raise AIProviderError(f"claude-agent CLI call failed (exit {result.returncode}): {result.stderr[-500:] or result.stdout[-500:]}")

    try:
        wrapper = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"claude-agent CLI returned non-JSON output: {exc}") from exc
    if wrapper.get("is_error"):
        raise AIProviderError(f"claude-agent CLI reported an error: {wrapper.get('result', '')[:300]}")

    _log_usage_safe(
        provider="claude_cli",
        model=model,
        input_tokens=(wrapper.get("usage") or {}).get("input_tokens", 0),
        output_tokens=(wrapper.get("usage") or {}).get("output_tokens", 0),
    )

    raw_text = _strip_json_fences(wrapper.get("result", ""))
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"claude-agent response was not valid JSON: {exc}") from exc

    return {"reply": parsed.get("reply", ""), "proposal": _normalize_proposal(tool_name, parsed.get(tool_name))}


def _call_openai(
    cfg: dict[str, str],
    system_prompt: str,
    user_message: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    import openai

    if not cfg["api_key"]:
        raise AIProviderError("No OpenAI API key configured (Settings page)")

    tool = {
        "type": "function",
        "function": {"name": tool_name, "description": tool_description, "parameters": input_schema},
    }
    try:
        client = openai.OpenAI(api_key=cfg["api_key"])
        response = client.chat.completions.create(
            model=cfg["model"] or DEFAULT_MODELS["openai"],
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=[tool],
        )
    except openai.APIError as exc:
        raise AIProviderError(f"OpenAI API call failed: {exc}") from exc

    if response.usage:
        _log_usage_safe(
            provider="openai",
            model=cfg["model"] or DEFAULT_MODELS["openai"],
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )

    message = response.choices[0].message
    reply = message.content or ""
    proposal = None
    if message.tool_calls:
        for call in message.tool_calls:
            if call.function.name == tool_name:
                try:
                    proposal = json.loads(call.function.arguments)
                except json.JSONDecodeError as exc:
                    raise AIProviderError(f"OpenAI returned invalid tool arguments JSON: {exc}") from exc
                break
    return {"reply": reply, "proposal": proposal}


def _call_gemini(
    cfg: dict[str, str],
    system_prompt: str,
    user_message: str,
    tool_name: str,
    input_schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    if not cfg["api_key"]:
        raise AIProviderError("No Gemini API key configured (Settings page or GEMINI_API_KEY)")

    response_schema = {
        "type": "OBJECT",
        "properties": {
            "reply": {"type": "STRING"},
            tool_name: _to_gemini_schema(input_schema),
        },
        "required": ["reply"],
    }
    system = (
        f"{system_prompt}\n\nAlways respond with a JSON object having two top-level keys: "
        f'"reply" (your conversational text) and "{tool_name}" (the complete proposed actions '
        "list if you're proposing changes, or null if you're just answering a question)."
    )
    try:
        client = genai.Client(api_key=cfg["api_key"])
        response = client.models.generate_content(
            model=cfg["model"] or DEFAULT_MODELS["gemini"],
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )
    except Exception as exc:
        raise AIProviderError(f"Gemini API call failed: {exc}") from exc

    if response.usage_metadata:
        _log_usage_safe(
            provider="gemini",
            model=cfg["model"] or DEFAULT_MODELS["gemini"],
            input_tokens=response.usage_metadata.prompt_token_count or 0,
            output_tokens=response.usage_metadata.candidates_token_count or 0,
        )

    if not response.text:
        raise AIProviderError("Gemini model returned no content")
    try:
        parsed = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"Gemini response was not valid JSON: {exc}") from exc

    return {"reply": parsed.get("reply", ""), "proposal": _normalize_proposal(tool_name, parsed.get(tool_name))}
