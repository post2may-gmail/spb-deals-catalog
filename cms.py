"""CMS: тексты сайта, страницы и навигационные ссылки."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

from cms_s3 import (
    download_cms_db,
    probe_s3,
    s3_configured,
    s3_public_info,
    upload_cms_db,
)
from security import admin_base_path, sanitize_html, sanitize_plain_text, validate_href
from seo import BRAND, DEFAULT_DESCRIPTION

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
PACKAGED_CMS = DATA_DIR / "cms.db"


def _default_cms_path() -> Path:
    """Пишем в CMS_DB_PATH, иначе в /data (том Timeweb), иначе /tmp."""
    env = (os.getenv("CMS_DB_PATH") or "").strip()
    if env:
        return Path(env)
    for candidate in (Path("/data/spb_cms.db"), Path("/var/data/spb_cms.db")):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            probe = candidate.parent / ".cms_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "spb_cms.db"


CMS_PATH = _default_cms_path()

_lock = threading.Lock()
_ready = False

TEXT_FIELDS: list[dict[str, str]] = [
    {
        "key": "brand",
        "label": "Название сайта (шапка и главный заголовок)",
        "default": BRAND,
    },
    {
        "key": "default_description",
        "label": "Описание сайта (meta description по умолчанию)",
        "default": DEFAULT_DESCRIPTION,
    },
    {
        "key": "hero_kicker",
        "label": "Подзаголовок над названием на главной",
        "default": "По договорам купли-продажи с 2024 года",
    },
    {
        "key": "hero_lead",
        "label": "Короткий текст под названием на главной",
        "default": (
            "По данным Росреестра по сделкам купли-продажи жилых помещений "
            "(квартиры, комнаты, доли). Поиск цен по названиям улиц и "
            "алфавитному указателю улиц"
        ),
    },
    {
        "key": "search_placeholder",
        "label": "Подсказка в поле поиска улицы",
        "default": "Например, Невский",
    },
    {
        "key": "catalog_section_title",
        "label": "Заголовок блока «каталог» на главной",
        "default": "Каталог по алфавиту",
    },
    {
        "key": "catalog_section_lead",
        "label": "Текст под заголовком каталога на главной",
        "default": "Выберите букву — откроется список улиц, затем таблица сделок.",
    },
    {
        "key": "footer_note",
        "label": "Текст в подвале (после статистики)",
        "default": "данные Росреестра",
    },
]

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or CMS_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# Старые дефолты → новые (на уже задеплоенном cms.db).
_TEXT_MIGRATIONS: dict[str, tuple[str, ...]] = {
    "hero_kicker": ("Росреестр · Санкт-Петербург",),
    "hero_lead": (
        "Квартиры, комнаты и доли — по улицам города, по данным Росреестра.",
    ),
}

_ABOUT_SEED_VERSION = "2026-11-25"
_ABOUT_SLUG = "o-proekte"
_ABOUT_TITLE = "О проекте"
_ABOUT_META = (
    "Петербургский агент Виталий Дихтяренко — о каталоге цен сделок "
    "Росреестра по квартирам в Санкт-Петербурге."
)
_ABOUT_BODY = """
<p>Здравствуйте!</p>
<p>Я петербургский агент по недвижимости Виталий Дихтяренко.</p>
<p>И мне, как и многим собственникам, покупателям и агентам, интересно знать, сколько стоит та или иная квартира в Петербурге.</p>
<p>И мне мало видеть цены в объявлениях и калькуляторы оценки на порталах недвижимости.</p>
<p>Мне хочется знать, а почём в реальности была продана квартира.</p>
<p>Эту цену обычно указывают в договоре купли-продажи и отправляют договор на государственную регистрацию в Росреестр.</p>
<p>И с недавних пор Росреестр выложил в открытый доступ дата-сеты с ценами сделок, но ими неудобно пользоваться.</p>
<p>Поэтому я с помощью вездесущего искусственного интеллекта обработал эти таблицы, вычленив ту информацию, которая касается жилых помещений, проданных в Петербурге, и опубликовал здесь. Доступна информация о сделках начиная с 1&nbsp;квартала 2024&nbsp;года.</p>
<p>Да, здесь и цены сделок на квартиры, и на доли. Но у вас теперь есть дополнительный инструмент для оценки и торга, особенно если вы изучаете сделки на тех улицах, которые вы хорошо знаете. Ведь помимо названий улиц у вас будут дополнительные ориентиры, а именно год постройки дома и материал дома.</p>
<p>Пользу от этого сервиса уже оценили мои клиенты и коллеги, когда я выложил в сентябре телеграм-бот <a href="https://t.me/FlatpricesSpbbot" rel="noopener noreferrer" target="_blank">@FlatpricesSpbbot</a> с ценами на сделки купли-продажи квартир, постаравшись максимально очистить базу от сделок с комнатами.</p>
<p>В общем, пользуйтесь, дорогие мои.</p>
<p>И если вам потребуется агент для продажи или покупки квартиры в Петербурге, обращайтесь по тел. <a href="tel:+79531762564">+7&nbsp;953&nbsp;176‑25‑64</a> или в телеграм <a href="https://t.me/vitaliy_real" rel="noopener noreferrer" target="_blank">@vitaliy_real</a>.</p>
""".strip()


def _seed_about_page(conn: sqlite3.Connection) -> bool:
    """Создаёт/обновляет «О проекте» при смене версии сида. True если меняли."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", ("about_seed_version",)
    ).fetchone()
    if row and str(row["value"]) == _ABOUT_SEED_VERSION:
        return False
    existing = conn.execute(
        "SELECT id, slug FROM pages WHERE slug IN (?, ?)",
        (_ABOUT_SLUG, "about"),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE pages
            SET slug = ?, title = ?, body = ?, meta_description = ?,
                is_published = 1, show_in_nav = 1, sort_order = 30,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                _ABOUT_SLUG,
                _ABOUT_TITLE,
                _ABOUT_BODY,
                _ABOUT_META,
                int(existing["id"]),
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO pages(
              slug, title, body, meta_description,
              is_published, show_in_nav, sort_order
            ) VALUES (?, ?, ?, ?, 1, 1, 30)
            """,
            (_ABOUT_SLUG, _ABOUT_TITLE, _ABOUT_BODY, _ABOUT_META),
        )
    conn.execute(
        """
        INSERT INTO settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("about_seed_version", _ABOUT_SEED_VERSION),
    )
    return True


def _seed(conn: sqlite3.Connection) -> bool:
    for field in TEXT_FIELDS:
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            (field["key"], field["default"]),
        )
    defaults = {f["key"]: f["default"] for f in TEXT_FIELDS}
    for key, old_values in _TEXT_MIGRATIONS.items():
        new_value = defaults.get(key)
        if not new_value:
            continue
        for old in old_values:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ? AND value = ?",
                (new_value, key, old),
            )
    existing = conn.execute("SELECT COUNT(*) FROM nav_links").fetchone()[0]
    if existing == 0:
        nav_defaults = [
            ("Поиск", "/", "header", 10, 1),
            ("Каталог улиц", "/catalog", "header", 20, 1),
        ]
        conn.executemany(
            """
            INSERT INTO nav_links(label, href, location, sort_order, is_visible)
            VALUES (?, ?, ?, ?, ?)
            """,
            nav_defaults,
        )
    about_changed = _seed_about_page(conn)
    conn.commit()
    return about_changed


def _checkpoint() -> Path:
    path = CMS_PATH
    if not path.exists():
        return path
    try:
        conn = _connect(path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("CMS checkpoint failed: %s", exc)
    return path


def persist_cms() -> tuple[bool, str]:
    """Сброс WAL и выгрузка в S3. (ok, message) — ok=False если S3 нужен, но не записался."""
    path = _checkpoint()
    if not s3_configured():
        return False, (
            "S3 не настроен — после редеплоя страницы пропадут. "
            "Задайте CMS_S3_BUCKET, CMS_S3_ACCESS_KEY, CMS_S3_SECRET_KEY."
        )
    return upload_cms_db(path)


def _checkpoint_and_upload() -> None:
    ok, msg = persist_cms()
    if not ok:
        logger.warning("CMS persist: %s", msg)


def ensure_cms() -> Path:
    global _ready, CMS_PATH
    with _lock:
        if _ready and CMS_PATH.exists() and CMS_PATH.stat().st_size > 0:
            return CMS_PATH
        target = CMS_PATH
        # 1) бэкап из Timeweb S3  2) упакованный cms.db  3) пустая схема
        if not target.exists() or target.stat().st_size == 0:
            ok, _msg = download_cms_db(target)
            if not ok and PACKAGED_CMS.exists() and PACKAGED_CMS.stat().st_size > 0:
                target.write_bytes(PACKAGED_CMS.read_bytes())
        conn = _connect(target)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              slug TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',
              meta_description TEXT NOT NULL DEFAULT '',
              is_published INTEGER NOT NULL DEFAULT 1,
              show_in_nav INTEGER NOT NULL DEFAULT 0,
              sort_order INTEGER NOT NULL DEFAULT 100,
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS nav_links (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              label TEXT NOT NULL,
              href TEXT NOT NULL,
              location TEXT NOT NULL DEFAULT 'header',
              sort_order INTEGER NOT NULL DEFAULT 100,
              is_visible INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        about_changed = _seed(conn)
        conn.close()
        CMS_PATH = target
        _ready = True
        if about_changed and s3_configured():
            ok, msg = upload_cms_db(target)
            if ok:
                logger.info("CMS about seed synced to S3: %s", msg)
            else:
                logger.warning("CMS about seed S3 sync failed: %s", msg)
        return target


def _db() -> sqlite3.Connection:
    ensure_cms()
    return _connect(CMS_PATH)


def get_texts() -> dict[str, str]:
    defaults = {f["key"]: f["default"] for f in TEXT_FIELDS}
    conn = _db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            defaults[row["key"]] = sanitize_plain_text(
                row["value"], max_len=4000
            )
        return defaults
    finally:
        conn.close()


def save_texts(values: dict[str, str]) -> None:
    conn = _db()
    try:
        for field in TEXT_FIELDS:
            key = field["key"]
            if key not in values:
                continue
            # Тексты сайта — только plain text (без HTML)
            max_len = 4000 if key == "default_description" else 500
            if key in {"default_description", "hero_lead", "catalog_section_lead"}:
                max_len = 2000
            cleaned = sanitize_plain_text(values[key], max_len=max_len)
            conn.execute(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, cleaned),
            )
        conn.commit()
    finally:
        conn.close()
    _checkpoint_and_upload()


def list_pages(*, published_only: bool = False) -> list[dict[str, Any]]:
    conn = _db()
    try:
        sql = "SELECT * FROM pages"
        if published_only:
            sql += " WHERE is_published = 1"
        sql += " ORDER BY sort_order, title"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _public_page(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["title"] = sanitize_plain_text(str(data.get("title") or ""), max_len=200)
    data["meta_description"] = sanitize_plain_text(
        str(data.get("meta_description") or ""), max_len=300
    )
    data["body"] = sanitize_html(str(data.get("body") or ""))
    return data


def get_page(page_id: int) -> dict[str, Any] | None:
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        return _public_page(row) if row else None
    finally:
        conn.close()


def get_page_by_slug(slug: str, *, published_only: bool = True) -> dict[str, Any] | None:
    conn = _db()
    try:
        sql = "SELECT * FROM pages WHERE slug = ?"
        params: list[Any] = [slug]
        if published_only:
            sql += " AND is_published = 1"
        row = conn.execute(sql, params).fetchone()
        return _public_page(row) if row else None
    finally:
        conn.close()


_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def slugify(text: str) -> str:
    """«О проекте» → «o-proekte»."""
    out: list[str] = []
    for ch in (text or "").strip().lower().replace("ё", "е"):
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        elif ch in {" ", "-", "_", ".", "/", "\\"}:
            out.append("-")
    slug = re.sub(r"-{2,}", "-", "".join(out)).strip("-")
    return slug[:80]


def validate_slug(slug: str) -> str | None:
    s = (slug or "").strip().lower()
    if not s:
        return "Укажите адрес страницы (латиницей), например about"
    if not _SLUG_RE.match(s):
        return "Адрес: только латиница, цифры и дефис (например about или o-proekte)"
    reserved = {
        "admin",
        "api",
        "catalog",
        "search",
        "street",
        "static",
        "robots.txt",
        "sitemap.xml",
        "sitemaps",
        "favicon.ico",
        "favicon.svg",
        "p",
    }
    reserved.add(admin_base_path().lower())
    if s in reserved:
        return "Этот адрес зарезервирован"
    return None


def create_page(
    *,
    slug: str,
    title: str,
    body: str,
    meta_description: str = "",
    is_published: bool = True,
    show_in_nav: bool = False,
    sort_order: int = 100,
) -> tuple[dict[str, Any] | None, str | None]:
    title = sanitize_plain_text(title, max_len=200)
    if not title:
        return None, "Укажите заголовок"
    slug = (slug or "").strip().lower() or slugify(title)
    err = validate_slug(slug)
    if err:
        return None, err
    safe_body = sanitize_html(body or "")
    safe_meta = sanitize_plain_text(meta_description or "", max_len=300)
    conn = _db()
    try:
        cur = conn.execute(
            """
            INSERT INTO pages(slug, title, body, meta_description, is_published, show_in_nav, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slug.strip().lower(),
                title,
                safe_body,
                safe_meta,
                1 if is_published else 0,
                1 if show_in_nav else 0,
                sort_order,
            ),
        )
        conn.commit()
        page = get_page(int(cur.lastrowid))
    except sqlite3.IntegrityError:
        return None, "Страница с таким адресом уже есть"
    finally:
        conn.close()
    _checkpoint_and_upload()
    return page, None


def update_page(
    page_id: int,
    *,
    slug: str,
    title: str,
    body: str,
    meta_description: str = "",
    is_published: bool = True,
    show_in_nav: bool = False,
    sort_order: int = 100,
) -> str | None:
    title = sanitize_plain_text(title, max_len=200)
    if not title:
        return "Укажите заголовок"
    slug = (slug or "").strip().lower() or slugify(title)
    err = validate_slug(slug)
    if err:
        return err
    safe_body = sanitize_html(body or "")
    safe_meta = sanitize_plain_text(meta_description or "", max_len=300)
    conn = _db()
    try:
        cur = conn.execute(
            """
            UPDATE pages
            SET slug = ?, title = ?, body = ?, meta_description = ?,
                is_published = ?, show_in_nav = ?, sort_order = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                slug.strip().lower(),
                title,
                safe_body,
                safe_meta,
                1 if is_published else 0,
                1 if show_in_nav else 0,
                sort_order,
                page_id,
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            return "Страница не найдена"
    except sqlite3.IntegrityError:
        return "Страница с таким адресом уже есть"
    finally:
        conn.close()
    _checkpoint_and_upload()
    return None


def delete_page(page_id: int) -> None:
    conn = _db()
    try:
        conn.execute("DELETE FROM pages WHERE id = ?", (page_id,))
        conn.commit()
    finally:
        conn.close()
    _checkpoint_and_upload()


def list_nav_links(location: str | None = None, *, visible_only: bool = False) -> list[dict[str, Any]]:
    conn = _db()
    try:
        sql = "SELECT * FROM nav_links WHERE 1=1"
        params: list[Any] = []
        if location:
            sql += " AND location = ?"
            params.append(location)
        if visible_only:
            sql += " AND is_visible = 1"
        sql += " ORDER BY sort_order, id"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _safe_nav_link(link: dict[str, Any]) -> dict[str, str] | None:
    href, err = validate_href(str(link.get("href") or ""))
    if err or not href:
        return None
    return {
        "label": sanitize_plain_text(str(link.get("label") or ""), max_len=120),
        "href": href,
    }


def public_nav_items() -> list[dict[str, str]]:
    """Ссылки шапки: ручные + опубликованные страницы с show_in_nav."""
    items: list[dict[str, str]] = []
    for link in list_nav_links("header", visible_only=True):
        safe = _safe_nav_link(link)
        if safe:
            items.append(safe)
    for page in list_pages(published_only=True):
        if int(page.get("show_in_nav") or 0):
            items.append(
                {
                    "label": sanitize_plain_text(str(page["title"]), max_len=120),
                    "href": f"/p/{page['slug']}",
                }
            )
    return items


def cms_storage_info() -> dict[str, Any]:
    """Для подсказки в админке: где лежит БД и эфемерна ли она."""
    path = CMS_PATH
    ephemeral = str(path).startswith("/tmp") or str(path).startswith(
        tempfile.gettempdir()
    )
    info = s3_public_info()
    s3 = bool(info["configured"])
    return {
        "path": str(path),
        "ephemeral": ephemeral and not s3,
        "s3": s3,
        "exists": path.exists(),
        "s3_info": info,
    }


def cms_backup_bytes() -> bytes:
    """Снимок SQLite для скачивания из админки."""
    ensure_cms()
    path = _checkpoint()
    return path.read_bytes()


def restore_cms_from_bytes(data: bytes) -> tuple[bool, str]:
    """Заменить локальную CMS из загруженного файла и попробовать залить в S3."""
    global _ready, CMS_PATH
    if not data or len(data) < 100:
        return False, "Файл слишком маленький — это не база CMS"
    if data[:16] != b"SQLite format 3\x00":
        return False, "Ожидался файл SQLite (cms.db)"
    with _lock:
        target = CMS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".restore")
        tmp.write_bytes(data)
        # снести wal/shm если были
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        tmp.replace(target)
        _ready = False
    ensure_cms()
    return persist_cms()


def sync_cms_to_s3() -> tuple[bool, str]:
    ensure_cms()
    return persist_cms()


def check_s3() -> tuple[bool, str]:
    return probe_s3()


def public_footer_links() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for link in list_nav_links("footer", visible_only=True):
        safe = _safe_nav_link(link)
        if safe:
            items.append(safe)
    return items


def create_nav_link(
    *,
    label: str,
    href: str,
    location: str = "header",
    sort_order: int = 100,
    is_visible: bool = True,
) -> tuple[dict[str, Any] | None, str | None]:
    label = sanitize_plain_text(label, max_len=120)
    safe_href, href_err = validate_href(href)
    if not label or not safe_href:
        return None, href_err or "Укажите название и URL"
    if location not in {"header", "footer"}:
        location = "header"
    conn = _db()
    try:
        cur = conn.execute(
            """
            INSERT INTO nav_links(label, href, location, sort_order, is_visible)
            VALUES (?, ?, ?, ?, ?)
            """,
            (label, safe_href, location, sort_order, 1 if is_visible else 0),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM nav_links WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        result = dict(row) if row else None
    finally:
        conn.close()
    _checkpoint_and_upload()
    return result, None


def update_nav_link(
    link_id: int,
    *,
    label: str,
    href: str,
    location: str = "header",
    sort_order: int = 100,
    is_visible: bool = True,
) -> str | None:
    label = sanitize_plain_text(label, max_len=120)
    safe_href, href_err = validate_href(href)
    if not label or not safe_href:
        return href_err or "Укажите название и URL"
    if location not in {"header", "footer"}:
        location = "header"
    conn = _db()
    try:
        cur = conn.execute(
            """
            UPDATE nav_links
            SET label = ?, href = ?, location = ?, sort_order = ?, is_visible = ?
            WHERE id = ?
            """,
            (label, safe_href, location, sort_order, 1 if is_visible else 0, link_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return "Ссылка не найдена"
    finally:
        conn.close()
    _checkpoint_and_upload()
    return None


def delete_nav_link(link_id: int) -> None:
    conn = _db()
    try:
        conn.execute("DELETE FROM nav_links WHERE id = ?", (link_id,))
        conn.commit()
    finally:
        conn.close()
    _checkpoint_and_upload()


def get_nav_link(link_id: int) -> dict[str, Any] | None:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM nav_links WHERE id = ?", (link_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
