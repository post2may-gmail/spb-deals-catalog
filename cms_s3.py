"""Сохранение CMS SQLite в Timeweb S3 (у Apps нет постоянного диска)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _env(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def s3_config() -> dict[str, str] | None:
    bucket = _env("CMS_S3_BUCKET", "S3_BUCKET")
    key_id = _env("CMS_S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
    secret = _env("CMS_S3_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")
    if not (bucket and key_id and secret):
        return None
    return {
        "bucket": bucket,
        "access_key": key_id,
        "secret_key": secret,
        "endpoint": _env("CMS_S3_ENDPOINT", "AWS_ENDPOINT_URL")
        or "https://s3.twcstorage.ru",
        "region": _env("CMS_S3_REGION", "AWS_REGION") or "ru-1",
        "object_key": _env("CMS_S3_KEY") or "spb-deals/cms.db",
    }


def s3_configured() -> bool:
    return s3_config() is not None


def _client(cfg: dict[str, str]):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        endpoint_url=cfg["endpoint"],
        region_name=cfg["region"],
        config=Config(s3={"addressing_style": "path"}),
    )


def download_cms_db(target: Path) -> bool:
    """Скачать cms.db из S3 в target. True если файл получен."""
    cfg = s3_config()
    if not cfg:
        return False
    try:
        client = _client(cfg)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".download")
        client.download_file(cfg["bucket"], cfg["object_key"], str(tmp))
        if tmp.stat().st_size <= 0:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(target)
        logger.info(
            "CMS восстановлена из s3://%s/%s (%s bytes)",
            cfg["bucket"],
            cfg["object_key"],
            target.stat().st_size,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — нет бэкапа = ок на первом запуске
        logger.warning("CMS S3 download skipped: %s", exc)
        return False


def upload_cms_db(source: Path) -> bool:
    """Залить локальный cms.db в S3. True при успехе."""
    cfg = s3_config()
    if not cfg:
        return False
    if not source.exists() or source.stat().st_size <= 0:
        return False
    try:
        client = _client(cfg)
        client.upload_file(str(source), cfg["bucket"], cfg["object_key"])
        logger.info(
            "CMS сохранена в s3://%s/%s (%s bytes)",
            cfg["bucket"],
            cfg["object_key"],
            source.stat().st_size,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("CMS S3 upload failed: %s", exc)
        return False
