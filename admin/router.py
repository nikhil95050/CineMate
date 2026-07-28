"""Admin website JSON API endpoints.

All routes are prefixed with /admin/api/ and return JSON.
Page routes are in admin/views.py.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/admin/api")


@router.post("/login")
async def api_login(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    password = body.get("password", "")
    from admin.auth import get_admin_password, create_session, SESSION_COOKIE

    if not get_admin_password() or password != get_admin_password():
        return JSONResponse({"error": "invalid password"}, status_code=401)

    session_id = create_session()
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, session_id, httponly=True, max_age=43200)
    return resp


@router.get("/stats")
async def api_stats() -> Any:
    try:
        from config.supabase_client import is_configured as sb_ok, select_rows

        stats: dict[str, Any] = {}
        if sb_ok():
            rows, _ = select_rows("users", limit=0)
            stats["total_users"] = len(rows) if rows else 0
        else:
            stats["total_users"] = 0

        from services.container import admin_repo
        bot_stats = admin_repo.get_all_stats()
        stats["total_interactions"] = bot_stats.get("total_interactions", 0)
        stats["total_errors"] = bot_stats.get("total_errors", 0)

        today_recs = 0
        if sb_ok():
            from datetime import date
            today = date.today().isoformat()
            rows, _ = select_rows(
                "user_activity_daily",
                filters={"date": f"eq.{today}"},
                limit=1000,
            )
            if rows:
                today_recs = sum(int(r.get("recs_received", 0)) for r in rows)

        stats["recs_today"] = today_recs

        stats["providers"] = {}
        try:
            from services.container import health_service
            for p in ("perplexity", "tmdb", "omdb", "watchmode"):
                stats["providers"][p] = health_service.get_provider_status(p)
        except Exception:
            pass

        queue_stats = {"pending": 0, "active": 0, "failed": 0}
        try:
            from config.redis_cache import get_redis
            redis_cli = get_redis()
            if redis_cli:
                queue_name = os.environ.get("CINEMATE_QUEUE_NAME", "cinemate_intent_jobs")
                from rq import Queue
                from rq.registry import FailedJobRegistry
                q = Queue(queue_name, connection=redis_cli)
                queue_stats["pending"] = q.count
                registry = FailedJobRegistry(queue=q)
                queue_stats["failed"] = registry.count
        except Exception:
            pass
        stats["queue"] = queue_stats

        return stats

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/provider/{name}")
async def api_provider_status(name: str) -> Any:
    try:
        from services.container import health_service
        return health_service.get_provider_status(name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/provider/{name}")
async def api_provider_action(name: str, request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = body.get("action", "")

    try:
        from services.container import health_service, admin_repo

        if action == "open":
            health_service.report_failure(name)
            health_service.report_failure(name)
            health_service.report_failure(name)
        elif action == "close":
            health_service.report_success(name)
        elif action == "toggle":
            key = f"provider.{name}.enabled"
            current = admin_repo.get_config(key) or "true"
            admin_repo.set_config(key, "false" if current.lower() == "true" else "true")

        return health_service.get_provider_status(name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/flags")
async def api_flags() -> Any:
    try:
        from config.supabase_client import is_configured as sb_ok, select_rows

        flags: list[dict[str, str]] = []
        if sb_ok():
            rows, _ = select_rows("app_config", limit=100)
            flags = [{"key": r["key"], "value": r.get("value", "")} for r in (rows or [])]
        return {"flags": flags}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/flags")
async def api_set_flag(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    key = body.get("key")
    value = body.get("value")
    if not key or value is None:
        return JSONResponse({"error": "key and value required"}, status_code=400)

    try:
        from services.container import admin_repo
        admin_repo.set_config(key, str(value))
        from config.app_config import _config_cache
        _config_cache.pop(key, None)
        return {"ok": True, "key": key, "value": value}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/users")
async def api_users(search: str = "", page: int = 1) -> Any:
    try:
        from config.supabase_client import is_configured as sb_ok, select_rows

        users: list[dict[str, Any]] = []
        if sb_ok():
            rows, _ = select_rows("users", limit=100, order="updated_at.desc")
            users = list(rows or [])
            if search:
                users = [u for u in users if search.lower() in (
                    u.get("username", "") + u.get("chat_id", "")
                ).lower()]

        return {"users": users[:50], "total": len(users)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/users/{chat_id}")
async def api_user_detail(chat_id: str) -> Any:
    try:
        from config.supabase_client import is_configured as sb_ok, select_rows

        result: dict[str, Any] = {"chat_id": chat_id}

        if sb_ok():
            rows, _ = select_rows("users", filters={"chat_id": chat_id}, limit=1)
            result["user"] = rows[0] if rows else None

            rows, _ = select_rows("sessions", filters={"chat_id": chat_id}, limit=1)
            result["session"] = rows[0] if rows else None

            rows, _ = select_rows("history", filters={"chat_id": chat_id}, order="recommended_at.desc", limit=50)
            result["history"] = rows or []

            rows, _ = select_rows("feedback", filters={"chat_id": chat_id}, order="created_at.desc", limit=50)
            result["feedback"] = rows or []

            rows, _ = select_rows("watchlist", filters={"chat_id": chat_id}, limit=50)
            result["watchlist"] = rows or []

        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/users/{chat_id}/export")
async def api_user_export(chat_id: str) -> Any:
    try:
        from config.supabase_client import is_configured as sb_ok, select_rows

        export: dict[str, Any] = {"chat_id": chat_id, "exported_at": ""}
        from utils.time_utils import utc_now_iso
        export["exported_at"] = utc_now_iso()

        if sb_ok():
            tables = ["users", "sessions", "history", "watchlist", "feedback", "user_interactions"]
            for table in tables:
                rows, _ = select_rows(table, filters={"chat_id": chat_id}, limit=1000)
                export[table] = rows or []

        return JSONResponse(export, headers={
            "Content-Disposition": f"attachment; filename=cinemate_export_{chat_id}.json"
        })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/errors")
async def api_errors(error_type: str = "", chat_id: str = "", limit: int = 50) -> Any:
    try:
        from config.supabase_client import is_configured as sb_ok, select_rows

        errors: list[dict[str, Any]] = []
        if sb_ok():
            filters: dict[str, Any] = {}
            if error_type:
                filters["error_type"] = f"ilike.*{error_type}*"
            if chat_id:
                filters["chat_id"] = chat_id
            rows, _ = select_rows(
                "error_logs",
                filters=filters if filters else None,
                order="timestamp.desc",
                limit=limit,
            )
            errors = rows or []

        return {"errors": errors}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/queue")
async def api_queue() -> Any:
    try:
        from config.redis_cache import get_redis
        redis_cli = get_redis()
        if not redis_cli:
            return {"pending": 0, "active": 0, "failed": 0, "jobs": []}

        from rq import Queue
        from rq.registry import FailedJobRegistry
        queue_name = os.environ.get("CINEMATE_QUEUE_NAME", "cinemate_intent_jobs")
        q = Queue(queue_name, connection=redis_cli)
        registry = FailedJobRegistry(queue=q)

        failed_jobs = []
        for job_id in registry.get_job_ids()[:20]:
            try:
                job = q.fetch_job(job_id)
                if job:
                    failed_jobs.append({
                        "id": job_id,
                        "func": getattr(job, "func_name", "unknown"),
                        "error": str(job.exc_info) if job.exc_info else "",
                    })
            except Exception:
                pass

        return {"pending": q.count, "active": 0, "failed": len(failed_jobs), "jobs": failed_jobs}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/usage")
async def api_usage(hours: int = 24) -> Any:
    try:
        from services.container import admin_service
        return admin_service.get_usage_report(hours=hours)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/broadcast")
async def api_broadcast(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    try:
        from config.supabase_client import is_configured as sb_ok, select_rows

        users: list[str] = []
        if sb_ok():
            rows, _ = select_rows("users", limit=10000)
            users = [str(r["chat_id"]) for r in rows if r.get("chat_id")]

        from services.worker_service import run_intent_job
        import asyncio

        for chat_id in users:
            try:
                asyncio.create_task(run_intent_job(
                    intent="fallback",
                    chat_id=chat_id,
                    username="",
                    input_text=message,
                    session={},
                    user={},
                    request_id="admin_broadcast",
                ))
            except Exception:
                pass

        return {"ok": True, "sent_to": len(users)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
