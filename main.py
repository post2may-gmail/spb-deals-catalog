"""Отдельный сайт каталога сделок СПб. Не связан с ботом банкротства."""
from __future__ import annotations

import logging
import traceback

logger = logging.getLogger("spb-deals.boot")

_BOOT_ERROR = ""

try:
    from app import create_app

    app = create_app()
except Exception as exc:  # noqa: BLE001 — не молчаливый 500 платформы
    # Traceback только в логи; в HTTP — короткая безопасная причина для отладки хостинга.
    logger.error("application boot failed:\n%s", traceback.format_exc())
    msg = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    if len(msg) > 240:
        msg = msg[:237] + "..."
    _BOOT_ERROR = msg

    async def app(scope, receive, send):  # type: ignore[no-redef]
        if scope["type"] != "http":
            return
        detail = _BOOT_ERROR or "unknown"
        body = (
            b"Service temporarily unavailable. "
            b"Application failed to start. "
            + detail.encode("utf-8", errors="replace")
        )
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    [b"content-type", b"text/plain; charset=utf-8"],
                    [b"cache-control", b"no-store"],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = ["app"]
