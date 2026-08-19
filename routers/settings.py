from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_permission
from core.ai_provider import AIProviderError, DEFAULT_MODELS, list_models
from core.ai_settings import PROVIDERS, get_active_ai_settings, save_ai_settings

router = APIRouter(dependencies=[Depends(require_session)])


def _require_settings_admin(request: Request) -> None:
    # Reuses the most-privileged permission check — only super_admin manages
    # AI provider/keys, same tier as user_management/publish.
    try:
        require_permission(request.state.user, "user_management")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/settings/ai")
def get_ai_settings(request: Request) -> dict:
    _require_settings_admin(request)
    row = get_active_ai_settings()
    if not row:
        return {"configured": False, "providers": sorted(PROVIDERS), "default_models": DEFAULT_MODELS}
    return {
        "configured": True,
        "provider": row["provider"],
        "model": row["model"],
        "api_key_masked": row["api_key"][:4] + "…" + row["api_key"][-4:] if len(row["api_key"]) > 8 else "····",
        "providers": sorted(PROVIDERS),
        "default_models": DEFAULT_MODELS,
    }


@router.post("/settings/ai")
def set_ai_settings(body: dict, request: Request) -> dict:
    _require_settings_admin(request)
    provider = body.get("provider")
    model = body.get("model")
    api_key = body.get("api_key")
    if provider not in PROVIDERS or not model or not api_key:
        raise HTTPException(status_code=400, detail=f"provider (one of {sorted(PROVIDERS)}), model, and api_key are required")
    save_ai_settings(provider=provider, model=model, api_key=api_key)
    return {"ok": True}


@router.get("/settings/ai/models")
def fetch_models(provider: str, api_key: str, request: Request) -> dict:
    _require_settings_admin(request)
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider must be one of {sorted(PROVIDERS)}")
    try:
        return {"models": list_models(provider, api_key)}
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
