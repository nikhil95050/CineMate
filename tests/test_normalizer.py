"""Placeholder tests for a normalizer module.

The original file used `from .normalizer import normalize_input, detect_intent`
which is a relative import pointing to a module (tests/normalizer.py) that
does not exist anywhere in this project.

These tests are skipped until a real normalizer implementation is created.
Once `utils/normalizer.py` (or equivalent) exists, replace the import below
and remove the skip markers.

TODO: implement utils/normalizer.py with normalize_input() and detect_intent()
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Replace this import once the module exists:
# from utils.normalizer import normalize_input, detect_intent
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="utils/normalizer.py not yet implemented")
def test_normalize_input_message():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "date": 1700000000,
            "text": "/start",
            "chat": {"id": 123},
            "from": {"username": "testuser"},
        },
    }
    result = normalize_input(update)  # noqa: F821
    assert result["chat_id"] == 123
    assert result["username"] == "testuser"
    assert result["input_text"] == "/start"
    assert result["action_type"] == "message"
    assert result["message_id"] == 10
    assert result["update_id"] == 1
    assert result["sent_at"] is not None


@pytest.mark.skip(reason="utils/normalizer.py not yet implemented")
def test_normalize_input_callback():
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cbq-1",
            "data": "like_123",
            "from": {"username": "tester"},
            "message": {
                "message_id": 20,
                "date": 1700000100,
                "chat": {"id": 456},
            },
        },
    }
    result = normalize_input(update)  # noqa: F821
    assert result["chat_id"] == 456
    assert result["input_text"] == "like_123"
    assert result["action_type"] == "callback"
    assert result["callback_query_id"] == "cbq-1"
    assert result["message_id"] == 20


@pytest.mark.skip(reason="utils/normalizer.py not yet implemented")
def test_detect_intent_basic_commands():
    assert detect_intent("/start", {}) == "start"  # noqa: F821
    assert detect_intent("/help", {}) == "help"  # noqa: F821
    assert detect_intent("/movie Inception", {}) == "movie"  # noqa: F821
    assert detect_intent("/trending", {}) == "trending"  # noqa: F821
    assert detect_intent("/surprise", {}) == "surprise"  # noqa: F821


@pytest.mark.skip(reason="utils/normalizer.py not yet implemented")
def test_detect_intent_question_state():
    session = {"session_state": "questioning"}
    assert detect_intent("something freeform", session) == "questioning"  # noqa: F821


@pytest.mark.skip(reason="utils/normalizer.py not yet implemented")
def test_detect_intent_fallback():
    assert detect_intent("random text", {}) == "fallback"  # noqa: F821
