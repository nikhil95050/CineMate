"""admin_only: decorator that silently ignores calls from non-admin users."""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable

logger = logging.getLogger("admin_decorator")


def admin_only(func: Callable) -> Callable:
    """Wrap an async handler so it silently no-ops for non-admins.

    Checks AdminRepository.is_admin(chat_id) -- never raises, never leaks
    internal errors to the caller.
    """
    @functools.wraps(func)
    async def wrapper(*args, chat_id: Any = None, **kwargs):
        chat_id_str = str(chat_id) if chat_id is not None else ""
        try:
            from services.container import admin_repo as _ar
            if not _ar.is_admin(chat_id_str):
                logger.debug(
                    "[admin_only] blocked non-admin %s from %s",
                    chat_id_str, func.__name__,
                )
                return  # silent no-op
        except Exception as exc:
            # Safety net: any repo failure -> deny access
            logger.warning(
                "[admin_only] access-check exception for %s in %s: %s",
                chat_id_str, func.__name__, exc,
            )
            return
        return await func(*args, chat_id=chat_id, **kwargs)

    return wrapper
