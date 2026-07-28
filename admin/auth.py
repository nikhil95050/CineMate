"""Admin website session-based authentication.

Uses a simple shared-secret password model. Sessions are stored in
the admin_sessions Supabase table with configurable expiry.
"""
from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse

SESSION_COOKIE = "cinemate_admin_session"
SESSION_TTL_HOURS = 12

_sessions: dict[str, datetime] = {}


def get_admin_password() -> str:
    return os.environ.get("ADMIN_PASSWORD", "").strip()


def is_configured() -> bool:
    return bool(get_admin_password())


def create_session() -> str:
    session_id = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    _sessions[session_id] = expires
    try:
        from config.supabase_client import is_configured as sb_ok, insert_rows
        if sb_ok():
            insert_rows("admin_sessions", [{
                "session_id": session_id,
                "expires_at": expires.isoformat(),
                "ip_address": None,
            }])
    except Exception:
        pass
    return session_id


def validate_session(session_id: str) -> bool:
    if not session_id:
        return False
    expires = _sessions.get(session_id)
    if expires and expires > datetime.now(timezone.utc):
        return True
    try:
        from config.supabase_client import is_configured as sb_ok, select_rows
        if sb_ok():
            rows, err = select_rows("admin_sessions", filters={"session_id": session_id}, limit=1)
            if not err and rows:
                expires_str = rows[0].get("expires_at")
                if expires_str:
                    expires = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                    if expires > datetime.now(timezone.utc):
                        _sessions[session_id] = expires
                        return True
    except Exception:
        pass
    _sessions.pop(session_id, None)
    return False


def invalidate_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


class AdminAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/admin/"):
            return await call_next(request)
        if path.startswith("/admin/api/login") or path.startswith("/admin/login"):
            return await call_next(request)
        if path.startswith("/admin/static/") or path.startswith("/admin/css"):
            return await call_next(request)

        session_id = request.cookies.get(SESSION_COOKIE)
        if not validate_session(session_id or ""):
            if path.startswith("/admin/api/"):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return RedirectResponse(url="/admin/login", status_code=302)

        return await call_next(request)
