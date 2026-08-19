"""
Executors — one function per executable action type. Every one of these is
only ever called after a human clicks Run on a specific proposed action
(routers/actions.py); nothing here runs autonomously.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import psycopg2

from core.config import settings
from core.repo_state import git_state
from db.deploy_state import set_last_publish


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


_ALLOWED_COMPOSE_SERVICES = {"cms", "web", "api"}
_PROD_COMPOSE_SERVICES = ["cms-prod", "web-prod", "api-prod"]
_PROD_COMPOSE_ARGS = ["-f", "docker-compose.yml", "-f", "docker-compose.prod.yml"]
_ALLOWED_STAGING_SERVICES = {"cms-prod", "web-prod", "api-prod"}
_ALLOWED_OPS = {"rebuild", "start", "stop", "restart"}


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
    base = ["docker", "compose", *compose_args, "-p", "rewamped-site"]

    if op == "stop":
        result = _run_subprocess([*base, "stop", *services], cwd=settings.repo_path, timeout=120)
        return {"steps": [result], "ok": result["ok"]}
    if op == "start":
        result = _run_subprocess([*base, "start", *services], cwd=settings.repo_path, timeout=120)
        return {"steps": [result], "ok": result["ok"]}
    if op == "restart":
        result = _run_subprocess([*base, "restart", *services], cwd=settings.repo_path, timeout=180)
        return {"steps": [result], "ok": result["ok"]}

    # op == "rebuild" (default, matches original behavior): build then force-recreate.
    build = _run_subprocess([*base, "build", *services], cwd=settings.repo_path, timeout=1800)
    if not build["ok"]:
        return {"steps": [build], "ok": False}
    up = _run_subprocess([*base, "up", "-d", "--force-recreate", *services], cwd=settings.repo_path, timeout=300)
    return {"steps": [build, up], "ok": build["ok"] and up["ok"]}


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


def call_content_agent(payload: dict[str, Any]) -> dict[str, Any]:
    kind = payload.get("kind")
    doc_id = payload.get("docId")
    fields = payload.get("fields")
    if kind not in {"page", "post", "resource", "case-study"}:
        raise ExecutionError("content action requires kind: page|post|resource|case-study")
    if not isinstance(fields, dict):
        raise ExecutionError("content action requires a 'fields' object")
    publish = payload.get("publish", True)

    if kind == "page":
        url = f"{settings.cms_url}/api/page-agent/apply"
        body = {"pageId": doc_id, "proposal": fields, "publish": publish}
    else:
        content_kind = {"post": "post", "resource": "resource", "case-study": "case-study"}[kind]
        url = f"{settings.cms_url}/api/content-agent/apply"
        body = {"kind": content_kind, "docId": doc_id, "proposal": fields, "publish": publish}

    resp = httpx.post(url, json=body, headers=_cms_headers(), timeout=30)
    if resp.status_code >= 400:
        raise ExecutionError(f"CMS content apply failed ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


def call_nav_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("label") or not payload.get("location"):
        raise ExecutionError("nav_link action requires label and location")
    if not payload.get("remove") and not payload.get("href"):
        raise ExecutionError("nav_link action requires href unless remove: true")
    url = f"{settings.cms_url}/api/nav-link/upsert"
    resp = httpx.post(url, json=payload, headers=_cms_headers(), timeout=30)
    if resp.status_code >= 400:
        raise ExecutionError(f"Nav link upsert failed ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


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
        ["docker", "compose", *_PROD_COMPOSE_ARGS, "-p", "rewamped-site", "build", *_PROD_COMPOSE_SERVICES],
        cwd=settings.repo_path,
        timeout=1800,
    )
    steps.append(build)
    if not build["ok"]:
        return {"steps": steps, "ok": False}

    up = _run_subprocess(
        ["docker", "compose", *_PROD_COMPOSE_ARGS, "-p", "rewamped-site", "up", "-d", "--force-recreate", *_PROD_COMPOSE_SERVICES],
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
        ["docker", "compose", *_PROD_COMPOSE_ARGS, "-p", "rewamped-site", "restart", "cms-prod"],
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


def run_codegen_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """Invokes a real coding-agent CLI (Claude Code or Codex) inside its
    isolated sandbox container (infra/claude-agent, infra/codex-agent) —
    full file-edit/bash tool access, unlike code_edit's mechanical
    old_string/new_string replace. The sandbox bind-mounts the same dev
    source directories this container's own /repo mount sees, so after it
    exits we diff the working tree here to surface what changed."""
    from core.codegen_router import route_codegen

    prompt = payload.get("prompt")
    if not prompt or not isinstance(prompt, str):
        raise ExecutionError("codegen_agent requires a non-empty 'prompt'")

    hint_tool = payload.get("tool")
    tool = route_codegen(prompt, hint_tool if hint_tool in _CODEGEN_SANDBOXES else None)
    service = _CODEGEN_SANDBOXES[tool]

    run = _run_subprocess(
        [
            "docker", "compose", "-f", f"{settings.repo_path}/docker-compose.yml",
            "--project-directory", settings.host_repo_path,
            "-p", "rewamped-site", "run", "--rm", service, prompt,
        ],
        cwd=settings.repo_path,
        timeout=1800,
    )

    diff_stat = _run_subprocess(["git", "diff", "--stat"], cwd=settings.repo_path, timeout=30)
    diff = _run_subprocess(["git", "diff"], cwd=settings.repo_path, timeout=30)

    return {
        "ok": run["ok"],
        "tool": tool,
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
    "user_management": run_user_management,
    "publish": run_publish,
    "rollback": run_rollback,
    "code_edit": run_code_edit,
    "codegen_agent": run_codegen_agent,
}
