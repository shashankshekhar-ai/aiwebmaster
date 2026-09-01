from __future__ import annotations

from typing import Any

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "docker_ops": {"docker", "git"},
    # codegen_agent/docker deliberately excluded: those spend the operator's
    # own logged-in Claude subscription quota / touch running containers,
    # which is a materially different risk tier than editing content.
    "ui_editor": {"content", "nav_link", "media", "code_edit"},
    "infra_admin": {"docker", "git", "sql", "code_edit", "codegen_agent"},
    "super_admin": {"docker", "git", "sql", "content", "nav_link", "media", "user_management", "publish", "rollback", "code_edit", "codegen_agent"},
}

# Roles allowed to target env:"staging" on a docker action. super_admin only
# — staging/production is promoted exclusively through the "publish" action
# (backs up the staging DB first, see core/executors.py), which is itself
# restricted to super_admin in ROLE_PERMISSIONS above. docker_ops/infra_admin
# can rebuild/start/stop/restart dev services only; a raw docker action
# against staging would let them bypass the publish flow's backup entirely,
# so it's blocked here regardless of their "docker" permission. Checked
# separately from ROLE_PERMISSIONS because it's a payload-level restriction,
# not an action-type one.
DOCKER_STAGING_ROLES = {"super_admin"}


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
