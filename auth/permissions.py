from __future__ import annotations

from typing import Any

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "docker_ops": {"docker", "git"},
    "ui_editor": {"content", "nav_link", "code_edit", "codegen_agent", "docker"},
    "infra_admin": {"docker", "git", "sql", "code_edit", "codegen_agent"},
    "super_admin": {"docker", "git", "sql", "content", "nav_link", "user_management", "publish", "rollback", "code_edit", "codegen_agent"},
}

# Roles allowed to target env:"staging" on a docker action. Everyone else
# with "docker" permission (currently just ui_editor) can only rebuild/start/
# stop/restart dev services — staging stays behind a stricter role. Checked
# separately from ROLE_PERMISSIONS because it's a payload-level restriction,
# not an action-type one.
DOCKER_STAGING_ROLES = {"docker_ops", "infra_admin", "super_admin"}


class PermissionDenied(Exception):
    pass


def require_permission(user: dict[str, Any], action_type: str) -> None:
    allowed = ROLE_PERMISSIONS.get(user["role"], set())
    if action_type not in allowed:
        raise PermissionDenied(f"Role '{user['role']}' cannot run action type '{action_type}'")


def require_docker_env(user: dict[str, Any], payload: dict[str, Any]) -> None:
    env = payload.get("env") or "dev"
    if env == "staging" and user["role"] not in DOCKER_STAGING_ROLES:
        raise PermissionDenied(f"Role '{user['role']}' can only run docker actions against dev, not staging")
