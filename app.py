from __future__ import annotations

import io
import logging
import os
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Any, AsyncIterator
from urllib.parse import quote, urlencode
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import cms
from admin_app import build_admin_router
from db import DealsRepository, ensure_db
from normalize import ALPHABET, street_letter
from periods import Quarter
from security import (
    BlockPrivatePathsMiddleware,
    HeavyRequestLimitMiddleware,
    admin_base_path,
    admin_base_url,
    SecurityHeadersMiddleware,
    client_ip,
    rate_limit_exceeded,
    require_session_secret,
    sessions_https_only,
    source_zip_enabled,
)
from seo import (
    BRAND as DEFAULT_BRAND,
    DEFAULT_DESCRIPTION as FALLBACK_DESCRIPTION,
    abs_url,
    breadcrumb_ld,
    dataset_ld,
    dumps_ld,
    public_base_url,
    street_page_ld,
    street_path,
    website_ld,
)
from street_titles import street_title

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
STATIC_DIR = BASE_DIR / "static"

REPO: DealsRepository | None = None

_SITEMAP_CACHE_TTL_SEC = 600.0
_sitemap_cache: dict[str, tuple[float, str]] = {}
_sitemap_cache_lock = Lock()


def _sitemap_cached(key: str, builder) -> str:
    now = time.time()
    with _sitemap_cache_lock:
        hit = _sitemap_cache.get(key)
        if hit and now - hit[0] < _SITEMAP_CACHE_TTL_SEC:
            return hit[1]
    body = builder()
    with _sitemap_cache_lock:
        _sitemap_cache[key] = (now, body)
    return body


def _urlset(entries: list[tuple[str, str]], request: Request) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, freq in entries:
        loc = xml_escape(abs_url(request, path))
        parts.append(f"<url><loc>{loc}</loc><changefreq>{freq}</changefreq></url>")
    parts.append("</urlset>")
    return "\n".join(parts)


def _sitemap_cache_key(request: Request, part: str) -> str:
    return f"{part}:{public_base_url(request)}"


def _money(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", "\u00a0") + "\u00a0₽"


def _area(value: float | None) -> str:
    if value is None:
        return "—"
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text}\u00a0м²"


def _quarter_label(year: int, quarter: int) -> str:
    return f"{quarter} кв. {year}"


def _materials(value: str | None, limit: int = 2) -> str:
    """Короткий вид материала: без раздувания строк таблицы."""
    raw = (value or "").strip()
    if not raw:
        return "—"
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not parts:
        return "—"
    if len(parts) <= limit:
        return " · ".join(parts)
    shown = " · ".join(parts[:limit])
    return f"{shown} · +{len(parts) - limit}"


def _materials_full(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    return " · ".join(parts)


TEMPLATES.env.filters["money"] = _money
TEMPLATES.env.filters["area"] = _area
TEMPLATES.env.filters["materials"] = _materials
TEMPLATES.env.filters["materials_full"] = _materials_full
TEMPLATES.env.filters["quarter_label"] = lambda row: _quarter_label(
    row["year"], row["quarter"]
)
TEMPLATES.env.filters["urlencode"] = lambda s: quote(str(s), safe="")
TEMPLATES.env.filters["street_title"] = street_title


def _parse_period_range(
    period_from: str, period_to: str
) -> tuple[Quarter | None, Quarter | None, str | None]:
    pf = Quarter.from_value(period_from)
    pt = Quarter.from_value(period_to)
    if pf is not None and pt is not None and pf > pt:
        return (
            None,
            None,
            "Период «с» не может быть позже периода «по». "
            "Выберите кварталы от раннего к позднему или оставьте «Все кварталы».",
        )
    return pf, pt, None


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        global REPO
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        try:
            ensure_db()
            cms.ensure_cms()
            REPO = DealsRepository()
            logger.info("catalog ready: %s", REPO.stats())
        except Exception:
            # Не роняем весь ASGI на serverless — лениво поднимем на первом запросе.
            logger.exception("catalog bootstrap failed; will retry on demand")
            REPO = None
        yield
        if REPO is not None:
            REPO.close()
            REPO = None

    docs_on = (os.getenv("ENABLE_API_DOCS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    app = FastAPI(
        title=DEFAULT_BRAND,
        lifespan=lifespan,
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    # Порядок: последний добавленный — самый внешний.
    app.add_middleware(
        SessionMiddleware,
        secret_key=require_session_secret(),
        session_cookie="spb_admin",
        same_site="lax",
        https_only=sessions_https_only(),
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(HeavyRequestLimitMiddleware, max_calls=90, window_sec=60.0)
    app.add_middleware(BlockPrivatePathsMiddleware)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(build_admin_router(TEMPLATES))

    def repo() -> DealsRepository:
        global REPO
        if REPO is None:
            ensure_db()
            cms.ensure_cms()
            REPO = DealsRepository()
        return REPO

    def site_texts() -> dict[str, str]:
        return cms.get_texts()

    def brand_name() -> str:
        return site_texts().get("brand") or DEFAULT_BRAND

    def site_description() -> str:
        return site_texts().get("default_description") or FALLBACK_DESCRIPTION

    def base_ctx(request: Request, **extra: Any) -> dict[str, Any]:
        r = repo()
        quarters = r.quarters()
        stats = r.stats()
        texts = site_texts()
        brand = texts.get("brand") or DEFAULT_BRAND
        default_description = texts.get("default_description") or FALLBACK_DESCRIPTION
        canonical_path = extra.pop("canonical_path", request.url.path or "/")
        robots = extra.pop("robots", "index,follow,max-image-preview:large")
        meta_title = extra.pop("meta_title", None)
        meta_description = extra.pop("meta_description", None)
        json_ld = extra.pop("json_ld", None)
        json_ld_extra = extra.pop("json_ld_extra", None)
        ctx = {
            "request": request,
            "stats": stats,
            "alphabet": ALPHABET,
            "letters": r.letters_with_counts(),
            "quarters": quarters,
            "texts": texts,
            "brand": brand,
            "default_description": default_description,
            "nav_items": cms.public_nav_items(),
            "footer_links": cms.public_footer_links(),
            "meta_title": meta_title or brand,
            "meta_description": meta_description or default_description,
            "canonical_url": abs_url(request, canonical_path),
            "robots": robots,
            "og_type": extra.pop("og_type", "website"),
            "og_image": extra.pop("og_image", abs_url(request, "/favicon.svg")),
            "json_ld": json_ld,
            "json_ld_extra": json_ld_extra,
            # Коды подтверждения для sdelkispb.ru (Search Console / Вебмастер)
            "google_site_verification": os.getenv(
                "GOOGLE_SITE_VERIFICATION",
                "5NjEm_xoie4FDJ7twznaCQ590-JwmFEStqUFssHQtyg",
            ),
            "yandex_verification": os.getenv(
                "YANDEX_VERIFICATION", "c61284db6bb2b623"
            ),
            "yandex_metrika_id": (
                os.getenv("YANDEX_METRIKA_ID", "114652550") or ""
            ).strip(),
        }
        ctx.update(extra)
        return ctx

    @app.get("/__health")
    async def health() -> dict[str, Any]:
        # Маркер успешного деплоя правок под Яндекс.Метрику (frame-ancestors).
        return {"ok": True, "metrika_framing": True, "build": "metrika-csp-v3"}

    @app.get("/__whoami")
    async def whoami(request: Request, token: str = "") -> dict[str, Any]:
        """Показывает IP, который видит приложение (для настройки ADMIN_ALLOWED_IPS).

        Включается только при заданном ADMIN_DEBUG_TOKEN:
        /__whoami?token=...
        После настройки админки переменную удалите.
        """
        expected = (os.getenv("ADMIN_DEBUG_TOKEN") or "").strip()
        if not expected or not token or token != expected:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        from security import client_ip, trust_proxy_headers, trusted_proxy_peers

        direct = request.client.host if request.client else None
        return {
            "client_ip": client_ip(request),
            "direct_peer": direct,
            "trust_proxy": trust_proxy_headers(),
            "trusted_peers": sorted(trusted_proxy_peers()),
            "x_forwarded_for": request.headers.get("x-forwarded-for"),
            "x_real_ip": request.headers.get("x-real-ip"),
        }

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        stats = repo().stats()
        brand = brand_name()
        description = site_description()
        return TEMPLATES.TemplateResponse(
            request,
            "home.html",
            base_ctx(
                request,
                canonical_path="/",
                meta_title=brand,
                meta_description=description,
                json_ld=dumps_ld(website_ld(request, name=brand, description=description)),
                json_ld_extra=dumps_ld(
                    dataset_ld(
                        request,
                        stats["deals"],
                        stats["streets"],
                        name=brand,
                        description=description,
                    )
                ),
            ),
        )

    @app.get("/search", response_class=HTMLResponse)
    async def search(
        request: Request,
        street: str = Query(""),
        period_from: str = Query(""),
        period_to: str = Query(""),
    ) -> HTMLResponse:
        q = (street or "").strip()
        if not q:
            return TEMPLATES.TemplateResponse(
                request,
                "home.html",
                base_ctx(
                    request,
                    error="Введите название улицы",
                    canonical_path="/",
                    robots="noindex,follow",
                ),
                status_code=400,
            )
        resolved = repo().resolve_street(q)
        if not resolved:
            return TEMPLATES.TemplateResponse(
                request,
                "search_results.html",
                base_ctx(
                    request,
                    query=q,
                    street=None,
                    deals=[],
                    period_from=period_from,
                    period_to=period_to,
                    not_found=True,
                    suggestions=repo().suggest_streets(q, limit=12),
                    canonical_path="/search",
                    robots="noindex,follow",
                    meta_title=f"Улица не найдена — {brand_name()}",
                    meta_description="По запросу улица не найдена. Воспользуйтесь каталогом улиц Санкт-Петербурга.",
                ),
            )
        pf, pt, period_error = _parse_period_range(period_from, period_to)
        if period_error:
            return TEMPLATES.TemplateResponse(
                request,
                "home.html",
                base_ctx(
                    request,
                    error=period_error,
                    canonical_path="/",
                    robots="noindex,follow",
                ),
                status_code=400,
            )
        # Каноникал — страница улицы (без дублей /search?…).
        params = {}
        if period_from:
            params["period_from"] = period_from
        if period_to:
            params["period_to"] = period_to
        target = street_path(resolved)
        if params:
            target = f"{target}?{urlencode(params)}"
        return RedirectResponse(target, status_code=302)

    @app.get("/catalog", response_class=HTMLResponse)
    async def catalog_index(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "catalog_index.html",
            base_ctx(
                request,
                canonical_path="/catalog",
                meta_title=f"Каталог улиц — {brand_name()}",
                meta_description=(
                    "Алфавитный каталог улиц Санкт-Петербурга с ценами "
                    "зарегистрированных сделок по данным Росреестра."
                ),
                json_ld=dumps_ld(
                    breadcrumb_ld(
                        request,
                        [("Главная", "/"), ("Каталог улиц", "/catalog")],
                    )
                ),
            ),
        )

    @app.get("/catalog/{letter}", response_class=HTMLResponse)
    async def catalog_letter(request: Request, letter: str) -> HTMLResponse:
        letter_key = letter.strip().upper().replace("Ё", "Е")
        if letter_key in {"0", "0-9", "#"}:
            letter_key = "0-9"
        streets = [
            {**s, "title": street_title(s["street"])}
            for s in repo().streets_by_letter(letter_key)
        ]
        path = f"/catalog/{quote(letter_key, safe='')}"
        return TEMPLATES.TemplateResponse(
            request,
            "catalog_letter.html",
            base_ctx(
                request,
                letter=letter_key,
                streets=streets,
                canonical_path=path,
                meta_title=f"Улицы на «{letter_key}» — {brand_name()}",
                meta_description=(
                    f"Список улиц Санкт-Петербурга на букву «{letter_key}» "
                    f"с ценами сделок Росреестра. {len(streets)} улиц в каталоге."
                ),
                json_ld=dumps_ld(
                    breadcrumb_ld(
                        request,
                        [
                            ("Главная", "/"),
                            ("Каталог улиц", "/catalog"),
                            (f"Буква {letter_key}", path),
                        ],
                    )
                ),
            ),
        )

    @app.get("/street/{street_name}", response_class=HTMLResponse)
    async def street_page(
        request: Request,
        street_name: str,
        period_from: str = Query(""),
        period_to: str = Query(""),
    ) -> HTMLResponse:
        resolved = repo().resolve_street(street_name)
        if not resolved:
            return TEMPLATES.TemplateResponse(
                request,
                "street.html",
                base_ctx(
                    request,
                    street=street_name,
                    deals=[],
                    not_found=True,
                    period_from=period_from,
                    period_to=period_to,
                    canonical_path=street_path(street_name),
                    robots="noindex,follow",
                    meta_title=f"Улица не найдена — {brand_name()}",
                ),
                status_code=404,
            )
        # канонический URL имени улицы
        if resolved != street_name:
            qs = request.url.query
            target = street_path(resolved)
            if qs:
                target = f"{target}?{qs}"
            return RedirectResponse(target, status_code=301)
        pf, pt, period_error = _parse_period_range(period_from, period_to)
        title_name = street_title(resolved)
        alpha = street_letter(resolved)
        canon = street_path(resolved)
        if period_error:
            return TEMPLATES.TemplateResponse(
                request,
                "street.html",
                base_ctx(
                    request,
                    street=resolved,
                    street_display=title_name,
                    street_alpha=alpha,
                    deals=[],
                    not_found=False,
                    period_from=period_from,
                    period_to=period_to,
                    error=period_error,
                    canonical_path=canon,
                    robots="noindex,follow",
                    meta_title=f"{title_name} — цены сделок в Санкт-Петербурге",
                ),
                status_code=400,
            )
        page_limit = DealsRepository.DEFAULT_DEALS_LIMIT
        raw_deals = repo().search_deals(
            resolved,
            period_from=pf,
            period_to=pt,
            limit=page_limit + 1,
        )
        deals_truncated = len(raw_deals) > page_limit
        deals = raw_deals[:page_limit]
        period_note = ""
        if pf or pt:
            period_note = (
                f" Период: {pf.label if pf else 'начало'} — "
                f"{pt.label if pt else 'конец'}."
            )
        # Страницы с фильтром периода не индексируем отдельно — каноникал без query.
        robots = (
            "noindex,follow"
            if (period_from or period_to)
            else "index,follow,max-image-preview:large"
        )
        return TEMPLATES.TemplateResponse(
            request,
            "street.html",
            base_ctx(
                request,
                street=resolved,
                street_display=title_name,
                street_alpha=alpha,
                deals=deals,
                deals_truncated=deals_truncated,
                deals_limit=page_limit,
                not_found=False,
                period_from=period_from,
                period_to=period_to,
                period_from_label=pf.label if pf else None,
                period_to_label=pt.label if pt else None,
                page_title=f"Цены сделок на {title_name}",
                canonical_path=canon,
                robots=robots,
                meta_title=f"Цены сделок на {title_name} в Санкт-Петербурге",
                meta_description=(
                    f"Цены зарегистрированных сделок с жильём на {title_name} "
                    f"в Санкт-Петербурге по данным Росреестра. "
                    f"В выборке {len(deals)} сделок.{period_note}"
                ),
                og_type="article",
                json_ld=dumps_ld(
                    street_page_ld(
                        request,
                        street=resolved,
                        street_display=title_name,
                        deals_count=len(deals),
                    )
                ),
                json_ld_extra=dumps_ld(
                    breadcrumb_ld(
                        request,
                        [
                            ("Главная", "/"),
                            ("Каталог улиц", "/catalog"),
                            (f"Буква {alpha}", f"/catalog/{quote(alpha, safe='')}"),
                            (title_name, canon),
                        ],
                    )
                ),
            ),
        )

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots_txt(request: Request) -> PlainTextResponse:
        sitemap = abs_url(request, "/sitemap.xml")
        body = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            f"Disallow: {admin_base_url()}\n"
            f"Disallow: {admin_base_url()}/\n"
            "Disallow: /search?\n"
            "Disallow: /*?*period_from=\n"
            "Disallow: /*?*period_to=\n"
            f"Sitemap: {sitemap}\n"
        )
        return PlainTextResponse(body, media_type="text/plain; charset=utf-8")

    @app.get("/sitemap.xml")
    async def sitemap_xml(request: Request) -> Response:
        """Индекс sitemap: лёгкий ответ, улицы вынесены в /sitemaps/streets-{letter}.xml."""

        def build() -> str:
            locs = [abs_url(request, "/sitemaps/core.xml")]
            for letter in ALPHABET:
                locs.append(
                    abs_url(request, f"/sitemaps/streets-{quote(letter, safe='')}.xml")
                )
            locs.append(abs_url(request, "/sitemaps/pages.xml"))
            parts = [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            ]
            for loc in locs:
                parts.append(
                    f"<sitemap><loc>{xml_escape(loc)}</loc></sitemap>"
                )
            parts.append("</sitemapindex>")
            return "\n".join(parts)

        xml = _sitemap_cached(_sitemap_cache_key(request, "index"), build)
        return Response(
            content=xml,
            media_type="application/xml; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/sitemaps/core.xml")
    async def sitemap_core(request: Request) -> Response:
        def build() -> str:
            urls: list[tuple[str, str]] = [
                ("/", "daily"),
                ("/catalog", "weekly"),
            ]
            for letter in ALPHABET:
                urls.append((f"/catalog/{quote(letter, safe='')}", "weekly"))
            return _urlset(urls, request)

        xml = _sitemap_cached(_sitemap_cache_key(request, "core"), build)
        return Response(
            content=xml,
            media_type="application/xml; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/sitemaps/pages.xml")
    async def sitemap_pages(request: Request) -> Response:
        def build() -> str:
            urls = [
                (f"/p/{page['slug']}", "weekly")
                for page in cms.list_pages(published_only=True)
            ]
            return _urlset(urls, request)

        xml = _sitemap_cached(_sitemap_cache_key(request, "pages"), build)
        return Response(
            content=xml,
            media_type="application/xml; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/sitemaps/streets-{letter}.xml")
    async def sitemap_streets_letter(request: Request, letter: str) -> Response:
        letter_key = letter.strip().upper().replace("Ё", "Е")
        if letter_key in {"0", "#"}:
            letter_key = "0-9"
        if letter_key not in ALPHABET:
            return Response(content="Not Found", status_code=404)

        def build() -> str:
            urls = [
                (street_path(row["street"]), "weekly")
                for row in repo().streets_by_letter(letter_key)
            ]
            return _urlset(urls, request)

        xml = _sitemap_cached(
            _sitemap_cache_key(request, f"streets:{letter_key}"),
            build,
        )
        return Response(
            content=xml,
            media_type="application/xml; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/favicon.svg")
    async def favicon() -> Response:
        path = STATIC_DIR / "favicon.svg"
        return Response(
            content=path.read_text(encoding="utf-8"),
            media_type="image/svg+xml",
        )

    @app.get("/favicon.ico")
    async def favicon_ico() -> RedirectResponse:
        return RedirectResponse("/favicon.svg", status_code=302)

    @app.get("/p/{slug}", response_class=HTMLResponse)
    async def cms_page(request: Request, slug: str) -> HTMLResponse:
        page = cms.get_page_by_slug(slug, published_only=True)
        if not page:
            return TEMPLATES.TemplateResponse(
                request,
                "cms_page.html",
                base_ctx(
                    request,
                    page={"title": "Страница не найдена", "body": "<p>Страница не найдена или снята с публикации.</p>"},
                    canonical_path=f"/p/{quote(slug, safe='')}",
                    robots="noindex,follow",
                    meta_title=f"Страница не найдена — {brand_name()}",
                ),
                status_code=404,
            )
        path = f"/p/{page['slug']}"
        return TEMPLATES.TemplateResponse(
            request,
            "cms_page.html",
            base_ctx(
                request,
                page=page,
                canonical_path=path,
                meta_title=f"{page['title']} — {brand_name()}",
                meta_description=page.get("meta_description")
                or f"{page['title']}. {site_description()}",
                json_ld=dumps_ld(
                    breadcrumb_ld(
                        request,
                        [("Главная", "/"), (page["title"], path)],
                    )
                ),
            ),
        )

    @app.get("/api/suggest", response_model=None)
    async def api_suggest(request: Request, q: str = Query("")) -> JSONResponse:
        ip = client_ip(request)
        if rate_limit_exceeded(
            f"suggest:{ip}",
            max_calls=60,
            window_sec=60.0,
        ):
            return JSONResponse(
                {"error": "rate_limited", "items": []},
                status_code=429,
            )
        query = (q or "").strip()
        if len(query) > 80:
            query = query[:80]
        items = []
        for row in repo().suggest_streets(query, limit=15):
            items.append(
                {
                    "street": row["street"],
                    "title": street_title(row["street"]),
                    "deals_count": row["deals_count"],
                }
            )
        return JSONResponse({"items": items})

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return {"ok": True, **repo().stats(), "quarters": [q.value for q in repo().quarters()]}

    @app.get("/source.zip")
    async def source_zip(request: Request) -> Response:
        """Архив кода без БД. Выкл. по умолчанию (ENABLE_SOURCE_ZIP=1).

        Лимит 2/час на IP. data/deals.db.gz в архив не входит и по URL закрыт.
        """
        if not source_zip_enabled():
            return Response(content="Not Found", status_code=404)
        ip = client_ip(request)
        if rate_limit_exceeded(
            f"source_zip:{ip}",
            max_calls=2,
            window_sec=3600.0,
        ):
            return Response(
                content="Too Many Requests",
                status_code=429,
                headers={"Retry-After": "3600", "Cache-Control": "no-store"},
            )
        root = Path(__file__).resolve().parent
        skip_dir_names = {"__pycache__", ".venv", "venv", ".git"}
        skip_suffixes = {".pyc", ".pyo", ".db-wal", ".db-shm", ".db"}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                if any(part in skip_dir_names for part in path.parts):
                    continue
                if path.suffix in skip_suffixes:
                    continue
                name = path.name.lower()
                if (
                    path.suffix == ".csv"
                    or name.endswith(".csv.gz")
                    or name.endswith(".db.gz")
                    or name in {"deals.db", "deals.db.gz"}
                ):
                    continue
                rel = path.relative_to(root).as_posix()
                zf.write(path, arcname=rel)
            processed = "data/processed/.gitkeep"
            if processed not in zf.namelist():
                zf.writestr(processed, "")
            deploy = (
                "# Timeweb Cloud Apps (отдельный репозиторий)\n\n"
                "Root directory: `/`.\n\n"
                "Build: `pip3 install --upgrade -r requirements.txt`\n\n"
                "Start: `uvicorn main:app --host 0.0.0.0 --port 80`\n\n"
                "Положите data/deals.db.gz отдельно (в HTTP-архив не входит).\n"
            )
            zf.writestr("DEPLOY_TIMEWEB.md", deploy)
        buf.seek(0)
        headers = {
            "Content-Disposition": 'attachment; filename="spb-deals-catalog.zip"',
            "Cache-Control": "no-store",
        }
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers=headers,
        )

    return app


app = create_app()
