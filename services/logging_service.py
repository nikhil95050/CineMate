import logging
import sys
import threading
import time
import json
from contextlib import contextmanager
from typing import Optional, Dict, Any
from pythonjsonlogger import jsonlogger
from contextvars import ContextVar

from utils.time_utils import utc_now_iso
from config.supabase_client import insert_rows, is_configured as is_supabase_configured

# Central context for logging interaction turns (User Input -> Bot Response)
interaction_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar("interaction_context", default=None)


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)
        if not log_record.get("timestamp"):
            log_record["timestamp"] = utc_now_iso()
        if log_record.get("level"):
            log_record["level"] = log_record["level"].upper()
        else:
            log_record["level"] = record.levelname


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
    """Manages a queue of log items and flushes them in batches to Supabase."""

    def __init__(self, table_name: str, batch_size: int = 10, flush_interval: int = 5):
        self.table_name = table_name
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue = []
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._shutdown = False

    def emit(self, item: dict) -> None:
        if self._shutdown:
            return
        with self._lock:
            self._queue.append(item)
            if len(self._queue) >= self.batch_size:
                self.flush()
            elif self._timer is None:
                self._timer = threading.Timer(self.flush_interval, self.flush)
                self._timer.daemon = True
                self._timer.start()

    def flush(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            if not self._queue:
                return
            batch = list(self._queue)
            self._queue.clear()

        try:
            if is_supabase_configured():
                res, err = insert_rows(self.table_name, batch)
                if err:
                    _logger.error(
                        f"[BatchLogger] Supabase error flushing {len(batch)} items to {self.table_name}: {err}"
                    )
        except Exception as e:
            _logger.error(
                f"[BatchLogger] Exception flushing {len(batch)} items to {self.table_name}: {e}"
            )

    def shutdown(self) -> None:
        self._shutdown = True
        self.flush()


interaction_batcher = BatchLogger("user_interactions", batch_size=5, flush_interval=5)
error_batcher = BatchLogger("error_logs", batch_size=1, flush_interval=1)


class LoggingService:
    """Service for structured logging, performance profiling, and persistent stats.

    This is a trimmed version of the original Antigravity LoggingService, kept
    focused on core behavior needed for the baseline.
    """

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
            "chat_id": chat_id,
            "intent": intent,
            "step": step,
            "provider": provider,
            "latency_ms": latency_ms,
            "status": status,
            "error_type": error_type,
            **(extra or {}),
        }
        if status == "error":
            _logger.error(f"Event {intent}:{step} failed", extra=log_data)
            error_batcher.emit(
                {
                    "chat_id": str(chat_id),
                    "error_type": error_type or str(intent),
                    "error_message": f"{step}: {json.dumps(extra or {})}",
                    "workflow_step": str(step),
                    "intent": str(intent),
                    "request_id": request_id,
                    "raw_payload": json.dumps(extra or {}),
                    "timestamp": utc_now_iso(),
                }
            )
        elif isinstance(latency_ms, int) and latency_ms > 2000:
            _logger.warning(f"Event {intent}:{step} was slow", extra=log_data)
        else:
            _logger.info(f"Event {intent}:{step} processed", extra=log_data)

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
        except Exception as e:  # pragma: no cover - defensive
            status = "error"
            error_message = str(e)
            raise
        finally:
            latency = int((time.time() - start) * 1000)
            LoggingService.log_event(
                chat_id=chat_id,
                intent=intent,
                step=step,
                request_id=request_id,
                provider=provider,
                latency_ms=latency,
                status=status,
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
        interaction_batcher.emit(
            {
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
            }
        )
