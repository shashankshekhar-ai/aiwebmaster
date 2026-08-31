"""
Doctor — read-only health checks for things that fail silently otherwise.
Two real incidents motivated this: NVIDIA_API_KEY missing from a sibling
app's docker-compose env block (container ran fine, provider just never
worked), and tbg_api_prod/tbg_cms_prod never existing (Publish/Rollback
would 500 the moment anyone clicked them) — neither showed up anywhere in
the UI until someone went looking by hand. This page is that look, automated.

Deliberately narrow: only checks this app's own responsibilities (its env
vars, its 4 databases, its CMS connection). Not a general infra monitor.
"""
from __future__ import annotations

import json
import subprocess

import httpx
import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_permission
from core.config import settings

router = APIRouter(dependencies=[Depends(require_session)])


def _require_docker_view(request: Request) -> None:
    try:
        require_permission(request.state.user, "docker")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def _check_env() -> list[dict]:
    checks = []
    checks.append(_check("SESSION_SECRET", bool(settings.session_secret), "set" if settings.session_secret else "empty — sessions sign with an empty key"))
    checks.append(_check("CMS_SERVICE_TOKEN", bool(settings.cms_service_token), "set" if settings.cms_service_token else "empty — content/nav_link/media actions will 401 against the CMS"))
    key_field = "anthropic_api_key" if settings.ai_provider == "anthropic" else "gemini_api_key" if settings.ai_provider == "gemini" else None
    if key_field:
        has_key = bool(getattr(settings, key_field))
        checks.append(_check(f"AI provider key ({settings.ai_provider})", has_key, "set" if has_key else f"empty — chat will fail, AI_PROVIDER={settings.ai_provider}"))
    else:
        checks.append(_check("AI provider key", False, f"unrecognized AI_PROVIDER={settings.ai_provider!r}"))
    return checks


def _check_compose_project() -> dict:
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.project={settings.compose_project}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        names = [n for n in result.stdout.splitlines() if n.strip()]
    except Exception as exc:
        return _check("COMPOSE_PROJECT", False, f"check failed: {exc}")
    ok = len(names) > 0
    return _check("COMPOSE_PROJECT", ok, f"'{settings.compose_project}' -> {len(names)} containers" if ok else f"'{settings.compose_project}' matches no running containers — docker/publish/codegen_agent actions will silently target nothing")


def _check_databases() -> list[dict]:
    dbs = {
        "tbg_api (dev)": settings.api_database_url,
        "tbg_cms (dev)": settings.cms_database_url,
        "tbg_api_prod (staging)": settings.api_database_url_prod,
        "tbg_cms_prod (staging)": settings.cms_database_url_prod,
    }
    checks = []
    for label, dsn in dbs.items():
        try:
            conn = psycopg2.connect(dsn, connect_timeout=5)
            conn.close()
            checks.append(_check(label, True, "reachable"))
        except Exception as exc:
            checks.append(_check(label, False, str(exc)[:200]))
    return checks


def _check_cms() -> dict:
    try:
        resp = httpx.get(f"{settings.cms_url}/api/pages?limit=1", headers={"x-service-token": settings.cms_service_token}, timeout=8)
        ok = resp.status_code < 400
        return _check("CMS reachable", ok, f"{settings.cms_url} -> {resp.status_code}")
    except Exception as exc:
        return _check("CMS reachable", False, f"{settings.cms_url} -> {str(exc)[:200]}")


@router.get("/doctor/check")
def doctor_check(request: Request) -> dict:
    _require_docker_view(request)
    checks = [
        *_check_env(),
        _check_compose_project(),
        *_check_databases(),
        _check_cms(),
    ]
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}
