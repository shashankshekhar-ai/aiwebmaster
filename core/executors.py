"""
Executors — one function per executable action type. Every one of these is
only ever called after a human clicks Run on a specific proposed action
(routers/actions.py); nothing here runs autonomously.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
import psycopg2

from core.config import settings
from core.repo_state import git_state
from db.deploy_state import set_last_publish
from db.deployments import list_deployments, record_deployment


class ExecutionError(Exception):
    pass


def _run_subprocess(args: list[str], *, cwd: str, timeout: int = 300) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(f"Command timed out after {timeout}s: {' '.join(args)}") from exc
    return {
        "command": " ".join(args),
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-4000:],
        "ok": result.returncode == 0,
    }


def run_git(payload: dict[str, Any]) -> dict[str, Any]:
    op = payload.get("op", "commit")

    if op == "discard":
        # Restores tracked files to their last-committed state. Deliberately
        # does NOT touch untracked new files (e.g. from a code_edit "write")
        # — `git checkout --` can't recover those if wrong, so leaving them
        # alone is the safer default; delete them by hand if truly unwanted.
        files = payload.get("files")
        if files is not None and (not isinstance(files, list) or not files):
            raise ExecutionError("git discard 'files' must be a non-empty list, or omit it to discard everything")
        # .env is gitignored (never tracked), so a bare "." here can't touch it.
        args = ["git", "checkout", "--"] + (files if files else ["."])
        result = _run_subprocess(args, cwd=settings.repo_path)
        return {"steps": [result], "ok": result["ok"]}

    message = payload.get("message")
    push = bool(payload.get("push", False))
    if not message or not isinstance(message, str):
        raise ExecutionError("git action requires a non-empty 'message'")

    outputs = []
    outputs.append(_run_subprocess(["git", "add", "-A"], cwd=settings.repo_path))
    outputs.append(_run_subprocess(["git", "commit", "-m", message], cwd=settings.repo_path))
    if push:
        outputs.append(_run_subprocess(["git", "push"], cwd=settings.repo_path))
    return {"steps": outputs, "ok": all(o["ok"] for o in outputs)}


_KEEP_IMAGE_TAGS = 10
_ALLOWED_COMPOSE_SERVICES = {"cms", "web", "api"}
_PROD_COMPOSE_SERVICES = ["cms-prod", "web-prod", "api-prod"]
_PROD_COMPOSE_ARGS = ["-f", "docker-compose.yml", "-f", "docker-compose.prod.yml"]
_ALLOWED_STAGING_SERVICES = {"cms-prod", "web-prod", "api-prod"}
_ALLOWED_OPS = {"rebuild", "start", "stop", "restart", "rollback_to"}


def _image_ref(service: str) -> str:
    # Matches Docker Compose's own auto-derived image name for a service
    # with no explicit `image:` key: "<project>-<service>".
    return f"{settings.compose_project}-{service}"


def _prune_old_image_tags(service: str, env: str) -> None:
    """Best-effort: drop the actual image layers for builds beyond
    _KEEP_IMAGE_TAGS — deployment HISTORY (the DB rows, hence rollback
    targets) is kept forever, only the disk space is reclaimed. A failed
    `docker rmi` (shared layers, already gone) is not an error here."""
    rows = list_deployments(service=service, env=env, limit=1000)
    for row in rows[_KEEP_IMAGE_TAGS:]:
        _run_subprocess(["docker", "rmi", f"{_image_ref(service)}:{row['image_tag']}"], cwd=settings.repo_path, timeout=30)


def run_docker(payload: dict[str, Any]) -> dict[str, Any]:
    services = payload.get("services")
    env = payload.get("env", "dev")
    op = payload.get("op", "rebuild")
    if not services or not isinstance(services, list):
        raise ExecutionError("docker action requires a non-empty 'services' list")
    if env not in {"dev", "staging"}:
        raise ExecutionError("docker action requires env: 'dev' or 'staging'")
    if op not in _ALLOWED_OPS:
        raise ExecutionError(f"docker action requires op in {sorted(_ALLOWED_OPS)}")

    allowed = _ALLOWED_COMPOSE_SERVICES if env == "dev" else _ALLOWED_STAGING_SERVICES
    invalid = [s for s in services if s not in allowed]
    if invalid:
        raise ExecutionError(f"Unknown service(s) {invalid} for env '{env}'; allowed: {sorted(allowed)}")

    compose_args = [] if env == "dev" else _PROD_COMPOSE_ARGS
    base = ["docker", "compose", *compose_args, "-p", settings.compose_project]

    if op == "stop":
        result = _run_subprocess([*base, "stop", *services], cwd=settings.repo_path, timeout=120)
        return {"steps": [result], "ok": result["ok"]}
    if op == "start":
        result = _run_subprocess([*base, "start", *services], cwd=settings.repo_path, timeout=120)
        return {"steps": [result], "ok": result["ok"]}
    if op == "restart":
        result = _run_subprocess([*base, "restart", *services], cwd=settings.repo_path, timeout=180)
        return {"steps": [result], "ok": result["ok"]}

    if op == "rollback_to":
        # Redeploy a specific PAST build without rebuilding: retag that
        # image as the one compose will pick up, then recreate — fast,
        # and exact (byte-identical to what was actually running then).
        image_tag = payload.get("image_tag")
        if not image_tag or len(services) != 1:
            raise ExecutionError("rollback_to requires exactly one service and a non-empty 'image_tag'")
        # image_tag is free text from the caller and ends up in a `docker
        # tag <image>:<tag>` arg (list-form subprocess, so no shell
        # injection risk) and in the aiwebmaster_deployments git_sha
        # column — constrain it to what this app ever actually generates
        # (a short hex git SHA) rather than trusting arbitrary text either
        # place.
        if not re.fullmatch(r"[0-9a-f]{7,40}", image_tag):
            raise ExecutionError("image_tag must be a git SHA (hex, 7-40 chars) — pick one from the Rollback list, not free text")
        service = services[0]
        image = _image_ref(service)
        tag_step = _run_subprocess(["docker", "tag", f"{image}:{image_tag}", f"{image}:latest"], cwd=settings.repo_path, timeout=30)
        if not tag_step["ok"]:
            return {"steps": [tag_step], "ok": False}
        up = _run_subprocess([*base, "up", "-d", "--force-recreate", "--no-deps", service], cwd=settings.repo_path, timeout=300)
        ok = tag_step["ok"] and up["ok"]
        if ok:
            record_deployment(service=service, env=env, git_sha=image_tag, image_tag=image_tag, actor=payload.get("_actor"), kind="rollback")
        return {"steps": [tag_step, up], "ok": ok}

    # op == "rebuild" (default, matches original behavior): build then force-recreate.
    build = _run_subprocess([*base, "build", *services], cwd=settings.repo_path, timeout=1800)
    if not build["ok"]:
        return {"steps": [build], "ok": False}
    # --no-deps: only touch the services actually requested. Without it,
    # compose can decide a dependency (postgres, shared by every service)
    # needs recreating too if it re-evaluates the dependency graph during
    # a --force-recreate — observed once during testing (clean shutdown/
    # restart, no data loss, but real momentary downtime for every service
    # sharing that database). Rebuild should only ever touch what you asked
    # it to touch.
    up = _run_subprocess([*base, "up", "-d", "--force-recreate", "--no-deps", *services], cwd=settings.repo_path, timeout=300)
    ok = build["ok"] and up["ok"]
    steps = [build, up]
    if ok:
        sha, _ = git_state()
        short_sha = sha[:12] if sha else "unknown"
        for service in services:
            image = _image_ref(service)
            tag_result = _run_subprocess(["docker", "tag", f"{image}:latest", f"{image}:{short_sha}"], cwd=settings.repo_path, timeout=30)
            steps.append(tag_result)
            if tag_result["ok"]:
                record_deployment(service=service, env=env, git_sha=short_sha, image_tag=short_sha, actor=payload.get("_actor"), kind="build")
                _prune_old_image_tags(service, env)
    return {"steps": steps, "ok": ok}


_DB_URLS = {"cms": "cms_database_url", "api": "api_database_url"}
# DROP/TRUNCATE are schema-destroying and irreversible even with a WHERE-style
# qualifier (neither statement even accepts one) — blocked outright, no exception.
_ALWAYS_BLOCKED_KEYWORDS = ("DROP ", "TRUNCATE ")
# DELETE removes rows, not the schema — still destructive but reversible via a
# restore, so it's only blocked when unscoped (no WHERE clause).
_UNSCOPED_BLOCKED_KEYWORDS = ("DELETE FROM",)


def run_sql(payload: dict[str, Any]) -> dict[str, Any]:
    database = payload.get("database")
    statement = payload.get("statement")
    if database not in _DB_URLS:
        raise ExecutionError("sql action requires database: 'cms' or 'api'")
    if not statement or not isinstance(statement, str):
        raise ExecutionError("sql action requires a non-empty 'statement'")

    upper = statement.strip().upper()
    if any(upper.startswith(k) or f" {k}" in f" {upper}" for k in _ALWAYS_BLOCKED_KEYWORDS):
        raise ExecutionError(
            "Refusing to run DROP/TRUNCATE — schema-destroying statements are never allowed via "
            "the sql action, run them manually if truly intended."
        )
    # Defense in depth alongside chat's user_management proposal being fully
    # disabled (core/aiwebmaster_agent.py) — closes the side-door of asking
    # the sql action to touch the users table directly instead.
    if "AIWEBMASTER_USERS" in upper:
        raise ExecutionError(
            "Refusing to run SQL against aiwebmaster_users — account/role changes are human-only, "
            "via the Users page."
        )
    if any(upper.startswith(k) or f" {k}" in f" {upper}" for k in _UNSCOPED_BLOCKED_KEYWORDS) and " WHERE " not in upper:
        raise ExecutionError(
            "Refusing to run an unscoped DELETE (no WHERE clause). Add a WHERE clause or run it manually."
        )

    dsn = getattr(settings, _DB_URLS[database])
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(statement)
            rowcount = cur.rowcount
            rows = None
            if cur.description:
                columns = [d[0] for d in cur.description]
                rows = [dict(zip(columns, r)) for r in cur.fetchmany(50)]
        conn.commit()
        return {"ok": True, "rowcount": rowcount, "rows": rows}
    except Exception as exc:
        conn.rollback()
        raise ExecutionError(f"SQL execution failed: {exc}") from exc
    finally:
        conn.close()


def _cms_headers() -> dict[str, str]:
    return {"x-service-token": settings.cms_service_token, "content-type": "application/json"}


_CONTENT_KIND_PATH = {
    "page": "/{slug}",
    "post": "/insights/{slug}",
    "resource": "/resources/{slug}",
    "case-study": "/resources/{slug}",
}


def _verify_content_live(kind: str, slug: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Same rationale as _verify_nav_link_live — a successful CMS write is
    not the same claim as "the site shows it." Fetches the actual public
    page for this doc and checks the title (the one field guaranteed present
    across all four content kinds) appears in the HTML."""
    title = fields.get("title") or fields.get("label")
    if not title or not slug:
        return {"checked": False, "reason": "no title/slug to check for"}
    path = _CONTENT_KIND_PATH.get(kind, "/{slug}").format(slug=slug.lstrip("/"))
    try:
        resp = httpx.get(f"{settings.web_url}{path}", timeout=15)
        if resp.status_code >= 400:
            return {"checked": False, "reason": f"{path} returned {resp.status_code}"}
        return {"checked": True, "visible": title in resp.text, "path": path}
    except Exception as exc:
        return {"checked": False, "reason": str(exc)[:200]}


def call_content_agent(payload: dict[str, Any]) -> dict[str, Any]:
    kind = payload.get("kind")
    doc_id = payload.get("docId")
    fields = payload.get("fields")
    if kind not in {"page", "post", "resource", "case-study", "faq", "testimonial"}:
        raise ExecutionError(
            "content action requires kind: page|post|resource|case-study|faq|testimonial"
        )
    if not isinstance(fields, dict):
        raise ExecutionError("content action requires a 'fields' object")
    publish = payload.get("publish", True)

    if kind == "page":
        url = f"{settings.cms_url}/api/page-agent/apply"
        body = {"pageId": doc_id, "proposal": fields, "publish": publish}
    else:
        content_kind = kind
        url = f"{settings.cms_url}/api/content-agent/apply"
        body = {"kind": content_kind, "docId": doc_id, "proposal": fields, "publish": publish}

    resp = httpx.post(url, json=body, headers=_cms_headers(), timeout=30)
    if resp.status_code >= 400:
        raise ExecutionError(f"CMS content apply failed ({resp.status_code}): {resp.text[:500]}")
    result = resp.json()
    if result.get("draft"):
        # Saved as a draft on purpose — it's *correct* that it won't show live yet.
        result["live_check"] = {"checked": False, "reason": "saved as draft, not published"}
    else:
        result["live_check"] = _verify_content_live(kind, result.get("slug", ""), fields)
    return result


def run_media_upload(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetches an image from a URL (pasted/referenced in chat — there's no
    file-attach UI) and hands it to the CMS's media-agent endpoint, which
    uploads it into Payload's media collection via the Local API. Returns
    the new media doc's id so a follow-up content action can reference it
    (e.g. a testimonial's `photo` field, which no content action can set
    directly — see the note in contentAgent.ts's testimonial fieldsDescription)."""
    url = payload.get("url")
    alt = payload.get("alt")
    if not url or not alt:
        raise ExecutionError("media action requires 'url' and 'alt'")
    body = {"url": url, "alt": alt, "caption": payload.get("caption")}
    resp = httpx.post(f"{settings.cms_url}/api/media-agent/upload", json=body, headers=_cms_headers(), timeout=30)
    if resp.status_code >= 400:
        raise ExecutionError(f"Media upload failed ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


def _verify_nav_link_live(payload: dict[str, Any]) -> dict[str, Any]:
    """Best-effort check that a nav_link write actually shows up on the live
    dev homepage — a DB write succeeding is not the same claim as "the site
    changed" (confirmed for real: Footer.tsx ignored the whole navigation
    collection for months, so every nav_link write to location=footer
    silently did nothing, while still reporting success). Fetches the
    homepage HTML and checks for the label as plain text. Not authoritative
    (a page that legitimately filters/groups links could false-negative) —
    reported as a hint, not a hard failure, so a write is never rolled back
    or hidden over this check alone."""
    label = payload.get("label", "")
    removed = bool(payload.get("remove"))
    try:
        resp = httpx.get(settings.web_url, timeout=15)
        if resp.status_code >= 400:
            return {"checked": False, "reason": f"homepage returned {resp.status_code}"}
        present = label in resp.text
        visible = (not present) if removed else present
        return {"checked": True, "visible": visible}
    except Exception as exc:
        return {"checked": False, "reason": str(exc)[:200]}


def call_nav_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("label") or not payload.get("location"):
        raise ExecutionError("nav_link action requires label and location")
    if not payload.get("remove") and not payload.get("href"):
        raise ExecutionError("nav_link action requires href unless remove: true")
    url = f"{settings.cms_url}/api/nav-link/upsert"
    resp = httpx.post(url, json=payload, headers=_cms_headers(), timeout=30)
    if resp.status_code >= 400:
        raise ExecutionError(f"Nav link upsert failed ({resp.status_code}): {resp.text[:500]}")
    result = resp.json()
    result["live_check"] = _verify_nav_link_live(payload)
    return result


def run_user_management(payload: dict[str, Any]) -> dict[str, Any]:
    from auth.models import ROLES, create_user
    from auth.passwords import hash_password

    email = payload.get("email")
    role = payload.get("role")
    password = payload.get("password")
    if not email or role not in ROLES:
        raise ExecutionError(f"user_management action requires email and role in {sorted(ROLES)}")
    if not password:
        raise ExecutionError("user_management action requires a password (set one even when updating a role)")

    user = create_user(email=email, password_hash=hash_password(password), role=role)
    return {"ok": True, "user": {"id": user["id"], "email": user["email"], "role": user["role"]}}


_BACKUP_KEEP = 5


def _backups_sorted() -> list[Path]:
    d = Path(settings.backups_dir)
    d.mkdir(parents=True, exist_ok=True)
    return sorted(d.glob("prod_cms_*.sql"), key=lambda p: p.name, reverse=True)


def run_publish(payload: dict[str, Any]) -> dict[str, Any]:
    target = payload.get("target", "prod")
    if target != "prod":
        raise ExecutionError("publish action only supports target: 'prod' (v3 scope)")

    steps: list[dict[str, Any]] = []

    # Snapshot the CURRENT prod DB before overwriting it, so a bad publish
    # can be undone with the "rollback" action. Best-effort: if prod DB is
    # empty/first-ever publish, this dump still succeeds (just near-empty).
    import datetime as _dt

    backup_path = Path(settings.backups_dir) / f"prod_cms_{_dt.datetime.now(_dt.timezone.utc):%Y%m%dT%H%M%SZ}.sql"
    backup = _run_subprocess(
        ["pg_dump", "--format=plain", "--clean", "--if-exists", "--no-owner",
         "-d", settings.cms_database_url_prod, "-f", str(backup_path)],
        cwd=settings.repo_path,
        timeout=300,
    )
    steps.append({**backup, "note": "pre-publish backup of current prod DB"})
    if backup["ok"]:
        # Same PG17-client/PG16-server "SET transaction_timeout" incompatibility
        # as the main dump below — strip it here too, or "rollback" can't restore it.
        steps.append(_run_subprocess(["sed", "-i", "/transaction_timeout/d", str(backup_path)], cwd=settings.repo_path, timeout=30))
        for stale in _backups_sorted()[_BACKUP_KEEP:]:
            stale.unlink(missing_ok=True)

    # Plain-SQL dump/restore (not -Fc/pg_restore): trixie's postgresql-client
    # is v17, our Postgres server is v16 — pg_dump's custom format embeds a
    # PG17-only "SET transaction_timeout" the v16 server rejects on restore.
    # Plain SQL lets us filter that one line out before feeding it to psql.
    dump = _run_subprocess(
        ["pg_dump", "--format=plain", "--clean", "--if-exists", "--no-owner",
         "-d", settings.cms_database_url, "-f", "/tmp/aiwebmaster_cms_dump.sql"],
        cwd=settings.repo_path,
        timeout=300,
    )
    steps.append(dump)
    if not dump["ok"]:
        return {"steps": steps, "ok": False}

    filter_step = _run_subprocess(
        ["sed", "-i", "/transaction_timeout/d", "/tmp/aiwebmaster_cms_dump.sql"],
        cwd=settings.repo_path,
        timeout=30,
    )
    steps.append(filter_step)

    restore = _run_subprocess(
        ["psql", "-v", "ON_ERROR_STOP=1", "-d", settings.cms_database_url_prod,
         "-f", "/tmp/aiwebmaster_cms_dump.sql"],
        cwd=settings.repo_path,
        timeout=300,
    )
    steps.append(restore)
    if not restore["ok"]:
        return {"steps": steps, "ok": False}

    build = _run_subprocess(
        ["docker", "compose", *_PROD_COMPOSE_ARGS, "-p", settings.compose_project, "build", *_PROD_COMPOSE_SERVICES],
        cwd=settings.repo_path,
        timeout=1800,
    )
    steps.append(build)
    if not build["ok"]:
        return {"steps": steps, "ok": False}

    up = _run_subprocess(
        ["docker", "compose", *_PROD_COMPOSE_ARGS, "-p", settings.compose_project, "up", "-d", "--force-recreate", *_PROD_COMPOSE_SERVICES],
        cwd=settings.repo_path,
        timeout=300,
    )
    steps.append(up)
    if up["ok"]:
        try:
            sha, tree_hash = git_state()
            set_last_publish(sha, tree_hash)
        except Exception:
            pass  # best-effort — worst case the next diff check looks stale, not wrong-dangerous
    return {"steps": steps, "ok": up["ok"]}


def run_rollback(payload: dict[str, Any]) -> dict[str, Any]:
    """Restores the most recent pre-publish backup into tbg_cms_prod, then
    restarts cms-prod. Does not rebuild images — rollback undoes a bad
    *content* promote; a bad *code* publish needs a fresh code_edit + publish."""
    backups = _backups_sorted()
    if not backups:
        raise ExecutionError("No publish backups found — nothing to roll back to.")
    latest = backups[0]

    restore = _run_subprocess(
        ["psql", "-v", "ON_ERROR_STOP=1", "-d", settings.cms_database_url_prod, "-f", str(latest)],
        cwd=settings.repo_path,
        timeout=300,
    )
    steps = [{**restore, "note": f"restored {latest.name}"}]
    if not restore["ok"]:
        return {"steps": steps, "ok": False}

    restart = _run_subprocess(
        ["docker", "compose", *_PROD_COMPOSE_ARGS, "-p", settings.compose_project, "restart", "cms-prod"],
        cwd=settings.repo_path,
        timeout=120,
    )
    steps.append(restart)
    return {"steps": steps, "ok": restart["ok"], "restored_from": latest.name}


# Paths code_edit refuses to touch even with approval — secrets, git
# internals, and AIwebmaster's own auth/RBAC code are too dangerous for a
# chat-proposed diff to land in (the last one is defense in depth alongside
# user_management being fully unproposable by chat — core/aiwebmaster_agent.py
# — closing the side-door of asking code_edit to patch permissions.py instead).
_CODE_EDIT_BLOCKLIST_PREFIXES = (".env", ".git/", "apps/aiwebmaster/auth/")


def _resolve_repo_path(file: str) -> Path:
    repo_root = Path(settings.repo_path).resolve()
    candidate = (repo_root / file).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        raise ExecutionError(f"'{file}' resolves outside the repo — refusing.") from None
    rel = str(candidate.relative_to(repo_root))
    if any(rel == p.rstrip("/") or rel.startswith(p) for p in _CODE_EDIT_BLOCKLIST_PREFIXES):
        raise ExecutionError(f"'{file}' is on the code_edit blocklist (secrets/git internals) — refusing.")
    return candidate


def run_code_edit(payload: dict[str, Any]) -> dict[str, Any]:
    file = payload.get("file")
    mode = payload.get("mode")
    if not file or not isinstance(file, str):
        raise ExecutionError("code_edit requires 'file' (repo-relative path)")
    if mode not in {"edit", "write"}:
        raise ExecutionError("code_edit requires mode: 'edit' or 'write'")

    path = _resolve_repo_path(file)

    if mode == "write":
        content = payload.get("content")
        if content is None or not isinstance(content, str):
            raise ExecutionError("code_edit mode 'write' requires string 'content'")
        existed = path.exists()
        os.makedirs(path.parent, exist_ok=True)
        path.write_text(content)
        return {"ok": True, "file": file, "mode": "write", "created": not existed, "bytes": len(content)}

    # mode == "edit"
    old_string = payload.get("old_string")
    new_string = payload.get("new_string")
    if old_string is None or new_string is None:
        raise ExecutionError("code_edit mode 'edit' requires 'old_string' and 'new_string'")
    if not path.exists():
        raise ExecutionError(f"'{file}' does not exist — use mode 'write' to create it")

    text = path.read_text()
    count = text.count(old_string)
    if count == 0:
        raise ExecutionError(f"old_string not found in '{file}' — nothing to replace")
    if count > 1:
        raise ExecutionError(f"old_string matches {count} times in '{file}' — must match exactly once, add more context")

    path.write_text(text.replace(old_string, new_string, 1))
    return {"ok": True, "file": file, "mode": "edit"}


_CODEGEN_SANDBOXES = {"claude": "claude-agent", "codex": "codex-agent"}
# Field holding the CLI's own session id, keyed by tool — mirrors
# core/agent_stream.py's _SESSION_ID_FIELDS (kept as a separate copy here
# rather than a shared import, to avoid a circular import between the two
# modules over one three-line constant+function).
_SESSION_ID_FIELDS = {"claude": "session_id", "codex": "thread_id"}


def _extract_cli_session_id(tool: str, output: str) -> str | None:
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get(_SESSION_ID_FIELDS[tool]):
            return event[_SESSION_ID_FIELDS[tool]]
    return None


def run_codegen_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """Invokes a real coding-agent CLI (Claude Code or Codex) inside its
    isolated sandbox container (infra/claude-agent, infra/codex-agent) —
    full file-edit/bash tool access, unlike code_edit's mechanical
    old_string/new_string replace. The sandbox bind-mounts the same dev
    source directories this container's own /repo mount sees, so after it
    exits we diff the working tree here to surface what changed.

    When called from chat (payload carries _chat_session_id/_user_id, set
    by routers/actions.py from the chat request's session), this resumes
    the same Claude Code conversation across multiple codegen_agent
    proposals in one chat thread — the same $RESUME_ID mechanism Agent
    Terminal uses, via db/agent_sessions.py::get_or_create_for_chat. A
    codegen_agent action run outside chat (no session context) still works,
    just always starts a fresh conversation."""
    from core.codegen_router import route_codegen

    prompt = payload.get("prompt")
    if not prompt or not isinstance(prompt, str):
        raise ExecutionError("codegen_agent requires a non-empty 'prompt'")

    chat_session_id = payload.get("_chat_session_id")
    user_id = payload.get("_user_id")
    thread = None
    if chat_session_id and user_id:
        from db.agent_sessions import get_or_create_for_chat, set_cli_session_id, set_session_tool
        thread = get_or_create_for_chat(chat_session_id, user_id)

    hint_tool = payload.get("tool")
    if thread and thread.get("tool"):
        tool = thread["tool"]
    else:
        tool = route_codegen(prompt, hint_tool if hint_tool in _CODEGEN_SANDBOXES else None)
        if thread:
            set_session_tool(thread["id"], tool)
    service = _CODEGEN_SANDBOXES[tool]

    resume_id = (thread or {}).get("cli_session_id") or ""
    cmd = [
        "docker", "compose", "-f", f"{settings.repo_path}/docker-compose.yml",
        "--project-directory", settings.host_repo_path,
        "-p", settings.compose_project, "run", "--rm",
    ]
    if resume_id:
        cmd += ["-e", f"RESUME_ID={resume_id}"]
    cmd += [service, prompt]

    run = _run_subprocess(cmd, cwd=settings.repo_path, timeout=1800)

    if thread and run["ok"]:
        new_session_id = _extract_cli_session_id(tool, run["stdout"])
        if new_session_id and new_session_id != resume_id:
            set_cli_session_id(thread["id"], new_session_id)

    diff_stat = _run_subprocess(["git", "diff", "--stat"], cwd=settings.repo_path, timeout=30)
    diff = _run_subprocess(["git", "diff"], cwd=settings.repo_path, timeout=30)

    return {
        "ok": run["ok"],
        "tool": tool,
        "resumed": bool(resume_id),
        "sandbox_output": run["stdout"],
        "sandbox_stderr": run["stderr"],
        "diff_stat": diff_stat["stdout"],
        "diff": diff["stdout"][-8000:],
    }


EXECUTORS = {
    "git": run_git,
    "docker": run_docker,
    "sql": run_sql,
    "content": call_content_agent,
    "nav_link": call_nav_endpoint,
    "media": run_media_upload,
    "user_management": run_user_management,
    "publish": run_publish,
    "rollback": run_rollback,
    "code_edit": run_code_edit,
    "codegen_agent": run_codegen_agent,
}
