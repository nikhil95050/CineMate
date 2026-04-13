"""admin_only: decorator that silently ignores calls from non-admin users."""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable

logger = logging.getLogger("admin_decorator")

# admin_repo is fetched dynamically inside admin_only to support test patching.


def admin_only(func: Callable) -> Callable:
    """Wrap an async handler so it silently no-ops for non-admins."""
    @functools.wraps(func)
    async def wrapper(*args, chat_id: Any = None, **kwargs):
        chat_id_str = str(chat_id) if chat_id is not None else ""
        try:
            from services.container import admin_repo
            if not admin_repo or not admin_repo.is_admin(chat_id_str):
                logger.debug(
                    "[admin_only] blocked non-admin %s from %s",
                    chat_id_str, func.__name__,
                )
                return
        except Exception as exc:
            logger.warning(
                "[admin_only] access-check exception for %s in %s: %s",
                chat_id_str, func.__name__, exc,
            )
            return
        return await func(*args, chat_id=chat_id, **kwargs)

    return wrapper