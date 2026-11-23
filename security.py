"""Безопасность CMS: секреты, CSRF, HTML, URL, rate limit, headers."""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import time
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlparse

import bleach
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

_ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "ul",
    "ol",
    "li",
    "h2",
    "h3",
    "h4",
    "blockquote",
    "a",
    "code",
    "pre",
]
_ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

_LOGIN_WINDOW_SEC = 900
_LOGIN_MAX_FAILURES = 8

_RATE_DB_PATH = Path(
    os.getenv("RATE_LIMIT_DB_PATH")
    or (Path(tempfile.gettempdir()) / "spb_rate.db")
)
_rate_db_lock = Lock()
_rate_conn: sqlite3.Connection | None = None

_IP_LIKE = re.compile(
    r"^(?:"
    r"(?:\d{1,3}\.){3}\d{1,3}"
    r"|"
    r"[0-9a-fA-F:]+"
    r")$"
)

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _env_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def require_admin_password() -> str | None:
    """Пароль только из окружения. Без переменной админка недоступна."""
    value = (os.getenv("ADMIN_PASSWORD") or "").strip()
    return value or None


_ephemeral_session_secret: str | None = None


def require_session_secret() -> str:
    """Секрет cookie-сессии админки.

    Предпочтительно ADMIN_SESSION_SECRET / SESSION_SECRET из окружения.
    Если не задан — одноразовый секрет на процесс (каталог поднимается,
    админ-сессии не переживают рестарт). На проде задайте постоянный секрет.
    """
    global _ephemeral_session_secret
    value = (
        os.getenv("ADMIN_SESSION_SECRET")
        or os.getenv("SESSION_SECRET")
        or ""
    ).strip()
    if value:
        return value
    if _ephemeral_session_secret is None:
        _ephemeral_session_secret = secrets.token_urlsafe(48)
        logger.warning(
            "ADMIN_SESSION_SECRET не задан — используется временный секрет процесса. "
            "Задайте ADMIN_SESSION_SECRET в env для стабильной админки."
        )
    return _ephemeral_session_secret


def sessions_https_only() -> bool:
    """По умолчанию cookie только по HTTPS. Для локального HTTP: ADMIN_ALLOW_HTTP=1."""
    if _env_flag("ADMIN_ALLOW_HTTP"):
        return False
    return True


def trust_proxy_headers() -> bool:
    """Флаг намерения читать X-Forwarded-For (только вместе с TRUSTED_PROXY_IPS)."""
    return _env_flag("TRUST_PROXY")


def source_zip_enabled() -> bool:
    """Публичная выгрузка /source.zip только при ENABLE_SOURCE_ZIP=1."""
    return _env_flag("ENABLE_SOURCE_ZIP")


def admin_loopback_ok() -> bool:
    """Локальная разработка: 127.0.0.1 / testclient в allowlist допустимы."""
    return _env_flag("ADMIN_ALLOW_HTTP") or _env_flag("ADMIN_ALLOW_LOOPBACK")


def _is_shared_proxy_placeholder(ip: str) -> bool:
    """Адреса, которые за reverse-proxy часто общие для всех посетителей."""
    low = (ip or "").strip().lower()
    if not low:
        return True
    if low in {"127.0.0.1", "::1", "localhost", "testclient", "0.0.0.0", "::", "unknown"}:
        return True
    if low.startswith("127."):
        return True
    return False


def trusted_proxy_peers() -> set[str]:
    """Только точные IP. Широкие маски (*, 0.0.0.0/0) игнорируются."""
    raw = (os.getenv("TRUSTED_PROXY_IPS") or "").strip()
    if not raw:
        return set()
    peers: set[str] = set()
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        low = item.lower()
        if item == "*" or low in {"0.0.0.0/0", "::/0", "any"}:
            logger.warning("TRUSTED_PROXY_IPS: игнорируем слишком широкий peer %r", item)
            continue
        if "/" in item:
            logger.warning("TRUSTED_PROXY_IPS: CIDR не поддерживается, пропуск %r", item)
            continue
        peers.add(item)
    return peers


_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
_plaintext_bcrypt_cache: dict[str, bytes] = {}


def passwords_match(provided: str, expected: str) -> bool:
    """Медленная проверка пароля (bcrypt).

    ADMIN_PASSWORD может быть:
    - bcrypt-хешем ($2b$…), либо
    - открытым текстом (для совместимости): тогда на процесс считается
      bcrypt-хеш и сравнение идёт через checkpw — онлайн-перебор замедлен.
    """
    import bcrypt

    exp = expected or ""
    prov = (provided or "").encode("utf-8")
    try:
        if exp.startswith(_BCRYPT_PREFIXES):
            return bcrypt.checkpw(prov, exp.encode("utf-8"))
        # Plaintext из env: кэшируем bcrypt-хеш на процесс (случайная соль).
        hashed = _plaintext_bcrypt_cache.get(exp)
        if hashed is None:
            hashed = bcrypt.hashpw(exp.encode("utf-8"), bcrypt.gensalt(rounds=12))
            _plaintext_bcrypt_cache[exp] = hashed
        return bcrypt.checkpw(prov, hashed)
    except (ValueError, TypeError):
        return False


def admin_allowed_ips() -> set[str]:
    raw = (os.getenv("ADMIN_ALLOWED_IPS") or "").strip()
    if not raw:
        return set()
    allowed = {part.strip() for part in raw.split(",") if part.strip()}
    if not admin_loopback_ok():
        # На публичном хосте 127.0.0.1/::1/testclient в allowlist опасны:
        # за прокси peer часто один на всех → админка открылась бы всем.
        filtered = {a for a in allowed if not _is_shared_proxy_placeholder(a)}
        dropped = allowed - filtered
        if dropped:
            logger.warning(
                "ADMIN_ALLOWED_IPS: игнорируем небезопасные для прода адреса %s "
                "(нужен реальный клиентский IP; для локалки — ADMIN_ALLOW_HTTP=1)",
                sorted(dropped),
            )
        return filtered
    return allowed


def admin_access_allowed(ip: str, request: Request | None = None) -> bool:
    """Доступ к /admin только по ADMIN_ALLOWED_IPS.

    На проде (без ADMIN_ALLOW_HTTP / ADMIN_ALLOW_LOOPBACK) loopback и
    placeholder-IP не дают доступ: иначе за reverse-proxy все выглядят как
    127.0.0.1 и allowlist с localhost открыл бы /admin всему интернету.
    """
    allowed = admin_allowed_ips()
    if not allowed:
        return False
    if not admin_loopback_ok():
        if _is_shared_proxy_placeholder(ip):
            return False
        if request is not None and request.client and request.client.host:
            direct = request.client.host
            # Peer = loopback и client_ip не отделился через TRUST_PROXY → общий hop.
            if _is_shared_proxy_placeholder(direct) and ip == direct:
                return False
    return ip in allowed


def rotate_session(request: Request) -> None:
    """Сброс сессии после логина — защита от session fixation."""
    request.session.clear()


def client_ip(request: Request) -> str:
    """IP для admin allowlist, rate limit и lockout.

    Заголовки клиента (X-Forwarded-For / X-Real-IP) читаются только если
    TRUST_PROXY=1 и непосредственный peer входит в TRUSTED_PROXY_IPS.

    Для X-Forwarded-For берём **правый** адрес вне списка прокси — иначе
    клиент мог бы подставить allowlist-IP слева в заголовке.
    """
    direct = request.client.host if request.client and request.client.host else None
    if trust_proxy_headers():
        peers = trusted_proxy_peers()
        if not peers:
            logger.warning(
                "TRUST_PROXY=1 без TRUSTED_PROXY_IPS — proxy-заголовки игнорируются"
            )
        elif direct and direct in peers:
            forwarded = request.headers.get("x-forwarded-for") or ""
            candidates: list[str] = []
            for part in forwarded.split(","):
                cand = part.strip()
                if cand and _IP_LIKE.match(cand):
                    candidates.append(cand)
            for cand in reversed(candidates):
                if cand not in peers:
                    return cand
            # Caddy/nginx часто ставят один hop в X-Real-IP.
            real_ip = (request.headers.get("x-real-ip") or "").strip()
            if real_ip and _IP_LIKE.match(real_ip) and real_ip not in peers:
                return real_ip
    return direct or "unknown"


def _ensure_rate_db() -> sqlite3.Connection:
    global _rate_conn
    with _rate_db_lock:
        if _rate_conn is not None:
            return _rate_conn
        _RATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(_RATE_DB_PATH),
            timeout=5.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_events (
                bucket TEXT NOT NULL,
                ts REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_events_bucket_ts "
            "ON rate_events(bucket, ts)"
        )
        _rate_conn = conn
        return conn


def _bucket_count_and_add(
    bucket: str,
    *,
    window_sec: float,
    max_events: int,
    add: bool,
) -> int:
    """Скользящее окно в SQLite (общее для воркеров на одном хосте)."""
    now = time.time()
    cutoff = now - window_sec
    conn = _ensure_rate_db()
    with _rate_db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "DELETE FROM rate_events WHERE bucket = ? AND ts < ?",
                (bucket, cutoff),
            )
            row = conn.execute(
                "SELECT COUNT(*) FROM rate_events WHERE bucket = ?",
                (bucket,),
            ).fetchone()
            count = int(row[0]) if row else 0
            if add and count < max_events:
                conn.execute(
                    "INSERT INTO rate_events(bucket, ts) VALUES (?, ?)",
                    (bucket, now),
                )
                count += 1
            conn.execute("COMMIT")
            return count
        except Exception:
            conn.execute("ROLLBACK")
            raise


def login_is_blocked(ip: str) -> bool:
    count = _bucket_count_and_add(
        f"login:{ip}",
        window_sec=_LOGIN_WINDOW_SEC,
        max_events=_LOGIN_MAX_FAILURES,
        add=False,
    )
    return count >= _LOGIN_MAX_FAILURES


def register_login_failure(ip: str) -> None:
    _bucket_count_and_add(
        f"login:{ip}",
        window_sec=_LOGIN_WINDOW_SEC,
        max_events=_LOGIN_MAX_FAILURES + 1,
        add=True,
    )


def clear_login_failures(ip: str) -> None:
    conn = _ensure_rate_db()
    with _rate_db_lock:
        conn.execute("DELETE FROM rate_events WHERE bucket = ?", (f"login:{ip}",))


def rate_limit_exceeded(
    key: str,
    *,
    max_calls: int,
    window_sec: float,
) -> bool:
    """True, если лимит уже исчерпан (текущий вызов не засчитывается)."""
    count = _bucket_count_and_add(
        key,
        window_sec=window_sec,
        max_events=max_calls,
        add=False,
    )
    if count >= max_calls:
        return True
    _bucket_count_and_add(
        key,
        window_sec=window_sec,
        max_events=max_calls,
        add=True,
    )
    return False


def issue_csrf(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token or not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, token: str | None) -> bool:
    expected = request.session.get("csrf_token")
    if not expected or not token:
        return False
    return hmac.compare_digest(str(expected), str(token))


def _force_safe_links(attrs: dict[tuple[str | None, str], str], new: bool = False):
    href = attrs.get((None, "href"), "")
    if href.startswith(("http://", "https://")):
        attrs[(None, "rel")] = "noopener noreferrer nofollow"
        attrs[(None, "target")] = "_blank"
    return attrs


def sanitize_html(raw: str) -> str:
    cleaned = bleach.clean(
        raw or "",
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
    return bleach.linkify(
        cleaned,
        callbacks=[_force_safe_links],
        skip_tags=["pre", "code"],
    )


def sanitize_plain_text(raw: str, *, max_len: int = 2000) -> str:
    text = bleach.clean(raw or "", tags=[], attributes={}, strip=True)
    text = " ".join(text.split())
    if len(text) > max_len:
        return text[:max_len]
    return text


_SAFE_RELATIVE = re.compile(r"^/([A-Za-z0-9._~!$&'()*+,;=:@%/-]*)$")


def validate_href(href: str) -> tuple[str | None, str | None]:
    """Разрешены только относительные пути /… или http(s) URL."""
    value = (href or "").strip()
    if not value:
        return None, "Укажите URL"
    low = value.lower()
    if low.startswith(("javascript:", "data:", "vbscript:", "blob:")):
        return None, "Недопустимая схема URL"
    if value.startswith("//"):
        return None, "Protocol-relative URL запрещены"
    if value.startswith("/"):
        if not _SAFE_RELATIVE.match(value):
            return None, "Некорректный относительный путь"
        if ".." in value.split("/"):
            return None, "Путь не должен содержать .."
        return value, None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return None, "Разрешены только http(s) или путь от /"
    if not parsed.netloc:
        return None, "Укажите корректный хост"
    return value, None


def safe_json_ld(data: dict[str, Any] | list[Any]) -> str:
    import json

    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # Не дать закрыть <script> из пользовательских строк
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


class BlockPrivatePathsMiddleware(BaseHTTPMiddleware):
    """Не отдаём исходники и data/* по прямому URL."""

    _BLOCK_EXACT = {
        "/dockerfile",
        "/procfile",
        "/readme.md",
        "/requirements.txt",
        "/runtime.txt",
        "/pyproject.toml",
        "/main.py",
        "/app.py",
        "/security.py",
        "/admin_app.py",
        "/db.py",
        "/cms.py",
        "/import_data.py",
        "/seo.py",
        "/normalize.py",
        "/periods.py",
        "/clean.py",
        "/street_titles.py",
        "/repair_dataset.py",
        "/data/deals.db",
        "/data/deals.db.gz",
    }

    async def dispatch(self, request: Request, call_next) -> Response:
        path = (request.url.path or "/").lower()
        # Точное /data или всё под /data/ — закрыто (в т.ч. deals.db.gz).
        if path == "/data" or path.startswith("/data/") or path in self._BLOCK_EXACT:
            return Response(content="Not Found", status_code=404)
        # /source.zip только при ENABLE_SOURCE_ZIP=1 (см. маршрут).
        if path == "/source.zip":
            return await call_next(request)
        if path.endswith(
            (".py", ".db", ".gz", ".csv", ".md", ".sqlite", ".sqlite3")
        ) and not path.startswith("/static/"):
            return Response(content="Not Found", status_code=404)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """CSP / XFO / XCTO / Referrer-Policy / HSTS на всех ответах."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        # HSTS только когда cookie/сессии рассчитаны на HTTPS (не локальный HTTP).
        if sessions_https_only():
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


class HeavyRequestLimitMiddleware(BaseHTTPMiddleware):
    """Общий лимит на тяжёлые read-only маршруты (каталог, улицы, API)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_calls: int = 90,
        window_sec: float = 60.0,
    ) -> None:
        super().__init__(app)
        self.max_calls = max_calls
        self.window_sec = window_sec

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path or "/"
        if (
            path.startswith("/static/")
            or path in {"/favicon.svg", "/favicon.ico", "/robots.txt"}
            or path.startswith("/admin")
        ):
            return await call_next(request)
        heavy = (
            path.startswith("/street/")
            or path.startswith("/catalog")
            or path.startswith("/api/")
            or path == "/search"
            or path == "/sitemap.xml"
            or path.startswith("/sitemaps/")
        )
        if heavy:
            ip = client_ip(request)
            if rate_limit_exceeded(
                f"heavy:{ip}",
                max_calls=self.max_calls,
                window_sec=self.window_sec,
            ):
                return Response(
                    content="Too Many Requests",
                    status_code=429,
                    media_type="text/plain; charset=utf-8",
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)
