"""RQ worker entrypoint for CineMate.

Run this process alongside the FastAPI web server once the app is implemented.

Example:
  web:    uvicorn main:app --host 0.0.0.0 --port $PORT
  worker: python rq_worker.py
"""
import os

from rq import Worker, Queue, Connection

from config.redis_cache import get_redis
from services.logging_service import get_logger

logger = get_logger("rq_worker")

QUEUE_NAME = os.environ.get("CINEMATE_QUEUE_NAME", "cinemate_intent_jobs")


def main() -> None:
    redis_conn = get_redis()
    if not redis_conn:
        logger.error("Redis is not configured or reachable. Worker cannot start.")
        return

    listen = [QUEUE_NAME]
    logger.info("Starting RQ worker listening on %s", ",".join(listen))

    with Connection(redis_conn):
        worker = Worker([Queue(name) for name in listen])
        worker.work()


if __name__ == "__main__":
    main()
