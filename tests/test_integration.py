"""Integration/end-to-end tests for the full app pipeline."""
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def reset_environ():
    old_admin = os.environ.get("ADMIN_PASSWORD")
    os.environ["ADMIN_PASSWORD"] = "testpass_integration"
    yield
    if old_admin:
        os.environ["ADMIN_PASSWORD"] = old_admin
    else:
        os.environ.pop("ADMIN_PASSWORD", None)


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


class TestCoreEndpoints:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "readiness" in data

    def test_webhook_no_token(self, client):
        resp = client.post("/webhook/some-token", json={"update_id": 1})
        assert resp.status_code == 404

    def test_webhook_empty_body(self, client):
        resp = client.post("/webhook/some-token")
        assert resp.status_code == 404

    def test_debug_start(self, client):
        resp = client.get("/debug/start")
        assert resp.status_code == 200
        data = resp.json()
        assert "bot_token_set" in data
        assert "render_url" in data

    def test_openapi_schema(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "CineMate Bot API"
        paths = list(schema["paths"].keys())


class TestAdminIntegration:
    def test_full_admin_flow(self, client):
        login_resp = client.post("/admin/api/login", json={"password": "testpass_integration"})
        assert login_resp.status_code == 200
        sid = login_resp.cookies.get("cinemate_admin_session")

        dashboard = client.get("/admin/dashboard", cookies={"cinemate_admin_session": sid})
        assert dashboard.status_code == 200
        assert "Dashboard" in dashboard.text

        users = client.get("/admin/users", cookies={"cinemate_admin_session": sid})
        assert users.status_code == 200
        assert "Users" in users.text

        flags = client.get("/admin/flags", cookies={"cinemate_admin_session": sid})
        assert flags.status_code == 200
        assert "Feature Flags" in flags.text

        providers = client.get("/admin/providers", cookies={"cinemate_admin_session": sid})
        assert providers.status_code == 200

        errors = client.get("/admin/errors", cookies={"cinemate_admin_session": sid})
        assert errors.status_code == 200

        queue_page = client.get("/admin/queue", cookies={"cinemate_admin_session": sid})
        assert queue_page.status_code == 200

        usage = client.get("/admin/usage", cookies={"cinemate_admin_session": sid})
        assert usage.status_code == 200

        export_page = client.get("/admin/export", cookies={"cinemate_admin_session": sid})
        assert export_page.status_code == 200

        broadcast_page = client.get("/admin/broadcast", cookies={"cinemate_admin_session": sid})
        assert broadcast_page.status_code == 200

        logout = client.get("/admin/logout", cookies={"cinemate_admin_session": sid}, follow_redirects=False)
        assert logout.status_code == 302

        dashboard_after = client.get("/admin/dashboard", cookies={"cinemate_admin_session": sid},
                                      follow_redirects=False)
        assert dashboard_after.status_code == 302


class TestWebhookPipeline:
    @pytest.mark.asyncio
    async def test_normalizer_message(self):
        from handlers.normalizer import normalize_input
        update = {
            "update_id": 12345,
            "message": {
                "message_id": 100,
                "date": 1700000000,
                "chat": {"id": 987654321},
                "from": {"username": "testuser"},
                "text": "/start",
            },
        }
        result = normalize_input(update)
        assert result["chat_id"] == 987654321
        assert result["username"] == "testuser"
        assert result["input_text"] == "/start"
        assert result["action_type"] == "message"
        assert result["message_id"] == 100
        assert result["sent_at"] is not None

    @pytest.mark.asyncio
    async def test_normalizer_callback(self):
        from handlers.normalizer import normalize_input
        update = {
            "update_id": 12346,
            "callback_query": {
                "id": "cb_001",
                "data": "like_tt123",
                "message": {
                    "message_id": 101,
                    "date": 1700000001,
                    "chat": {"id": 987654321},
                },
                "from": {"username": "testuser"},
            },
        }
        result = normalize_input(update)
        assert result["chat_id"] == 987654321
        assert result["input_text"] == "like_tt123"
        assert result["action_type"] == "callback"
        assert result["callback_query_id"] == "cb_001"

    def test_detect_intent_start(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/start") == "start"
        assert detect_intent("/START") == "start"

    def test_detect_intent_commands(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/help") == "help"
        assert detect_intent("/reset") == "reset"
        assert detect_intent("/trending") == "trending"
        assert detect_intent("/surprise") == "surprise"
        assert detect_intent("/recommend") == "recommend"
        assert detect_intent("/clear_history") == "clear_history"
        assert detect_intent("/search") == "search"
        assert detect_intent("/movie") == "movie"

    def test_detect_intent_callbacks(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("watched_tt123") == "watched"
        assert detect_intent("save_tt456") == "save"
        assert detect_intent("more_like_tt789") == "more_like"
        assert detect_intent("like_tt123") == "like"
        assert detect_intent("dislike_tt456") == "dislike"
        assert detect_intent("more_suggestions") == "more_suggestions"

    def test_detect_intent_questioning(self):
        from handlers.normalizer import detect_intent
        session = {"session_state": "questioning"}
        assert detect_intent("some free text", session) == "questioning"
        assert detect_intent("q_more_recs", session) == "more_suggestions"
        assert detect_intent("q_reset", session) == "reset"

    def test_detect_intent_fallback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("random text") == "fallback"
        assert detect_intent("") == "fallback"

    def test_detect_intent_admin(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("admin_health") == "admin_health"
        assert detect_intent("/admin_stats") == "admin_stats"


class TestRequestSizeLimit:
    def test_small_request_passes(self, client):
        resp = client.post("/webhook/some-token", json={"small": "data"})
        assert resp.status_code == 404

    def test_oversized_request_rejected(self, client):
        large_data = "x" * (2 * 1024 * 1024)
        resp = client.post("/webhook/some-token", json={"data": large_data})
        assert resp.status_code == 413
        assert "too large" in resp.json()["description"].lower()
