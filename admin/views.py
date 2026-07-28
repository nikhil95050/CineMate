"""Admin website page routes using Jinja2 templates."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

views = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="/workspace/templates")


@views.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Any:
    from admin.auth import SESSION_COOKIE, validate_session
    session_id = request.cookies.get(SESSION_COOKIE)
    if validate_session(session_id or ""):
        return RedirectResponse(url="/admin/dashboard", status_code=302)
    return templates.TemplateResponse(request, "admin/login.html")


@views.get("/logout")
async def logout(request: Request) -> Any:
    from admin.auth import SESSION_COOKIE, invalidate_session
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        invalidate_session(session_id)
    resp = RedirectResponse(url="/admin/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@views.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "admin/dashboard.html", {"page": "dashboard"})


@views.get("/users", response_class=HTMLResponse)
async def users_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "admin/users.html", {"page": "users"})


@views.get("/flags", response_class=HTMLResponse)
async def flags_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "admin/flags.html", {"page": "flags"})


@views.get("/errors", response_class=HTMLResponse)
async def errors_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "admin/errors.html", {"page": "errors"})


@views.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "admin/queue.html", {"page": "queue"})


@views.get("/usage", response_class=HTMLResponse)
async def usage_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "admin/usage.html", {"page": "usage"})


@views.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "admin/providers.html", {"page": "providers"})


@views.get("/export", response_class=HTMLResponse)
async def export_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "admin/export.html", {"page": "export"})


@views.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "admin/broadcast.html", {"page": "broadcast"})
