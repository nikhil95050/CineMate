"""Background metrics aggregation service.

Runs on a timer to aggregate raw api_usage and user_interactions rows into
pre-computed provider_metrics and user_activity_daily tables. This keeps
the admin dashboard responsive even as raw data grows unbounded.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

logger = logging.getLogger("metrics_service")

AGGREGATION_INTERVAL = 300  # Run every 5 minutes


async def _aggregate_api_usage() -> None:
    try:
        from config.supabase_client import is_configured
        if not is_configured():
            return

        from config.supabase_client import select_rows, insert_rows
        today = date.today().isoformat()

        rows, err = select_rows(
            "api_usage",
            limit=0,
            order="timestamp.desc",
        )
        if err:
            logger.warning("[Metrics] api_usage query failed: %s", err)
            return

        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rows, err = select_rows(
            "api_usage",
            filters={"timestamp": f"gte.{since}"},
            limit=5000,
        )
        if err or not rows:
            return

        agg: dict[str, dict] = {}
        for row in rows:
            p = row.get("provider", "unknown")
            if p not in agg:
                agg[p] = {
                    "provider": p, "date": today,
                    "calls": 0, "tokens": 0,
                    "tokens_prompt": 0, "tokens_completion": 0,
                    "errors": 0, "latency_avg_ms": None, "latency_p50_ms": None,
                }
            agg[p]["calls"] += 1
            agg[p]["tokens"] += int(row.get("total_tokens") or 0)
            agg[p]["tokens_prompt"] += int(row.get("prompt_tokens") or 0)
            agg[p]["tokens_completion"] += int(row.get("completion_tokens") or 0)

        if agg:
            metrics_rows = list(agg.values())
            _, upsert_err = insert_rows(
                "provider_metrics",
                metrics_rows,
                upsert=True,
                on_conflict="provider,date",
            )
            if upsert_err:
                logger.warning("[Metrics] provider_metrics upsert failed: %s", upsert_err)

    except Exception as exc:
        logger.warning("[Metrics] _aggregate_api_usage failed: %s", exc)


async def _aggregate_user_activity() -> None:
    try:
        from config.supabase_client import is_configured
        if not is_configured():
            return

        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        from config.supabase_client import select_rows, insert_rows
        today = date.today().isoformat()

        rows, err = select_rows(
            "user_interactions",
            filters={"timestamp": f"gte.{since}"},
            limit=5000,
        )
        if err or not rows:
            return

        user_agg: dict[str, dict] = {}
        for row in rows:
            cid = row.get("chat_id", "unknown")
            intent = row.get("intent", "")
            if cid not in user_agg:
                user_agg[cid] = {
                    "chat_id": cid, "date": today,
                    "interactions": 0, "recs_received": 0,
                    "recs_liked": 0, "recs_disliked": 0, "searches": 0,
                }
            user_agg[cid]["interactions"] += 1
            if intent in ("recommend", "trending", "surprise", "more_suggestions", "movie", "search"):
                user_agg[cid]["recs_received"] += 1
            if intent == "like":
                user_agg[cid]["recs_liked"] += 1
            if intent == "dislike":
                user_agg[cid]["recs_disliked"] += 1
            if intent in ("search", "movie", "movie_search"):
                user_agg[cid]["searches"] += 1

        if user_agg:
            activity_rows = list(user_agg.values())
            _, upsert_err = insert_rows(
                "user_activity_daily",
                activity_rows,
                upsert=True,
                on_conflict="chat_id,date",
            )
            if upsert_err:
                logger.warning("[Metrics] user_activity_daily upsert failed: %s", upsert_err)

    except Exception as exc:
        logger.warning("[Metrics] _aggregate_user_activity failed: %s", exc)


async def _metrics_loop() -> None:
    """Run aggregation continuously every AGGREGATION_INTERVAL seconds."""
    while True:
        await asyncio.sleep(AGGREGATION_INTERVAL)
        try:
            await _aggregate_api_usage()
            await _aggregate_user_activity()
        except Exception as exc:
            logger.warning("[Metrics] aggregation loop error: %s", exc)


def start_metrics_aggregation() -> None:
    """Start the background metrics aggregation task."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_metrics_loop())
        logger.info("[Metrics] Background aggregation started (every %ds)", AGGREGATION_INTERVAL)
    except RuntimeError:
        logger.warning("[Metrics] No running event loop -- aggregation not started")
