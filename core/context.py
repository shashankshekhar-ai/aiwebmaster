"""
Live site-context snapshot — fed into the model's user_message alongside chat
history so proposals are grounded in what actually exists, not guessed blind.
Best-effort: any failure here should never block chat.
"""
from __future__ import annotations

import logging
import subprocess
from functools import lru_cache
from pathlib import Path

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

_AGENT_CONTEXT_PATH = Path(__file__).resolve().parent.parent / "AGENT_CONTEXT.md"


@lru_cache(maxsize=1)
def _static_context() -> str:
    try:
        return _AGENT_CONTEXT_PATH.read_text()
    except OSError:
        logger.warning("AGENT_CONTEXT.md not found at %s", _AGENT_CONTEXT_PATH)
        return ""


def _fetch_cms_list(path: str, *, limit: int = 20) -> list[dict]:
    try:
        resp = httpx.get(f"{settings.cms_url}/api/{path}?limit={limit}", timeout=5)
        resp.raise_for_status()
        return resp.json().get("docs", [])
    except Exception:
        logger.warning("context: failed to fetch %s", path, exc_info=True)
        return []


def _docker_ps() -> str:
    try:
        result = subprocess.run(
            ["docker", "compose", "-p", settings.compose_project, "ps", "--format", "table {{.Name}}\\t{{.Status}}"],
            cwd=settings.repo_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip()
    except Exception:
        logger.warning("context: failed to run docker compose ps", exc_info=True)
        return ""


def build_site_context() -> str:
    static = _static_context()

    pages = _fetch_cms_list("pages")
    posts = _fetch_cms_list("posts")
    nav = _fetch_cms_list("navigation", limit=50)
    services = _docker_ps()

    lines = ["Current site state (live snapshot, for grounding your proposals):"]

    if pages:
        lines.append("Pages: " + ", ".join(f"{p.get('title')} (/{p.get('slug')})" for p in pages))
    if posts:
        lines.append("Posts/Insights: " + ", ".join(f"{p.get('title')} (/insights/{p.get('slug')})" for p in posts))
    if nav:
        lines.append(
            "Navigation: "
            + ", ".join(f"{n.get('label')} -> {n.get('href')} [{n.get('location')}]" for n in nav)
        )
    if services:
        lines.append("Docker services:\n" + services)

    dynamic = "\n".join(lines) if len(lines) > 1 else ""
    return "\n\n".join(part for part in (static, dynamic) if part)
