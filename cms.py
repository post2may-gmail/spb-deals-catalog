"""CMS: тексты сайта, страницы и навигационные ссылки."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

from security import admin_base_path, sanitize_html, sanitize_plain_text, validate_href
from seo import BRAND, DEFAULT_DESCRIPTION

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


def _seed(conn: sqlite3.Connection) -> None:
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
    conn.commit()


def ensure_cms() -> Path:
    global _ready, CMS_PATH
    with _lock:
        if _ready and CMS_PATH.exists() and CMS_PATH.stat().st_size > 0:
            return CMS_PATH
        target = CMS_PATH
        if PACKAGED_CMS.exists() and PACKAGED_CMS.stat().st_size > 0 and (
            not target.exists() or target.stat().st_size == 0
        ):
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
        _seed(conn)
        conn.close()
        CMS_PATH = target
        _ready = True
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
        return get_page(int(cur.lastrowid)), None
    except sqlite3.IntegrityError:
        return None, "Страница с таким адресом уже есть"
    finally:
        conn.close()


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
        return None
    except sqlite3.IntegrityError:
        return "Страница с таким адресом уже есть"
    finally:
        conn.close()


def delete_page(page_id: int) -> None:
    conn = _db()
    try:
        conn.execute("DELETE FROM pages WHERE id = ?", (page_id,))
        conn.commit()
    finally:
        conn.close()


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
    return {
        "path": str(path),
        "ephemeral": ephemeral,
        "exists": path.exists(),
    }


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
        return dict(row) if row else None, None
    finally:
        conn.close()


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
        return None
    finally:
        conn.close()


def delete_nav_link(link_id: int) -> None:
    conn = _db()
    try:
        conn.execute("DELETE FROM nav_links WHERE id = ?", (link_id,))
        conn.commit()
    finally:
        conn.close()


def get_nav_link(link_id: int) -> dict[str, Any] | None:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM nav_links WHERE id = ?", (link_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
