"""
Shared helpers for detecting whether dev has actually diverged from staging
— used both to record state right after a successful publish (executors.py)
and to answer "would publishing do anything?" before the human clicks the
button (routers/deploy.py).
"""
from __future__ import annotations

import hashlib
import subprocess

from core.config import settings


def _git(args: list[str], timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=settings.repo_path, capture_output=True, text=True, timeout=timeout, check=False
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def git_state() -> tuple[str, str]:
    """(HEAD sha, sha256 of `git diff HEAD`) — the pair changes whenever
    the working tree changes, committed or not."""
    sha = _git(["rev-parse", "HEAD"]).strip()
    diff = _git(["diff", "HEAD"], timeout=30)
    tree_hash = hashlib.sha256(diff.encode("utf-8", errors="replace")).hexdigest()
    return sha, tree_hash


def db_content_hash(database_url: str, *, timeout: int = 120) -> str | None:
    """sha256 of a plain-SQL dump (schema+data), so two DBs with identical
    content hash equal regardless of dump timestamp/ordering noise being
    the only difference is not fully guaranteed by pg_dump, but is stable
    dump-to-dump for an unchanged DB, which is all this needs."""
    try:
        result = subprocess.run(
            ["pg_dump", "--format=plain", "--no-owner", "-d", database_url],
            cwd=settings.repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return None
        return hashlib.sha256(result.stdout.encode("utf-8", errors="replace")).hexdigest()
    except Exception:
        return None
