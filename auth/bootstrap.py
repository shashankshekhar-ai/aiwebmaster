"""
Seeds one super_admin on first boot from env vars, since there's no public
signup route — someone has to exist before anyone can log in.
"""
from __future__ import annotations

import logging

from auth.models import count_users, create_user, init_users_table
from auth.passwords import hash_password
from core.config import settings

logger = logging.getLogger(__name__)


def bootstrap_admin() -> None:
    init_users_table()
    if count_users() > 0:
        return
    if not settings.aiwebmaster_admin_email or not settings.aiwebmaster_admin_password:
        logger.warning(
            "aiwebmaster_users is empty and AIWEBMASTER_ADMIN_EMAIL/PASSWORD are not set — "
            "no one can log in until a super_admin is seeded."
        )
        return
    create_user(
        email=settings.aiwebmaster_admin_email,
        password_hash=hash_password(settings.aiwebmaster_admin_password),
        role="super_admin",
    )
    logger.info("Seeded initial super_admin: %s", settings.aiwebmaster_admin_email)
