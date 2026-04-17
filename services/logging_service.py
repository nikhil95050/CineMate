"""Logging service.

BUG #2 FIX: Added log_api_usage() static method — writes to api_usage table.
BUG #9 FIX: log_api_usage() now guards against chat_id=None being passed
            explicitly by callers in user-request context, which would violate
            the NOT NULL constraint on api_usage.chat_id.  Any falsy value
            (None, empty string, 0) is coerced to the sentinel 'system'.
BUG #10 FIX: BatchLogger._send() retries once before silently dropping rows.
"""
import logging
import sys
import threading
import time
import json
from contextlib import contextmanager
from typing import Optional, Dict, Any, List
from pythonjsonlogger import jsonlogger
from contextvars import ContextVar

from utils.time_utils import utc_now_iso
from config.supabase_client import insert_rows, is_configured as is_supabase_configured

interaction_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar("interaction_context", default=None)


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        if not log_record.get("timestamp"):
            log_record["timestamp"] = utc_now_iso()
        log_record["level"] = str(log_record.get("level") or record.levelname).upper()


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    log_handler = logging.StreamHandler(sys.stdout)
    formatter = CustomJsonFormatter("%(timestamp)s %(level)s %(name)s %(message)s")
    log_handler.setFormatter(formatter)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    logger.addHandler(log_handler)
    return logger


_logger = setup_logging()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class BatchLogger:
    """Thread-safe batched writer to a Supabase table.

    BUG #10 FIX: _send() now retries once on failure before giving up.
    """

    def __init__(self, table_name: str, batch_size: int = 10, flush_interval: int = 5):
        self.table_name = table_name
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue: List[dict] = []
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._shutdown = False

    def _drain(self) -> List[dict]:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        batch = list(self._queue)
        self._queue.clear()
        return batch

    def _schedule_timer(self) -> None:
        if self._timer is None:
            t = threading.Timer(self.flush_interval, self._flush_from_timer)
            t.daemon = True
            t.name = f"BatchLogger-{self.table_name}-flush"
            t.start()
            self._timer = t

    def _flush_from_timer(self) -> None:
        with self._lock:
            self._timer = None
            batch = self._drain()
        self._send(batch)

    def _send(self, batch: List[dict]) -> None:
        """Insert batch into Supabase. BUG #10 FIX: one retry on transient failure."""
        if not batch:
            return
        if not is_supabase_configured():
            return
        for attempt in range(2):
            try:
                _res, err = insert_rows(self.table_name, batch)
                if err:
                    if attempt == 0:
                        time.sleep(0.5)
                        continue
                    _logger.error(
                        "[BatchLogger] Supabase error flushing %d items to %s: %s",
                        len(batch), self.table_name, err,
                    )
                return
            except Exception as exc:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                _logger.error(
                    "[BatchLogger] Exception flushing %d items to %s: %s",
                    len(batch), self.table_name, exc,
                )
                return

    def emit(self, item: dict) -> None:
        if self._shutdown:
            return
        batch_to_send: List[dict] = []
        with self._lock:
            self._queue.append(item)
            if len(self._queue) >= self.batch_size:
                batch_to_send = self._drain()
            else:
                self._schedule_timer()
        self._send(batch_to_send)

    def flush(self) -> None:
        with self._lock:
            batch = self._drain()
        self._send(batch)

    def shutdown(self) -> None:
        self._shutdown = True
        self.flush()


interaction_batcher = BatchLogger("user_interactions", batch_size=5, flush_interval=5)
error_batcher = BatchLogger("error_logs", batch_size=1, flush_interval=1)


class LoggingService:
    """Structured logging, performance profiling, and persistent stats."""

    @staticmethod
    def log_event(
        chat_id: str,
        intent: str,
        step: str,
        request_id: str = "N/A",
        provider: Optional[str] = None,
        latency_ms: Optional[int] = None,
        status: str = "success",
        error_type: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        log_data = {
            "chat_id": chat_id, "intent": intent, "step": step,
            "provider": provider, "latency_ms": latency_ms, "status": status,
            "error_type": error_type, **(extra or {}),
        }
        if status == "error":
            _logger.error(f"Event {intent}:{step} failed", extra=log_data)
            error_batcher.emit({
                "chat_id": str(chat_id),
                "error_type": error_type or str(intent),
                "error_message": f"{step}: {json.dumps(extra or {})}",
                "workflow_step": str(step),
                "intent": str(intent),
                "request_id": request_id,
                "raw_payload": json.dumps(extra or {}),
                "timestamp": utc_now_iso(),
            })
        elif isinstance(latency_ms, int) and latency_ms > 2000:
            _logger.warning(f"Event {intent}:{step} was slow", extra=log_data)
        else:
            _logger.info(f"Event {intent}:{step} processed", extra=log_data)

    @staticmethod
    def log_api_usage(
        provider: str,
        action: str,
        chat_id: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        """Write a row to api_usage after every external provider call.

        BUG #9 FIX: The api_usage.chat_id column is NOT NULL with DEFAULT
        'system'.  When callers pass chat_id=None explicitly (e.g. from
        background / system contexts), PostgREST would attempt to INSERT null,
        violating the constraint and raising a 400 error.

        Any falsy value (None, empty string, 0) is now coerced to the 'system'
        sentinel before the row is written, matching the column's DB default.
        """
        # BUG #9 FIX: coerce any falsy chat_id to the 'system' sentinel.
        safe_chat_id: str = str(chat_id) if chat_id else "system"

        try:
            from repositories.api_usage_repository import api_usage_repo
            api_usage_repo.log(
                provider=provider,
                action=action,
                chat_id=safe_chat_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        except Exception as exc:
            _logger.warning("[LoggingService] log_api_usage failed: %s", exc)

    @staticmethod
    @contextmanager
    def profile_context(label: str):
        start = time.time()
        try:
            yield
        finally:
            latency = int((time.time() - start) * 1000)
            _logger.info(f"Profile [{label}] completed in {latency}ms")

    @staticmethod
    def profile_call(chat_id, intent, step, provider, func, *args, request_id="N/A", **kwargs):
        start = time.time()
        status = "success"
        error_message = None
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            status = "error"
            error_message = str(e)
            raise
        finally:
            latency = int((time.time() - start) * 1000)
            LoggingService.log_event(
                chat_id=chat_id, intent=intent, step=step,
                request_id=request_id, provider=provider,
                latency_ms=latency, status=status,
                extra={"err": error_message} if error_message else None,
            )

    @staticmethod
    def log_interaction(
        chat_id: str,
        input_text: str,
        response_text: str,
        intent: str,
        latency_ms: int = 0,
        user_sent_at: str | None = None,
        bot_replied_at: str | None = None,
        username: str = "",
        request_id: str = "N/A",
    ) -> None:
        interaction_batcher.emit({
            "chat_id": str(chat_id),
            "username": username or "",
            "input_text": input_text[:1000] if input_text else "",
            "bot_response": response_text[:2000] if response_text else "",
            "intent": intent or "unknown",
            "latency_ms": latency_ms,
            "request_id": request_id,
            "user_sent_at": user_sent_at or utc_now_iso(),
            "bot_replied_at": bot_replied_at or utc_now_iso(),
            "timestamp": utc_now_iso(),
        })
