"""Admin command handlers: health, stats, cache, errors, usage, provider flags."""
from __future__ import annotations

import logging
from typing import Any

from clients.telegram_helpers import send_message
from handlers.admin.decorator import admin_only

logger = logging.getLogger("admin_handlers")


# ---------------------------------------------------------------------------
# /admin_health
# ---------------------------------------------------------------------------

@admin_only
async def handle_admin_health(
    chat_id: Any,
    **kwargs,
) -> None:
    from services.container import admin_service
    try:
        result = admin_service.check_health()
    except Exception as exc:
        logger.error("[admin_health] exception: %s", exc)
        await send_message(chat_id, "\u26a0\ufe0f Health check failed internally.")
        return

    lines = ["\U0001fa7a <b>System Health</b>\n"]
    icons = {"ok": "\u2705", "not_configured": "\u26aa"}
    for service, status in result.items():
        icon = icons.get(status, "\u274c")
        lines.append(f"{icon} <b>{service}</b>: <code>{status}</code>")

    await send_message(chat_id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /admin_stats
# ---------------------------------------------------------------------------

@admin_only
async def handle_admin_stats(
    chat_id: Any,
    **kwargs,
) -> None:
    from services.container import admin_service
    try:
        stats = admin_service.get_stats()
    except Exception as exc:
        logger.error("[admin_stats] exception: %s", exc)
        await send_message(chat_id, "\u26a0\ufe0f Could not retrieve stats.")
        return

    if not stats:
        await send_message(chat_id, "\U0001f4ca No stats recorded yet.")
        return

    lines = ["\U0001f4ca <b>Bot Statistics</b>\n"]
    for metric, value in sorted(stats.items()):
        lines.append(f"\u2022 <b>{metric}</b>: {value:,}")
    await send_message(chat_id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /admin_clear_cache
# ---------------------------------------------------------------------------

@admin_only
async def handle_admin_clear_cache(
    chat_id: Any,
    **kwargs,
) -> None:
    from services.container import admin_service
    try:
        report = admin_service.clear_cache()
    except Exception as exc:
        logger.error("[admin_clear_cache] exception: %s", exc)
        await send_message(chat_id, "\u26a0\ufe0f Cache clear encountered an error.")
        return

    lines = ["\U0001f5d1 <b>Cache Cleared</b>\n"]
    for k, v in report.items():
        lines.append(f"\u2022 <b>{k}</b>: {v}")
    await send_message(chat_id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /admin_errors
# ---------------------------------------------------------------------------

@admin_only
async def handle_admin_errors(
    chat_id: Any,
    input_text: str = "",
    **kwargs,
) -> None:
    from services.container import admin_service

    limit = 10
    parts = (input_text or "").strip().split()
    if len(parts) >= 2:
        try:
            limit = min(int(parts[-1]), 50)
        except ValueError:
            pass

    try:
        errors = admin_service.get_recent_errors(limit=limit)
    except Exception as exc:
        logger.error("[admin_errors] exception: %s", exc)
        await send_message(chat_id, "\u26a0\ufe0f Could not retrieve error logs.")
        return

    if not errors:
        await send_message(chat_id, "\u2705 No recent errors found.")
        return

    lines = [f"\U0001f6a8 <b>Last {len(errors)} Errors</b>\n"]
    for e in errors:
        ts = str(e.get("timestamp", ""))[:19]
        etype = e.get("error_type") or "unknown"
        emsg = (e.get("error_message") or "")[:120]
        step = e.get("workflow_step") or ""
        cid = e.get("chat_id") or ""
        lines.append(
            f"<b>[{ts}]</b> <code>{etype}</code>"
            f"{f' | step: {step}' if step else ''}"
            f"{f' | chat: {cid}' if cid else ''}\n"
            f"  \u21b3 {emsg}\n"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3900] + "\n\u2026(truncated)"
    await send_message(chat_id, text)


# ---------------------------------------------------------------------------
# /admin_usage
# ---------------------------------------------------------------------------

@admin_only
async def handle_admin_usage(
    chat_id: Any,
    input_text: str = "",
    **kwargs,
) -> None:
    from services.container import admin_service

    hours = 24
    parts = (input_text or "").strip().split()
    if len(parts) >= 2:
        try:
            hours = int(parts[-1])
        except ValueError:
            pass

    try:
        report = admin_service.get_usage_report(hours=hours)
    except Exception as exc:
        logger.error("[admin_usage] exception: %s", exc)
        await send_message(chat_id, "\u26a0\ufe0f Could not retrieve usage report.")
        return

    lines = [f"\U0001f4c8 <b>API Usage (last {hours}h)</b>\n"]
    for p in report.get("providers", []):
        lines.append(
            f"<b>{p['provider']}</b>: {p['calls']:,} calls | "
            f"{p['total_tokens']:,} tokens | "
            f"~${p['estimated_cost_usd']:.4f}"
        )
    lines.append(
        f"\n\U0001f4b0 <b>Total estimated cost</b>: "
        f"~${report['total_estimated_cost_usd']:.4f}"
    )
    lines.append("\n\U0001f465 <b>Top Users</b>")
    for u in report.get("top_users", []):
        lines.append(f"  \u2022 <code>{u['chat_id']}</code>: {u['interactions']} interactions")

    await send_message(chat_id, "\n".join(lines))


# ---------------------------------------------------------------------------
# Provider flags
# ---------------------------------------------------------------------------

@admin_only
async def handle_admin_disable_provider(
    chat_id: Any,
    input_text: str = "",
    **kwargs,
) -> None:
    from services.container import admin_service
    parts = (input_text or "").strip().split()
    if len(parts) < 2:
        await send_message(chat_id, "Usage: <code>/admin_disable_provider perplexity</code>")
        return
    provider = parts[-1].strip()
    try:
        admin_service.disable_provider(provider)
        await send_message(chat_id, f"\U0001f534 Provider <b>{provider}</b> disabled.")
    except Exception as exc:
        logger.error("[admin_disable_provider] exception: %s", exc)
        await send_message(chat_id, f"\u26a0\ufe0f Failed to disable provider: {exc}")


@admin_only
async def handle_admin_enable_provider(
    chat_id: Any,
    input_text: str = "",
    **kwargs,
) -> None:
    from services.container import admin_service
    parts = (input_text or "").strip().split()
    if len(parts) < 2:
        await send_message(chat_id, "Usage: <code>/admin_enable_provider perplexity</code>")
        return
    provider = parts[-1].strip()
    try:
        admin_service.enable_provider(provider)
        await send_message(chat_id, f"\U0001f7e2 Provider <b>{provider}</b> enabled.")
    except Exception as exc:
        logger.error("[admin_enable_provider] exception: %s", exc)
        await send_message(chat_id, f"\u26a0\ufe0f Failed to enable provider: {exc}")
