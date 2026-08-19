"""
Best-effort login-state check for the claude-agent/codex-agent codegen
sandboxes (see core/executors.py::run_codegen_agent, core/agent_stream.py).
Neither sandbox exposes a status API — this shells a one-shot `docker
compose run` per tool that just tests for the CLI's own on-disk credential
file inside its persisted home volume, mirroring the exact
-f/--project-directory/-p invocation core/executors.py already uses for the
same bind-mount-resolution reason (see HOST_REPO_PATH in core/config.py).
"""
from __future__ import annotations

import subprocess
from typing import Any

from core.config import settings

# Path *inside each sandbox container* to the file that only exists once a
# real login has happened. claude-agent: infra/claude-agent/run.sh symlinks
# ~/.claude.json -> ~/.claude/.claude.json, and the whole ~/.claude/ dir is
# the claude_agent_home volume, so this path survives container recreation.
# codex-agent: codex_agent_home mounts ~/.codex (Codex CLI's own home dir);
# auth.json is where `codex login` / device-auth persists its token.
_CREDENTIAL_PATH = {
    "claude-agent": "/home/node/.claude/.claude.json",
    "codex-agent": "/root/.codex/auth.json",
}


def _check_one(service: str) -> dict[str, Any]:
    path = _CREDENTIAL_PATH[service]
    try:
        result = subprocess.run(
            [
                "docker", "compose", "-f", f"{settings.repo_path}/docker-compose.yml",
                "--project-directory", settings.host_repo_path,
                "-p", settings.compose_project, "run", "--rm", "--entrypoint", "sh", service,
                "-c", f"test -f {path} && echo LOGGED_IN || echo NOT_LOGGED_IN",
            ],
            cwd=settings.repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = result.stdout.strip()
        if "LOGGED_IN" in out:
            return {"service": service, "logged_in": True}
        if "NOT_LOGGED_IN" in out:
            return {"service": service, "logged_in": False}
        return {"service": service, "logged_in": None, "error": (result.stderr or out)[-500:]}
    except subprocess.TimeoutExpired:
        return {"service": service, "logged_in": None, "error": "status check timed out"}
    except Exception as exc:
        return {"service": service, "logged_in": None, "error": str(exc)}


def sandbox_status() -> list[dict[str, Any]]:
    return [_check_one(service) for service in _CREDENTIAL_PATH]
