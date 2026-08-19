from __future__ import annotations

from typing import Any

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "docker_ops": {"docker", "git"},
    "ui_editor": {"content", "nav_link", "code_edit"},
    "infra_admin": {"docker", "git", "sql", "code_edit", "codegen_agent"},
    "super_admin": {"docker", "git", "sql", "content", "nav_link", "user_management", "publish", "rollback", "code_edit", "codegen_agent"},
}


class PermissionDenied(Exception):
    pass


def require_permission(user: dict[str, Any], action_type: str) -> None:
    allowed = ROLE_PERMISSIONS.get(user["role"], set())
    if action_type not in allowed:
        raise PermissionDenied(f"Role '{user['role']}' cannot run action type '{action_type}'")
