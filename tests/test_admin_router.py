"""Tests for admin router API endpoints."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_admin():
    os = __import__("os")
    os.environ["ADMIN_PASSWORD"] = "testpass"
    from main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    return client


def _login(client):
    resp = client.post("/admin/api/login", json={"password": "testpass"})
    assert resp.status_code == 200
    return resp.cookies.get("cinemate_admin_session")


class TestAdminLogin:
    def test_login_page_renders(self, client_with_admin):
        resp = client_with_admin.get("/admin/login")
        assert resp.status_code == 200
        assert "CineMate Admin" in resp.text

    def test_login_wrong_password(self, client_with_admin):
        resp = client_with_admin.post("/admin/api/login", json={"password": "wrong"})
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid password"

    def test_login_success_returns_cookie(self, client_with_admin):
        resp = client_with_admin.post("/admin/api/login", json={"password": "testpass"})
        assert resp.status_code == 200
        assert "cinemate_admin_session" in resp.cookies

    def test_redirect_to_dashboard_when_logged_in(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/login", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_logout_clears_cookie(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/logout", cookies={"cinemate_admin_session": sid}, follow_redirects=False)
        assert resp.status_code == 302


class TestAdminAPIUnauthenticated:
    def test_dashboard_redirects_to_login(self, client_with_admin):
        resp = client_with_admin.get("/admin/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["location"]

    def test_api_stats_requires_auth(self, client_with_admin):
        resp = client_with_admin.get("/admin/api/stats")
        assert resp.status_code == 401

    def test_api_flags_requires_auth(self, client_with_admin):
        resp = client_with_admin.get("/admin/api/flags")
        assert resp.status_code == 401

    def test_api_users_requires_auth(self, client_with_admin):
        resp = client_with_admin.get("/admin/api/users")
        assert resp.status_code == 401


class TestAdminAPIAuthenticated:
    def test_stats_endpoint(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/api/stats", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_users" in data
        assert "recs_today" in data
        assert "providers" in data
        assert "queue" in data

    def test_provider_status(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/api/provider/perplexity", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_flags_list(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/api/flags", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200
        data = resp.json()
        assert "flags" in data

    def test_users_list(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/api/users", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data

    def test_user_detail(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/api/users/12345", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_errors_list(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/api/errors", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_queue_stats(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/api/queue", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_usage_report(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/api/usage", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_broadcast_empty_message(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.post("/admin/api/broadcast", json={"message": ""},
                                       cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 400
        assert "message required" in resp.json()["error"]

    def test_set_flag(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.post("/admin/api/flags",
                                       json={"key": "test.flag", "value": "true"},
                                       cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_provider_action(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.post("/admin/api/provider/perplexity",
                                       json={"action": "close"},
                                       cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200


class TestAdminPageRoutes:
    def test_dashboard_page(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/dashboard", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_users_page(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/users", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_flags_page(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/flags", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_errors_page(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/errors", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_queue_page(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/queue", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_usage_page(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/usage", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_providers_page(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/providers", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_export_page(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/export", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200

    def test_broadcast_page(self, client_with_admin):
        sid = _login(client_with_admin)
        resp = client_with_admin.get("/admin/broadcast", cookies={"cinemate_admin_session": sid})
        assert resp.status_code == 200
