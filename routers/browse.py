"""
Read-only content browser — proxies the CMS's own public read REST API so
the structured picker UI (static/browse.html) can list/prefill existing
content without going through the chat/LLM path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
import httpx

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_permission
from core.config import settings
from core.site_index import list_code_pages
from db.audit import log_event

router = APIRouter(dependencies=[Depends(require_session)])

# Pages are indexed straight from the repo (see core/site_index.py) — the
# CMS `pages` collection is effectively unused (1 draft + junk test docs),
# so showing it here would undercount what the site actually has. Navigation
# is NOT in this set: it used to be equally unused, but the header/footer
# nav was migrated onto the CMS `navigation` collection (seeded for real,
# verified live) — Header.tsx/Footer.tsx now read it as primary, hardcoded
# array is fallback-only. See HANDOFF.md.
_CODE_INDEXED_KINDS = {"pages"}

_KIND_TO_COLLECTION = {
    "pages": "pages",
    "posts": "posts",
    "resources": "resources",
    "case-studies": "case-studies",
    "navigation": "navigation",
    "faqs": "faqs",
    "testimonials": "testimonials",
    "media": "media",
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
            {"kind": "faqs", "label": "FAQs"},
            {"kind": "testimonials", "label": "Testimonials"},
            {"kind": "media", "label": "Media"},
        ]
    }


@router.get("/content/{kind}")
def list_content(kind: str, limit: int = 100) -> dict:
    if kind == "pages":
        return {"docs": list_code_pages()}

    collection = _KIND_TO_COLLECTION.get(kind)
    if not collection:
        raise HTTPException(status_code=400, detail=f"Unknown kind '{kind}'")
    resp = httpx.get(f"{settings.cms_url}/api/{collection}?limit={limit}&depth=0", timeout=15)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"CMS list failed: {resp.text[:300]}")
    return resp.json()


@router.get("/content/{kind}/{doc_id}")
def get_content(kind: str, doc_id: str) -> dict:
    if kind in _CODE_INDEXED_KINDS:
        raise HTTPException(status_code=400, detail=f"'{kind}' is code-indexed, not a CMS doc — edit the file directly")

    collection = _KIND_TO_COLLECTION.get(kind)
    if not collection:
        raise HTTPException(status_code=400, detail=f"Unknown kind '{kind}'")
    resp = httpx.get(f"{settings.cms_url}/api/{collection}/{doc_id}?depth=0", timeout=15)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"CMS get failed: {resp.text[:300]}")
    return resp.json()


@router.get("/media/file/{filename:path}")
def media_file(filename: str) -> Response:
    """Proxies the CMS's media file bytes through AIwebmaster's own origin.
    The CMS runs on a separate port that isn't guaranteed reachable from
    wherever the browser actually is (AIwebmaster is the only service meant
    to be browser-facing) — without this, `<img src="d.url">` (a CMS-relative
    path returned as-is by /content/media) 404s and every media card shows
    no preview."""
    resp = httpx.get(f"{settings.cms_url}/api/media/file/{filename}", timeout=15)
    if resp.status_code >= 400:
        raise HTTPException(status_code=404, detail="Media file not found")
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "application/octet-stream"))


@router.post("/media/upload")
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    alt: str = Form(...),
    caption: str | None = Form(None),
) -> dict:
    """Real "upload from your computer" — the only prior path was the
    `media` chat action pasting a public image URL. Proxies straight
    through to the CMS's new `/media-agent/upload-file` (service-token
    gated, same size/type validation as the URL-fetch endpoint), bypassing
    the chat/LLM path entirely, same as every other Browse read here."""
    try:
        require_permission(request.state.user, "media")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    content = await file.read()
    files = {"file": (file.filename or "upload", content, file.content_type or "application/octet-stream")}
    data = {"alt": alt}
    if caption:
        data["caption"] = caption

    resp = httpx.post(
        f"{settings.cms_url}/api/media-agent/upload-file",
        headers={"x-service-token": settings.cms_service_token},
        files=files,
        data=data,
        timeout=30,
    )
    ok = resp.status_code < 400
    result = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text[:300]}
    log_event(
        event="executed",
        action_type="media_upload_file",
        action_id=f"media-upload-{file.filename}",
        actor=request.state.user.get("email"),
        payload={"filename": file.filename, "alt": alt, "size": len(content)},
        result=result,
        ok=ok,
    )
    if not ok:
        raise HTTPException(status_code=502, detail=f"CMS upload failed: {resp.text[:300]}")
    return result


@router.post("/media/{media_id}/replace")
async def replace_media(
    media_id: str,
    request: Request,
    file: UploadFile = File(...),
    alt: str | None = Form(None),
) -> dict:
    """Swaps a media doc's file bytes IN PLACE — same doc id, same url.
    Anything already referencing this media id (a page's hero image, a
    testimonial photo) picks up the new image automatically, no re-pointing
    needed."""
    try:
        require_permission(request.state.user, "media")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    content = await file.read()
    files = {"file": (file.filename or "upload", content, file.content_type or "application/octet-stream")}
    data = {"id": media_id}
    if alt:
        data["alt"] = alt

    resp = httpx.post(
        f"{settings.cms_url}/api/media-agent/replace-file",
        headers={"x-service-token": settings.cms_service_token},
        files=files,
        data=data,
        timeout=30,
    )
    ok = resp.status_code < 400
    result = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text[:300]}
    log_event(
        event="executed",
        action_type="media_replace_file",
        action_id=f"media-replace-{media_id}",
        actor=request.state.user.get("email"),
        payload={"media_id": media_id, "filename": file.filename, "size": len(content)},
        result=result,
        ok=ok,
    )
    if not ok:
        raise HTTPException(status_code=502, detail=f"CMS replace failed: {resp.text[:300]}")
    return result


@router.delete("/media/{media_id}")
def delete_media(media_id: str, request: Request) -> dict:
    try:
        require_permission(request.state.user, "media")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    resp = httpx.post(
        f"{settings.cms_url}/api/media-agent/delete",
        headers={"x-service-token": settings.cms_service_token, "content-type": "application/json"},
        json={"id": media_id},
        timeout=15,
    )
    ok = resp.status_code < 400
    result = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text[:300]}
    log_event(
        event="executed",
        action_type="media_delete",
        action_id=f"media-delete-{media_id}",
        actor=request.state.user.get("email"),
        payload={"media_id": media_id},
        result=result,
        ok=ok,
    )
    if not ok:
        raise HTTPException(status_code=502, detail=f"CMS delete failed: {resp.text[:300]}")
    return result
