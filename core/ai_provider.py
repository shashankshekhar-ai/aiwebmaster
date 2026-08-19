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
    key = settings.anthropic_api_key if provider == "anthropic" else settings.gemini_api_key
    return {"provider": provider, "model": DEFAULT_MODELS.get(provider, ""), "api_key": key}


def list_models(provider: str, api_key: str) -> list[str]:
    """Live model list from the provider's API, falling back to a curated
    static list if the provider has no list-models endpoint reachable here."""
    provider = provider.lower()
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

    return {"reply": parsed.get("reply", ""), "proposal": parsed.get(tool_name)}
