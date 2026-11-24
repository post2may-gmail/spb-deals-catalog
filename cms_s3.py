"""Сохранение CMS SQLite в Timeweb S3 (у Apps нет постоянного диска)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Имена переменных, которые смотрим (первый непустой выигрывает).
_BUCKET_VARS = ("CMS_S3_BUCKET", "S3_BUCKET")
_KEY_VARS = ("CMS_S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
_SECRET_VARS = ("CMS_S3_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")
_ENDPOINT_VARS = ("CMS_S3_ENDPOINT", "AWS_ENDPOINT_URL")
_REGION_VARS = ("CMS_S3_REGION", "AWS_REGION")


def _env(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip().strip('"').strip("'")
        if value:
            return value
    return ""


def missing_s3_env() -> list[str]:
    """Какие обязательные переменные не заданы (по «семейству»)."""
    missing: list[str] = []
    if not _env(*_BUCKET_VARS):
        missing.append("CMS_S3_BUCKET")
    if not _env(*_KEY_VARS):
        missing.append("CMS_S3_ACCESS_KEY")
    if not _env(*_SECRET_VARS):
        missing.append("CMS_S3_SECRET_KEY")
    return missing


def s3_config() -> dict[str, str] | None:
    if missing_s3_env():
        return None
    return {
        "bucket": _env(*_BUCKET_VARS),
        "access_key": _env(*_KEY_VARS),
        "secret_key": _env(*_SECRET_VARS),
        "endpoint": _env(*_ENDPOINT_VARS) or "https://s3.twcstorage.ru",
        "region": _env(*_REGION_VARS) or "ru-1",
        # В корне бакета — проще увидеть в UI Timeweb.
        "object_key": _env("CMS_S3_KEY") or "cms.db",
    }


def s3_configured() -> bool:
    return s3_config() is not None


def s3_public_info() -> dict[str, object]:
    """Статус для админки (без секретов)."""
    missing = missing_s3_env()
    cfg = s3_config()
    return {
        "configured": cfg is not None,
        "missing": missing,
        "bucket": cfg["bucket"] if cfg else "",
        "endpoint": cfg["endpoint"] if cfg else "https://s3.twcstorage.ru",
        "object_key": cfg["object_key"] if cfg else "cms.db",
        "region": cfg["region"] if cfg else "ru-1",
    }


def _client(cfg: dict[str, str]):
    import boto3

    # Как в доке Timeweb — без addressing_style.
    return boto3.client(
        "s3",
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        endpoint_url=cfg["endpoint"],
        region_name=cfg["region"],
    )


def download_cms_db(target: Path) -> tuple[bool, str]:
    """Скачать cms.db из S3. (ok, message)."""
    cfg = s3_config()
    if not cfg:
        return False, "S3 не настроен: " + ", ".join(missing_s3_env())
    try:
        client = _client(cfg)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".download")
        client.download_file(cfg["bucket"], cfg["object_key"], str(tmp))
        if tmp.stat().st_size <= 0:
            tmp.unlink(missing_ok=True)
            return False, "В S3 пустой объект"
        tmp.replace(target)
        msg = (
            f"Восстановлено из s3://{cfg['bucket']}/{cfg['object_key']} "
            f"({target.stat().st_size} байт)"
        )
        logger.info(msg)
        return True, msg
    except Exception as exc:  # noqa: BLE001
        logger.warning("CMS S3 download skipped: %s", exc)
        return False, f"S3 download: {exc}"


def upload_cms_db(source: Path) -> tuple[bool, str]:
    """Залить локальный cms.db в S3. (ok, message)."""
    cfg = s3_config()
    if not cfg:
        return False, "S3 не настроен: задайте " + ", ".join(
            missing_s3_env() or ["CMS_S3_BUCKET", "CMS_S3_ACCESS_KEY", "CMS_S3_SECRET_KEY"]
        )
    if not source.exists() or source.stat().st_size <= 0:
        return False, "Локальный файл CMS пуст или отсутствует"
    try:
        client = _client(cfg)
        client.upload_file(str(source), cfg["bucket"], cfg["object_key"])
        # Проверка, что объект реально виден
        head = client.head_object(Bucket=cfg["bucket"], Key=cfg["object_key"])
        size = int(head.get("ContentLength") or source.stat().st_size)
        msg = f"Сохранено в s3://{cfg['bucket']}/{cfg['object_key']} ({size} байт)"
        logger.info(msg)
        return True, msg
    except Exception as exc:  # noqa: BLE001
        logger.exception("CMS S3 upload failed: %s", exc)
        return False, f"S3 upload ошибка: {exc}"


def probe_s3() -> tuple[bool, str]:
    """Проверить доступ: list + запись маленького объекта-маркера."""
    cfg = s3_config()
    if not cfg:
        return False, "Не заданы: " + ", ".join(missing_s3_env())
    try:
        client = _client(cfg)
        client.head_bucket(Bucket=cfg["bucket"])
        marker = "cms-probe.txt"
        client.put_object(
            Bucket=cfg["bucket"],
            Key=marker,
            Body=b"ok",
            ContentType="text/plain",
        )
        client.delete_object(Bucket=cfg["bucket"], Key=marker)
        # Есть ли уже cms.db
        try:
            head = client.head_object(Bucket=cfg["bucket"], Key=cfg["object_key"])
            size = head.get("ContentLength", 0)
            return (
                True,
                f"S3 OK: бакет «{cfg['bucket']}», объект «{cfg['object_key']}» "
                f"есть ({size} байт).",
            )
        except Exception:  # noqa: BLE001
            return (
                True,
                f"S3 OK: бакет «{cfg['bucket']}» доступен, но «{cfg['object_key']}» "
                f"ещё нет — сохраните страницу или нажмите «Залить CMS в S3».",
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("CMS S3 probe failed: %s", exc)
        return False, f"S3 недоступен: {exc}"
