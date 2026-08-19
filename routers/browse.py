"""
Read-only content browser — proxies the CMS's own public read REST API so
the structured picker UI (static/browse.html) can list/prefill existing
content without going through the chat/LLM path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
import httpx

from auth.deps import require_session
from core.config import settings

router = APIRouter(dependencies=[Depends(require_session)])

_KIND_TO_COLLECTION = {
    "pages": "pages",
    "posts": "posts",
    "resources": "resources",
    "case-studies": "case-studies",
    "navigation": "navigation",
}


@router.get("/apps")
def list_apps() -> dict:
    return {
        "kinds": [
            {"kind": "pages", "label": "Pages"},
            {"kind": "posts", "label": "Posts / Insights"},
            {"kind": "resources", "label": "Resources"},
            {"kind": "case-studies", "label": "Case Studies"},
            {"kind": "navigation", "label": "Navigation"},
        ]
    }


@router.get("/content/{kind}")
def list_content(kind: str, limit: int = 100) -> dict:
    collection = _KIND_TO_COLLECTION.get(kind)
    if not collection:
        raise HTTPException(status_code=400, detail=f"Unknown kind '{kind}'")
    resp = httpx.get(f"{settings.cms_url}/api/{collection}?limit={limit}&depth=0", timeout=15)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"CMS list failed: {resp.text[:300]}")
    return resp.json()


@router.get("/content/{kind}/{doc_id}")
def get_content(kind: str, doc_id: str) -> dict:
    collection = _KIND_TO_COLLECTION.get(kind)
    if not collection:
        raise HTTPException(status_code=400, detail=f"Unknown kind '{kind}'")
    resp = httpx.get(f"{settings.cms_url}/api/{collection}/{doc_id}?depth=0", timeout=15)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"CMS get failed: {resp.text[:300]}")
    return resp.json()
