"""
Status + manual trigger for infra/scripts/backup-dbs.sh (also runs nightly
via a host systemd timer, outside this app entirely — see that script's own
header comment). Surfaced here so backup health is visible without SSHing
in, and so a human can force one before a risky change.

Restore only ever operates on LOCAL backups (BACKUP_DIR — plain, unencrypted
.sql.gz files this container can already read). It deliberately never
touches the off-host encrypted copies (db-backups-repo/-prod) — those need
the age PRIVATE key, which by design never exists on this host, so this
container has no way to decrypt them even if it wanted to. Off-host restore
is a manual, human-with-the-key operation (see each backup repo's README).
"""
from __future__ import annotations

import gzip
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Request

from auth.deps import require_session
from auth.permissions import PermissionDenied, require_permission
from core.config import settings
from db.audit import log_event

router = APIRouter(dependencies=[Depends(require_session)])

BACKUP_DIR = Path("/home/admin/tbg/db-backups")
BACKUP_SCRIPT = f"{settings.repo_path}/infra/scripts/backup-dbs.sh"
RESTORE_SCRIPT = f"{settings.repo_path}/infra/scripts/restore-db.sh"
_NAME_RE = re.compile(r"^(?P<db>tbg_(?:api|cms)(?:_prod)?)_(?P<stamp>\d{8}T\d{6}Z)\.sql\.gz$")
_VALID_DBS = {"tbg_api", "tbg_cms", "tbg_api_prod", "tbg_cms_prod"}
DB_META = {
    "tbg_api": {"label": "API", "tier": "dev", "description": "FastAPI backend — leads, AI scoring, integrations, plus AIwebmaster's own accounts, sessions, and audit log."},
    "tbg_cms": {"label": "CMS", "tier": "dev", "description": "Payload CMS — site content: pages, posts, navigation, resources, case studies, FAQs, testimonials, media."},
    "tbg_api_prod": {"label": "API", "tier": "staging", "description": "Staging copy of the API database — updated only via Publish, from the dev database above."},
    "tbg_cms_prod": {"label": "CMS", "tier": "staging", "description": "Staging copy of the CMS database — updated only via Publish, from the dev database above."},
}
OFFHOST_BRANCH_FOR_DB = {"tbg_api": "dev", "tbg_cms": "dev", "tbg_api_prod": "prod", "tbg_cms_prod": "prod"}
_DB_URL_ATTR = {
    "tbg_api": "api_database_url",
    "tbg_cms": "cms_database_url",
    "tbg_api_prod": "api_database_url_prod",
    "tbg_cms_prod": "cms_database_url_prod",
}
_COPY_START_RE = re.compile(r"^COPY\s+(?:public\.)?(?P<table>\S+)\s*\(")


def _require_backup_permission(request: Request) -> None:
    # Same tier as `sql` — direct database access.
    try:
        require_permission(request.state.user, "sql")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _require_super_admin(request: Request) -> None:
    # Restore overwrites a live database — same tier as publish/rollback,
    # not just "sql" (which also allows arbitrary read/write statements,
    # but not a full drop-and-replace of an entire database).
    if request.state.user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Only super_admin can restore a database.")


def _resolve_backup_file(database: str, filename: str) -> Path:
    if database not in _VALID_DBS:
        raise HTTPException(status_code=400, detail=f"Unknown database '{database}'")
    m = _NAME_RE.match(filename)
    if not m or m.group("db") != database:
        raise HTTPException(status_code=400, detail="Invalid backup filename for this database")
    path = (BACKUP_DIR / filename).resolve()
    if path.parent != BACKUP_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Backup file not found")
    return path


def _dump_table_counts(path: Path) -> dict[str, int]:
    """Row counts per table from a plain-format pg_dump's COPY...FROM stdin
    blocks — reading the dump for real rather than trusting its filename,
    and without needing a scratch database just to answer "how many rows"."""
    counts: dict[str, int] = {}
    current: str | None = None
    n = 0
    with gzip.open(path, "rt", errors="replace") as f:
        for line in f:
            if current is None:
                m = _COPY_START_RE.match(line)
                if m and "FROM stdin" in line:
                    current = m.group("table")
                    n = 0
                continue
            if line.rstrip("\n") == "\\.":
                counts[current] = n
                current = None
                continue
            n += 1
    return counts


def _live_table_counts(dsn: str, tables: list[str]) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
    except Exception:
        return {t: None for t in tables}
    try:
        with conn.cursor() as cur:
            for t in tables:
                try:
                    cur.execute(f'SELECT count(*) FROM "{t}"')
                    counts[t] = cur.fetchone()[0]
                except Exception:
                    conn.rollback()
                    counts[t] = None
    finally:
        conn.close()
    return counts


def _offhost_last_push(branch: str) -> str | None:
    marker = BACKUP_DIR / f".last_offhost_push_{branch}"
    if not marker.is_file():
        return None
    try:
        stamp = marker.read_text().strip()  # e.g. 20260831T144509Z
        return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except (OSError, ValueError):
        return None


@router.get("/backups/status")
def backups_status(request: Request) -> dict:
    _require_backup_permission(request)
    latest: dict[str, dict] = {}
    if BACKUP_DIR.is_dir():
        for f in BACKUP_DIR.glob("*.sql.gz"):
            m = _NAME_RE.match(f.name)
            if not m:
                continue
            db = m.group("db")
            stat = f.stat()
            entry = {"file": f.name, "size_bytes": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()}
            if db not in latest or entry["modified_at"] > latest[db]["modified_at"]:
                latest[db] = entry
    expected = ["tbg_api", "tbg_cms", "tbg_api_prod", "tbg_cms_prod"]
    return {
        "backups": [
            {
                "database": db,
                **DB_META[db],
                **latest.get(db, {"file": None, "size_bytes": 0, "modified_at": None}),
                "offhost_synced_at": _offhost_last_push(OFFHOST_BRANCH_FOR_DB[db]),
            }
            for db in expected
        ]
    }


@router.post("/backups/run")
def backups_run(request: Request) -> dict:
    _require_backup_permission(request)
    try:
        result = subprocess.run(["bash", BACKUP_SCRIPT], capture_output=True, text=True, timeout=120, check=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backup script failed to run: {exc}") from exc
    ok = result.returncode == 0 and "FAILED" not in result.stdout
    return {"ok": ok, "output": (result.stdout + result.stderr)[-4000:]}


@router.get("/backups/list")
def backups_list(request: Request, database: str) -> dict:
    _require_backup_permission(request)
    if database not in _VALID_DBS:
        raise HTTPException(status_code=400, detail=f"Unknown database '{database}'")
    files = []
    if BACKUP_DIR.is_dir():
        for f in BACKUP_DIR.glob(f"{database}_*.sql.gz"):
            m = _NAME_RE.match(f.name)
            if not m or m.group("db") != database:
                continue
            stat = f.stat()
            files.append({"file": f.name, "size_bytes": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()})
    files.sort(key=lambda r: r["modified_at"], reverse=True)
    return {"database": database, "files": files}


@router.get("/backups/preview")
def backups_preview(request: Request, database: str, file: str) -> dict:
    """Per-table row-count diff between the live database and a backup
    file, read directly out of the dump — not a guess, not just a
    timestamp. Restoring a database blind ("trust the filename") is how
    people restore the wrong thing; this is the "what will actually
    change" the human sees before confirming."""
    _require_backup_permission(request)
    path = _resolve_backup_file(database, file)
    try:
        backup_counts = _dump_table_counts(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read backup file: {exc}") from exc

    dsn = getattr(settings, _DB_URL_ATTR[database])
    all_tables = sorted(set(backup_counts.keys()))
    live_counts = _live_table_counts(dsn, all_tables)

    tables = [
        {"table": t, "current_rows": live_counts.get(t), "backup_rows": backup_counts.get(t, 0)}
        for t in all_tables
    ]
    return {
        "database": database,
        "file": file,
        "tables": tables,
        "live_db_reachable": any(v is not None for v in live_counts.values()) if all_tables else None,
    }


@router.post("/backups/restore")
def backups_restore(request: Request, body: dict) -> dict:
    _require_super_admin(request)
    database = body.get("database")
    file = body.get("file")
    confirm_name = body.get("confirm_name")
    if database != confirm_name:
        raise HTTPException(status_code=400, detail="Confirmation text did not match the database name.")
    path = _resolve_backup_file(database, file)

    try:
        result = subprocess.run(
            ["bash", RESTORE_SCRIPT, database, str(path), "-y"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Restore script failed to run: {exc}") from exc
    ok = result.returncode == 0
    output = (result.stdout + result.stderr)[-4000:]
    log_event(
        event="executed",
        actor=request.state.user["email"],
        action_type="db_restore",
        action_id=f"restore-{database}-{file}",
        payload={"database": database, "file": file},
        result={"output": output},
        ok=ok,
    )
    return {"ok": ok, "output": output}
