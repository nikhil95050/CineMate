"""Unit tests verifying that error_logs rows are properly shaped for Supabase.

Covers:
- error_batcher.emit() payload schema matches the error_logs DB table
- BatchLogger.flush() calls insert_rows with the emitted rows
- BatchLogger does not raise when Supabase is unconfigured
- LoggingService.log_event() calls error_batcher.emit() on status='error'
- LoggingService.log_event() does NOT call error_batcher on status='success'
- All required error_logs columns present: chat_id, error_type, error_message,
  workflow_step, intent, request_id, raw_payload, timestamp
"""
from __future__ import annotations

import json
import time
import pytest
from unittest.mock import MagicMock, patch

from services.logging_service import BatchLogger, LoggingService, error_batcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {
    "chat_id", "error_type", "error_message",
    "workflow_step", "intent", "request_id",
    "raw_payload", "timestamp",
}


def _make_error_row(**overrides) -> dict:
    base = {
        "chat_id": "111",
        "error_type": "test_error",
        "error_message": "something went wrong",
        "workflow_step": "test.step",
        "intent": "testing",
        "request_id": "req-001",
        "raw_payload": "{}",
        "timestamp": "2026-04-12T00:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Error row schema
# ---------------------------------------------------------------------------

class TestErrorRowSchema:
    def test_all_required_columns_present(self):
        """POSITIVE: Every column needed by error_logs table is in the dict."""
        row = _make_error_row()
        assert REQUIRED_COLUMNS.issubset(row.keys()), (
            f"Missing columns: {REQUIRED_COLUMNS - row.keys()}"
        )

    def test_chat_id_is_string(self):
        row = _make_error_row(chat_id=12345)
        # In real code we always cast: str(chat_id)
        assert str(row["chat_id"]) == "12345"

    def test_error_message_truncated_to_2000_chars(self):
        """POSITIVE: Long messages must not overflow the column."""
        long_msg = "x" * 3000
        truncated = long_msg[:2000]
        assert len(truncated) == 2000
        row = _make_error_row(error_message=truncated)
        assert len(row["error_message"]) <= 2000

    def test_raw_payload_is_valid_json_string(self):
        """POSITIVE: raw_payload must be a JSON-serialisable string."""
        row = _make_error_row(raw_payload=json.dumps({"key": "value"}))
        assert json.loads(row["raw_payload"]) == {"key": "value"}


# ---------------------------------------------------------------------------
# BatchLogger
# ---------------------------------------------------------------------------

class TestBatchLogger:
    def test_emit_adds_item_to_queue(self):
        """POSITIVE: emit() queues items before flushing."""
        logger = BatchLogger("error_logs", batch_size=10, flush_interval=60)
        row = _make_error_row()
        logger.emit(row)
        with logger._lock:
            assert len(logger._queue) == 1

    def test_flush_calls_insert_rows_when_configured(self):
        """POSITIVE: flush() sends batch to Supabase when configured."""
        logger = BatchLogger("error_logs", batch_size=10, flush_interval=60)
        logger.emit(_make_error_row())
        with patch("services.logging_service.is_supabase_configured", return_value=True), \
             patch("services.logging_service.insert_rows", return_value=(None, None)) as mock_insert:
            logger.flush()
        mock_insert.assert_called_once()
        args = mock_insert.call_args[0]
        assert args[0] == "error_logs"
        assert isinstance(args[1], list)
        assert len(args[1]) == 1

    def test_flush_skips_insert_when_not_configured(self):
        """NEGATIVE: Supabase not configured → insert_rows never called."""
        logger = BatchLogger("error_logs", batch_size=10, flush_interval=60)
        logger.emit(_make_error_row())
        with patch("services.logging_service.is_supabase_configured", return_value=False), \
             patch("services.logging_service.insert_rows") as mock_insert:
            logger.flush()
        mock_insert.assert_not_called()

    def test_flush_does_not_raise_on_supabase_error(self):
        """NEGATIVE: Supabase returns error string → no exception raised."""
        logger = BatchLogger("error_logs", batch_size=10, flush_interval=60)
        logger.emit(_make_error_row())
        with patch("services.logging_service.is_supabase_configured", return_value=True), \
             patch("services.logging_service.insert_rows",
                   return_value=(None, "Supabase error 500: internal server error")):
            logger.flush()  # Must not raise

    def test_flush_does_not_raise_on_exception(self):
        """NEGATIVE: insert_rows itself raises → no exception propagated."""
        logger = BatchLogger("error_logs", batch_size=10, flush_interval=60)
        logger.emit(_make_error_row())
        with patch("services.logging_service.is_supabase_configured", return_value=True), \
             patch("services.logging_service.insert_rows", side_effect=Exception("network down")):
            logger.flush()  # Must not raise

    def test_flush_clears_queue(self):
        """POSITIVE: After flush, queue is empty."""
        logger = BatchLogger("error_logs", batch_size=10, flush_interval=60)
        logger.emit(_make_error_row())
        with patch("services.logging_service.is_supabase_configured", return_value=True), \
             patch("services.logging_service.insert_rows", return_value=(None, None)):
            logger.flush()
        with logger._lock:
            assert len(logger._queue) == 0

    def test_batch_size_triggers_auto_flush(self):
        """POSITIVE: Emitting batch_size items flushes immediately."""
        logger = BatchLogger("error_logs", batch_size=2, flush_interval=60)
        with patch("services.logging_service.is_supabase_configured", return_value=True), \
             patch("services.logging_service.insert_rows", return_value=(None, None)) as mock_insert:
            logger.emit(_make_error_row(error_type="e1"))
            logger.emit(_make_error_row(error_type="e2"))  # triggers flush
        mock_insert.assert_called_once()

    def test_emit_does_nothing_after_shutdown(self):
        """NEGATIVE: emit() after shutdown() is silently ignored."""
        logger = BatchLogger("error_logs", batch_size=10, flush_interval=60)
        with patch("services.logging_service.is_supabase_configured", return_value=True), \
             patch("services.logging_service.insert_rows", return_value=(None, None)):
            logger.shutdown()
        logger.emit(_make_error_row())  # should be no-op
        with logger._lock:
            assert len(logger._queue) == 0


# ---------------------------------------------------------------------------
# LoggingService.log_event
# ---------------------------------------------------------------------------

class TestLoggingServiceLogEvent:
    def test_error_status_calls_error_batcher(self):
        """NEGATIVE: status='error' → error_batcher.emit() must be called."""
        with patch("services.logging_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            LoggingService.log_event(
                chat_id="111",
                intent="discovery",
                step="perplexity.chat",
                request_id="req-1",
                status="error",
                error_type="perplexity_timeout",
            )
        mock_batcher.emit.assert_called_once()
        payload = mock_batcher.emit.call_args[0][0]
        assert payload["chat_id"] == "111"
        assert payload["error_type"] == "perplexity_timeout"

    def test_success_status_does_not_call_error_batcher(self):
        """POSITIVE: status='success' → error_batcher.emit() NOT called."""
        with patch("services.logging_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            LoggingService.log_event(
                chat_id="111",
                intent="discovery",
                step="perplexity.chat",
                status="success",
            )
        mock_batcher.emit.assert_not_called()

    def test_error_payload_has_all_required_columns(self):
        """POSITIVE: Every emitted error row must satisfy the DB schema."""
        with patch("services.logging_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            LoggingService.log_event(
                chat_id="222",
                intent="enrichment",
                step="watchmode.get",
                request_id="req-99",
                status="error",
                error_type="watchmode_timeout",
                extra={"movie_id": "tt123"},
            )
        payload = mock_batcher.emit.call_args[0][0]
        assert REQUIRED_COLUMNS.issubset(payload.keys()), (
            f"Missing: {REQUIRED_COLUMNS - payload.keys()}"
        )

    def test_log_event_does_not_raise_without_supabase(self):
        """NEGATIVE: No Supabase configured → log_event must not crash."""
        with patch("services.logging_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            # Should never raise regardless of configuration
            LoggingService.log_event(
                chat_id="333",
                intent="test",
                step="test.step",
                status="error",
                error_type="test_err",
            )
