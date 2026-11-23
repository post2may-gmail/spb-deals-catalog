"""Отдельный сайт каталога сделок СПб. Не связан с ботом банкротства."""
from __future__ import annotations

import logging
import traceback

logger = logging.getLogger("spb-deals.boot")

# Меняйте при каждом hotfix-деплое — по тексту 503 видно, подтянул ли Timeweb коммит.
_DEPLOY_TAG = "2026-11-23e"

_BOOT_ERROR = ""

try:
    # Не вызываем create_app на уровне app.py — только здесь.
    from app import create_app

    app = create_app()
except Exception as exc:  # noqa: BLE001
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
            f"BOOT_FAIL tag={_DEPLOY_TAG} | {detail}"
        ).encode("utf-8", errors="replace")
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
