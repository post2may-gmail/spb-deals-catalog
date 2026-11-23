"""Отдельный сайт каталога сделок СПб. Не связан с ботом банкротства."""
from __future__ import annotations

import logging
import traceback

logger = logging.getLogger("spb-deals.boot")

try:
    from app import create_app

    app = create_app()
except Exception:  # noqa: BLE001 — не молчаливый 500 платформы
    # Traceback только в логи процесса — никогда в HTTP-ответ.
    logger.error("application boot failed:\n%s", traceback.format_exc())

    async def app(scope, receive, send):  # type: ignore[no-redef]
        if scope["type"] != "http":
            return
        body = (
            b"Service temporarily unavailable. "
            b"Application failed to start."
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
