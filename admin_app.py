"""Админка: тексты, страницы, ссылки."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi import status as http_status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import cms
from security import (
    admin_access_allowed,
    clear_login_failures,
    client_ip,
    issue_csrf,
    login_is_blocked,
    passwords_match,
    register_login_failure,
    require_admin_password,
    rotate_session,
    validate_csrf,
)

logger = logging.getLogger(__name__)


def _authed(request: Request) -> bool:
    return bool(request.session.get("admin"))


def build_admin_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    def ctx(request: Request, **extra: Any) -> dict[str, Any]:
        data = {
            "request": request,
            "authed": _authed(request),
            "flash": request.session.pop("flash", None),
            "admin_section": extra.pop("admin_section", ""),
            "csrf_token": issue_csrf(request),
            "admin_configured": require_admin_password() is not None,
        }
        data.update(extra)
        return data

    def flash(request: Request, message: str) -> None:
        request.session["flash"] = message

    def reject_csrf() -> HTMLResponse:
        return HTMLResponse("Недействительный CSRF-токен", status_code=403)

    def ensure_csrf(request: Request, token: str | None) -> HTMLResponse | None:
        if validate_csrf(request, token):
            return None
        return reject_csrf()

    def deny_admin_gate(request: Request) -> HTMLResponse | None:
        """Без ADMIN_ALLOWED_IPS админка скрыта (404)."""
        ip = client_ip(request)
        if admin_access_allowed(ip, request):
            return None
        direct = request.client.host if request.client else None
        logger.warning(
            "admin 404: client_ip=%s direct=%s xff=%r xri=%r",
            ip,
            direct,
            request.headers.get("x-forwarded-for"),
            request.headers.get("x-real-ip"),
        )
        return HTMLResponse("Not Found", status_code=404)

    def require_admin(request: Request) -> HTMLResponse | RedirectResponse | None:
        gated = deny_admin_gate(request)
        if gated:
            return gated
        if _authed(request):
            return None
        return RedirectResponse("/admin/login", status_code=302)

    @router.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> HTMLResponse:
        gated = deny_admin_gate(request)
        if gated:
            return gated
        if _authed(request):
            return RedirectResponse("/admin", status_code=302)
        return templates.TemplateResponse(
            request, "admin/login.html", ctx(request, error=None)
        )

    @router.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        password: str = Form(""),
        csrf_token: str = Form(""),
    ) -> HTMLResponse:
        gated = deny_admin_gate(request)
        if gated:
            return gated
        bad_csrf = ensure_csrf(request, csrf_token)
        if bad_csrf:
            return bad_csrf

        ip = client_ip(request)
        if login_is_blocked(ip):
            return templates.TemplateResponse(
                request,
                "admin/login.html",
                ctx(
                    request,
                    error="Слишком много попыток. Подождите и попробуйте снова.",
                ),
                status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
            )

        expected = require_admin_password()
        if expected is None:
            return templates.TemplateResponse(
                request,
                "admin/login.html",
                ctx(
                    request,
                    error="Админка не настроена: задайте переменную ADMIN_PASSWORD.",
                ),
                status_code=503,
            )

        if passwords_match(password, expected):
            clear_login_failures(ip)
            rotate_session(request)
            request.session["admin"] = True
            issue_csrf(request)
            flash(request, "Вы вошли в админку")
            return RedirectResponse("/admin", status_code=303)

        register_login_failure(ip)
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            ctx(request, error="Неверный пароль"),
            status_code=401,
        )

    @router.post("/logout", response_model=None)
    async def logout(
        request: Request, csrf_token: str = Form("")
    ) -> Any:
        gated = deny_admin_gate(request)
        if gated:
            return gated
        bad_csrf = ensure_csrf(request, csrf_token)
        if bad_csrf:
            return bad_csrf
        request.session.clear()
        return RedirectResponse("/admin/login", status_code=303)

    @router.get("", response_class=HTMLResponse)
    async def dashboard(request: Request) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        return templates.TemplateResponse(
            request,
            "admin/dashboard.html",
            ctx(
                request,
                admin_section="home",
                pages_count=len(cms.list_pages()),
                links_count=len(cms.list_nav_links()),
                cms_storage=cms.cms_storage_info(),
            ),
        )

    @router.get("/texts", response_class=HTMLResponse)
    async def texts_form(request: Request) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        texts = cms.get_texts()
        fields = [
            {**f, "value": texts.get(f["key"], f["default"])} for f in cms.TEXT_FIELDS
        ]
        return templates.TemplateResponse(
            request,
            "admin/texts.html",
            ctx(request, admin_section="texts", fields=fields),
        )

    @router.post("/texts", response_model=None)
    async def texts_save(
        request: Request, csrf_token: str = Form("")
    ) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        bad_csrf = ensure_csrf(request, csrf_token)
        if bad_csrf:
            return bad_csrf
        form = await request.form()
        values = {f["key"]: str(form.get(f["key"], "")) for f in cms.TEXT_FIELDS}
        cms.save_texts(values)
        flash(request, "Тексты сохранены")
        return RedirectResponse("/admin/texts", status_code=303)

    @router.get("/pages", response_class=HTMLResponse)
    async def pages_list(request: Request) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        return templates.TemplateResponse(
            request,
            "admin/pages.html",
            ctx(request, admin_section="pages", pages=cms.list_pages()),
        )

    @router.get("/pages/new", response_class=HTMLResponse)
    async def page_new(request: Request) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        return templates.TemplateResponse(
            request,
            "admin/page_edit.html",
            ctx(
                request,
                admin_section="pages",
                page=None,
                error=None,
                form={
                    "slug": "",
                    "title": "",
                    "body": "",
                    "meta_description": "",
                    "is_published": True,
                    "show_in_nav": False,
                    "sort_order": 100,
                },
            ),
        )

    @router.post("/pages/new", response_model=None)
    async def page_create(
        request: Request,
        slug: str = Form(""),
        title: str = Form(""),
        body: str = Form(""),
        meta_description: str = Form(""),
        sort_order: int = Form(100),
        is_published: str | None = Form(None),
        show_in_nav: str | None = Form(None),
        csrf_token: str = Form(""),
    ) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        bad_csrf = ensure_csrf(request, csrf_token)
        if bad_csrf:
            return bad_csrf
        page, err = cms.create_page(
            slug=slug,
            title=title,
            body=body,
            meta_description=meta_description,
            is_published=is_published is not None,
            show_in_nav=show_in_nav is not None,
            sort_order=sort_order,
        )
        if err:
            return templates.TemplateResponse(
                request,
                "admin/page_edit.html",
                ctx(
                    request,
                    admin_section="pages",
                    page=None,
                    error=err,
                    form={
                        "slug": slug,
                        "title": title,
                        "body": body,
                        "meta_description": meta_description,
                        "is_published": is_published is not None,
                        "show_in_nav": show_in_nav is not None,
                        "sort_order": sort_order,
                    },
                ),
                status_code=400,
            )
        flash(
            request,
            f"Страница «{page['title']}» создана — на сайте: /p/{page['slug']}",
        )
        return RedirectResponse(f"/admin/pages/{page['id']}", status_code=303)

    @router.get("/pages/{page_id}", response_class=HTMLResponse)
    async def page_edit(request: Request, page_id: int) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        page = cms.get_page(page_id)
        if not page:
            flash(request, "Страница не найдена")
            return RedirectResponse("/admin/pages", status_code=302)
        return templates.TemplateResponse(
            request,
            "admin/page_edit.html",
            ctx(
                request,
                admin_section="pages",
                page=page,
                error=None,
                form={
                    "slug": page["slug"],
                    "title": page["title"],
                    "body": page["body"],
                    "meta_description": page["meta_description"],
                    "is_published": bool(page["is_published"]),
                    "show_in_nav": bool(page["show_in_nav"]),
                    "sort_order": page["sort_order"],
                },
            ),
        )

    @router.post("/pages/{page_id}", response_model=None)
    async def page_update(
        request: Request,
        page_id: int,
        slug: str = Form(""),
        title: str = Form(""),
        body: str = Form(""),
        meta_description: str = Form(""),
        sort_order: int = Form(100),
        is_published: str | None = Form(None),
        show_in_nav: str | None = Form(None),
        csrf_token: str = Form(""),
    ) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        bad_csrf = ensure_csrf(request, csrf_token)
        if bad_csrf:
            return bad_csrf
        err = cms.update_page(
            page_id,
            slug=slug,
            title=title,
            body=body,
            meta_description=meta_description,
            is_published=is_published is not None,
            show_in_nav=show_in_nav is not None,
            sort_order=sort_order,
        )
        if err:
            page = cms.get_page(page_id)
            return templates.TemplateResponse(
                request,
                "admin/page_edit.html",
                ctx(
                    request,
                    admin_section="pages",
                    page=page,
                    error=err,
                    form={
                        "slug": slug,
                        "title": title,
                        "body": body,
                        "meta_description": meta_description,
                        "is_published": is_published is not None,
                        "show_in_nav": show_in_nav is not None,
                        "sort_order": sort_order,
                    },
                ),
                status_code=400,
            )
        updated = cms.get_page(page_id)
        slug = (updated or {}).get("slug") or slug
        flash(request, f"Страница сохранена — на сайте: /p/{slug}")
        return RedirectResponse(f"/admin/pages/{page_id}", status_code=303)

    @router.post("/pages/{page_id}/delete", response_model=None)
    async def page_delete(
        request: Request, page_id: int, csrf_token: str = Form("")
    ) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        bad_csrf = ensure_csrf(request, csrf_token)
        if bad_csrf:
            return bad_csrf
        cms.delete_page(page_id)
        flash(request, "Страница удалена")
        return RedirectResponse("/admin/pages", status_code=303)

    @router.get("/links", response_class=HTMLResponse)
    async def links_list(request: Request) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        return templates.TemplateResponse(
            request,
            "admin/links.html",
            ctx(request, admin_section="links", links=cms.list_nav_links()),
        )

    @router.get("/links/new", response_class=HTMLResponse)
    async def link_new(request: Request) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        return templates.TemplateResponse(
            request,
            "admin/link_edit.html",
            ctx(
                request,
                admin_section="links",
                link=None,
                error=None,
                form={
                    "label": "",
                    "href": "/",
                    "location": "header",
                    "sort_order": 100,
                    "is_visible": True,
                },
            ),
        )

    @router.post("/links/new", response_model=None)
    async def link_create(
        request: Request,
        label: str = Form(""),
        href: str = Form(""),
        location: str = Form("header"),
        sort_order: int = Form(100),
        is_visible: str | None = Form(None),
        csrf_token: str = Form(""),
    ) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        bad_csrf = ensure_csrf(request, csrf_token)
        if bad_csrf:
            return bad_csrf
        link, err = cms.create_nav_link(
            label=label,
            href=href,
            location=location,
            sort_order=sort_order,
            is_visible=is_visible is not None,
        )
        if err:
            return templates.TemplateResponse(
                request,
                "admin/link_edit.html",
                ctx(
                    request,
                    admin_section="links",
                    link=None,
                    error=err,
                    form={
                        "label": label,
                        "href": href,
                        "location": location,
                        "sort_order": sort_order,
                        "is_visible": is_visible is not None,
                    },
                ),
                status_code=400,
            )
        flash(request, "Ссылка добавлена")
        return RedirectResponse(f"/admin/links/{link['id']}", status_code=303)

    @router.get("/links/{link_id}", response_class=HTMLResponse)
    async def link_edit(request: Request, link_id: int) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        link = cms.get_nav_link(link_id)
        if not link:
            flash(request, "Ссылка не найдена")
            return RedirectResponse("/admin/links", status_code=302)
        return templates.TemplateResponse(
            request,
            "admin/link_edit.html",
            ctx(
                request,
                admin_section="links",
                link=link,
                error=None,
                form={
                    "label": link["label"],
                    "href": link["href"],
                    "location": link["location"],
                    "sort_order": link["sort_order"],
                    "is_visible": bool(link["is_visible"]),
                },
            ),
        )

    @router.post("/links/{link_id}", response_model=None)
    async def link_update(
        request: Request,
        link_id: int,
        label: str = Form(""),
        href: str = Form(""),
        location: str = Form("header"),
        sort_order: int = Form(100),
        is_visible: str | None = Form(None),
        csrf_token: str = Form(""),
    ) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        bad_csrf = ensure_csrf(request, csrf_token)
        if bad_csrf:
            return bad_csrf
        err = cms.update_nav_link(
            link_id,
            label=label,
            href=href,
            location=location,
            sort_order=sort_order,
            is_visible=is_visible is not None,
        )
        if err:
            return templates.TemplateResponse(
                request,
                "admin/link_edit.html",
                ctx(
                    request,
                    admin_section="links",
                    link=cms.get_nav_link(link_id),
                    error=err,
                    form={
                        "label": label,
                        "href": href,
                        "location": location,
                        "sort_order": sort_order,
                        "is_visible": is_visible is not None,
                    },
                ),
                status_code=400,
            )
        flash(request, "Ссылка сохранена")
        return RedirectResponse(f"/admin/links/{link_id}", status_code=303)

    @router.post("/links/{link_id}/delete", response_model=None)
    async def link_delete(
        request: Request, link_id: int, csrf_token: str = Form("")
    ) -> Any:
        denied = require_admin(request)
        if denied:
            return denied
        bad_csrf = ensure_csrf(request, csrf_token)
        if bad_csrf:
            return bad_csrf
        cms.delete_nav_link(link_id)
        flash(request, "Ссылка удалена")
        return RedirectResponse("/admin/links", status_code=303)

    return router
