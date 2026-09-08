"""
Indexes the site's REAL pages — straight from the repo, not the CMS.

The CMS `pages` collection is effectively unused: one draft "Home" doc plus
a handful of leftover QA/demo test docs. The actual site renders ~20 routes
hardcoded as Next.js `page.tsx` files. So hardcoded IS the real source of
truth today for pages — this module reads it directly instead of a
mostly-empty CMS table, so Browse stops undercounting how many pages exist.

(Navigation used to need the same treatment — see git history for
`list_code_nav` — but was migrated onto the CMS `navigation` collection
instead: seeded for real, Header.tsx/Footer.tsx verified reading it live.
Browse's Navigation tab proxies the CMS directly now, same as any other
CMS-backed kind.)
"""
from __future__ import annotations

from pathlib import Path

from core.config import settings

_APP_DIR = "apps/web/app"
_EXCLUDE_TOP_SEGMENTS = {"api"}


def list_code_pages() -> list[dict]:
    repo_root = Path(settings.repo_path)
    app_dir = repo_root / _APP_DIR
    if not app_dir.is_dir():
        return []

    pages = []
    for page_file in sorted(app_dir.rglob("page.tsx")):
        rel_dir = page_file.parent.relative_to(app_dir)
        segments = [p for p in rel_dir.parts if not (p.startswith("(") and p.endswith(")"))]
        if segments and segments[0] in _EXCLUDE_TOP_SEGMENTS:
            continue
        route = "/" + "/".join(segments) if segments else "/"
        pages.append({
            "id": route,
            "title": route,
            "slug": route,
            "dynamic": "[" in route,
            "file": str(page_file.relative_to(repo_root)),
            "source": "code",
        })
    return pages
