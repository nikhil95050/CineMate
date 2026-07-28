"""Tests for admin auth module."""
import os
import pytest
from unittest.mock import patch, MagicMock

from admin.auth import (
    create_session, validate_session, invalidate_session,
    get_admin_password, is_configured, SESSION_COOKIE,
)


class TestAdminAuthConfig:
    def test_no_password_unset(self):
        old = os.environ.pop("ADMIN_PASSWORD", None)
        try:
            assert get_admin_password() == ""
            assert not is_configured()
        finally:
            if old:
                os.environ["ADMIN_PASSWORD"] = old

    def test_password_set(self):
        os.environ["ADMIN_PASSWORD"] = "secret123"
        try:
            assert get_admin_password() == "secret123"
            assert is_configured()
        finally:
            os.environ.pop("ADMIN_PASSWORD", None)

    def test_password_whitespace_trimmed(self):
        os.environ["ADMIN_PASSWORD"] = "  mypass  "
        try:
            assert get_admin_password() == "mypass"
        finally:
            os.environ.pop("ADMIN_PASSWORD", None)


class TestAdminSessions:
    def test_create_and_validate(self):
        sid = create_session()
        assert isinstance(sid, str)
        assert len(sid) == 64

        assert validate_session(sid) is True

    def test_validate_invalid(self):
        assert validate_session("invalid-session-id") is False

    def test_validate_expired_session(self):
        from admin.auth import _sessions
        from datetime import datetime, timedelta, timezone

        sid = create_session()
        _sessions[sid] = datetime.now(timezone.utc) - timedelta(hours=24)
        assert validate_session(sid) is False

    def test_invalidate(self):
        sid = create_session()
        assert validate_session(sid) is True
        invalidate_session(sid)
        assert validate_session(sid) is False

    def test_validate_empty_string(self):
        assert validate_session("") is False

    def test_multiple_sessions(self):
        s1 = create_session()
        s2 = create_session()
        assert s1 != s2
        assert validate_session(s1) is True
        assert validate_session(s2) is True

        invalidate_session(s1)
        assert validate_session(s1) is False
        assert validate_session(s2) is True


class TestAdminAuthMiddleware:
    @pytest.mark.asyncio
    async def test_middleware_exists(self):
        from admin.auth import AdminAuthMiddleware
        from starlette.middleware.base import BaseHTTPMiddleware
        assert issubclass(AdminAuthMiddleware, BaseHTTPMiddleware)
