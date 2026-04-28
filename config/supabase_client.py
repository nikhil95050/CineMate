"""Supabase REST client.

M-4 FIX: _async_client and _sync_client are no longer created at module-import
time.  They are now returned by lazy getter functions (_get_async_client /
_get_sync_client).  This prevents connection-pool sharing when the process is
forked (e.g. gunicorn pre-fork workers), which caused random connection errors
in production.
"""
import json
import os
import threading
import httpx
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_API_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.environ.get("SUPABASE_API_KEY", "").strip()
)
REST_BASE = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else ""

# ---------------------------------------------------------------------------
# M-4 FIX: lazy client singletons (one per process, created on first use)
# ---------------------------------------------------------------------------
_async_client_instance: Optional[httpx.AsyncClient] = None
_sync_client_instance: Optional[httpx.Client] = None
_client_lock = threading.Lock()


def _get_async_client() -> httpx.AsyncClient:
    """Return the process-local async httpx client, creating it on first call."""
    global _async_client_instance
    if _async_client_instance is None:
        with _client_lock:
            if _async_client_instance is None:
                _async_client_instance = httpx.AsyncClient(
                    timeout=httpx.Timeout(15.0, connect=5.0),
                    limits=httpx.Limits(
                        max_connections=50, max_keepalive_connections=20
                    ),
                )
    return _async_client_instance


def _get_sync_client() -> httpx.Client:
    """Return the process-local sync httpx client, creating it on first call."""
    global _sync_client_instance
    if _sync_client_instance is None:
        with _client_lock:
            if _sync_client_instance is None:
                _sync_client_instance = httpx.Client(timeout=15.0)
    return _sync_client_instance


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_API_KEY)


def _headers(prefer: Optional[str] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "apikey": SUPABASE_API_KEY,
        "Authorization": f"Bearer {SUPABASE_API_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _build_url_and_headers(path: str, prefer: Optional[str] = None) -> Tuple[str, Dict[str, str]]:
    return f"{REST_BASE}/{path.lstrip('/')}", _headers(prefer)


def _parse_response(resp: httpx.Response) -> Tuple[Optional[Any], Optional[str]]:
    if 200 <= resp.status_code < 300:
        return (resp.json() if resp.text.strip() else None), None
    return None, f"Supabase error {resp.status_code}: {resp.text[:500]}"


async def _request_async(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    prefer: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    if not is_configured():
        return None, "supabase_not_configured"
    url, headers = _build_url_and_headers(path, prefer)
    try:
        resp = await _get_async_client().request(
            method, url, headers=headers, params=params, json=json_body
        )
        return _parse_response(resp)
    except Exception as e:  # pragma: no cover - defensive
        return None, f"Supabase network error: {str(e)}"


def _request_sync(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    prefer: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    if not is_configured():
        return None, "supabase_not_configured"
    url, headers = _build_url_and_headers(path, prefer)
    try:
        resp = _get_sync_client().request(
            method, url, headers=headers, params=params, json=json_body
        )
        return _parse_response(resp)
    except Exception as e:  # pragma: no cover - defensive
        return None, f"Supabase network error: {str(e)}"


def _format_filter(value: Any) -> str:
    val_str = str(value)
    ops = ("eq.", "gt.", "gte.", "lt.", "lte.", "neq.", "in.", "is.")
    if any(val_str.startswith(op) for op in ops):
        return val_str
    return f"eq.{value}"


# ---------------------------------------------------------------------------
# Async read/write helpers
# ---------------------------------------------------------------------------

async def select_rows_async(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    order: Optional[str] = None,
    offset: Optional[int] = None,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    params: Dict[str, Any] = {}
    if filters:
        for key, value in filters.items():
            params[key] = _format_filter(value)
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if order:
        params["order"] = order
    return await _request_async("GET", table, params=params)


async def insert_rows_async(
    table: str,
    rows: List[Dict[str, Any]],
    upsert: bool = False,
    on_conflict: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    prefer = (
        "resolution=merge-duplicates,return=representation"
        if upsert
        else "return=representation"
    )
    params = {"on_conflict": on_conflict} if on_conflict else None
    return await _request_async("POST", table, params=params, json_body=rows, prefer=prefer)


async def update_rows_async(
    table: str,
    patch: Dict[str, Any],
    filters: Dict[str, Any],
) -> Tuple[Optional[Any], Optional[str]]:
    params = {key: _format_filter(value) for key, value in (filters or {}).items()}
    return await _request_async(
        "PATCH",
        table,
        params=params,
        json_body=patch,
        prefer="return=representation",
    )


async def delete_rows_async(
    table: str,
    filters: Dict[str, Any],
) -> Tuple[Optional[Any], Optional[str]]:
    params = {key: _format_filter(value) for key, value in (filters or {}).items()}
    return await _request_async(
        "DELETE", table, params=params, prefer="return=representation"
    )


# ---------------------------------------------------------------------------
# Sync read/write helpers (kept for background/non-async callers)
# ---------------------------------------------------------------------------

def select_rows(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    order: Optional[str] = None,
    offset: Optional[int] = None,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    params: Dict[str, Any] = {}
    if filters:
        for key, value in filters.items():
            params[key] = _format_filter(value)
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if order:
        params["order"] = order
    return _request_sync("GET", table, params=params)


def insert_rows(
    table: str,
    rows: List[Dict[str, Any]],
    upsert: bool = False,
    on_conflict: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    prefer = (
        "resolution=merge-duplicates,return=representation"
        if upsert
        else "return=representation"
    )
    params = {"on_conflict": on_conflict} if on_conflict else None
    return _request_sync("POST", table, params=params, json_body=rows, prefer=prefer)


def upsert_rows(
    table: str,
    rows: List[Dict[str, Any]],
    on_conflict: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Convenience wrapper: insert with upsert=True."""
    return insert_rows(table, rows, upsert=True, on_conflict=on_conflict)


def update_rows(
    table: str, patch: Dict[str, Any], filters: Dict[str, Any]
) -> Tuple[Optional[Any], Optional[str]]:
    params = {key: _format_filter(value) for key, value in (filters or {}).items()}
    return _request_sync(
        "PATCH",
        table,
        params=params,
        json_body=patch,
        prefer="return=representation",
    )


def delete_rows(
    table: str, filters: Dict[str, Any]
) -> Tuple[Optional[Any], Optional[str]]:
    params = {key: _format_filter(value) for key, value in (filters or {}).items()}
    return _request_sync(
        "DELETE", table, params=params, prefer="return=representation"
    )


# ---------------------------------------------------------------------------
# H-2 FIX: efficient row count using PostgREST Prefer: count=exact
# ---------------------------------------------------------------------------

def count_rows(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
) -> int:
    """Return the number of rows matching *filters* without fetching data.

    Uses PostgREST's ``Prefer: count=exact`` header with ``limit=0`` so
    only the ``Content-Range`` header is inspected — no row payload is
    transferred or parsed.  Falls back to ``len(select_rows(...))`` on
    any failure.
    """
    if not is_configured():
        return 0
    params: Dict[str, Any] = {"limit": 0}
    if filters:
        for key, value in filters.items():
            params[key] = _format_filter(value)
    url, headers = _build_url_and_headers(table, prefer="count=exact")
    try:
        resp = _get_sync_client().request(
            "HEAD", url, headers=headers, params=params,
        )
        # PostgREST returns Content-Range: 0-0/N or */N
        cr = resp.headers.get("content-range", "")
        if "/" in cr:
            total_str = cr.rsplit("/", 1)[-1]
            if total_str != "*":
                return int(total_str)
    except Exception:
        pass
    # Fallback: fetch all rows and count (original behaviour)
    try:
        rows, err = select_rows(table, filters=filters)
        if not err and rows is not None:
            return len(rows)
    except Exception:
        pass
    return 0


def is_available() -> bool:
    try:
        data, error = select_rows("sessions", limit=0)
        return error is None
    except Exception:  # pragma: no cover - defensive
        return False

