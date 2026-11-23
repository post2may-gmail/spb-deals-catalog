"""SEO helpers: канонические URL, Open Graph, JSON-LD."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

from fastapi import Request


BRAND = "Цены сделок на жилье в Санкт-Петербурге"
DEFAULT_DESCRIPTION = (
    "Цены реальных сделок с квартирами, комнатами и долями в Санкт-Петербурге "
    "по открытым данным Росреестра — поиск по улице и кварталам."
)


def public_base_url(request: Request) -> str:
    """Канонический origin только из env или из ASGI base_url — без X-Forwarded-*."""
    for name in ("SITE_BASE_URL", "PUBLIC_BASE_URL"):
        env = (os.getenv(name) or "").strip().rstrip("/")
        if env:
            return env
    # request.base_url берётся из ASGI scope сервера, не из клиентских
    # X-Forwarded-Host / X-Forwarded-Proto / Host (их не читаем намеренно).
    return str(request.base_url).rstrip("/")


def abs_url(request: Request, path: str = "/") -> str:
    base = public_base_url(request)
    if not path:
        return base + "/"
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def street_path(street: str) -> str:
    return f"/street/{quote(street, safe='')}"


def dumps_ld(data: dict[str, Any] | list[Any]) -> str:
    from security import safe_json_ld

    return safe_json_ld(data)


def website_ld(
    request: Request,
    *,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    base = public_base_url(request)
    site_name = name or BRAND
    site_description = description or DEFAULT_DESCRIPTION
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": site_name,
        "url": base + "/",
        "inLanguage": "ru-RU",
        "description": site_description,
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": base + "/search?street={search_term_string}",
            },
            "query-input": "required name=search_term_string",
        },
    }


def dataset_ld(
    request: Request,
    deals: int,
    streets: int,
    *,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": name or "Сделки с жильём в Санкт-Петербурге (Росреестр)",
        "description": description or DEFAULT_DESCRIPTION,
        "url": public_base_url(request) + "/",
        "creator": {"@type": "Organization", "name": "Росреестр"},
        "license": "https://rosreestr.gov.ru/",
        "spatialCoverage": {
            "@type": "Place",
            "name": "Санкт-Петербург, Россия",
        },
        "variableMeasured": [
            "цена сделки",
            "площадь",
            "цена за м²",
            "материал дома",
            "год постройки",
        ],
        "isAccessibleForFree": True,
        "measurementTechnique": "Открытые данные Росреестра о ценах регистрируемых сделок",
        "numberOfItems": deals,
        "additionalProperty": [
            {
                "@type": "PropertyValue",
                "name": "streets",
                "value": streets,
            }
        ],
    }


def breadcrumb_ld(
    request: Request, items: list[tuple[str, str]]
) -> dict[str, Any]:
    """items: list of (name, path)."""
    elements = []
    for i, (name, path) in enumerate(items, start=1):
        elements.append(
            {
                "@type": "ListItem",
                "position": i,
                "name": name,
                "item": abs_url(request, path),
            }
        )
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": elements,
    }


def street_page_ld(
    request: Request,
    *,
    street: str,
    street_display: str,
    deals_count: int,
) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": f"Цены сделок на {street_display} в Санкт-Петербурге",
        "description": (
            f"Зарегистрированные сделки с жильём на {street_display} "
            f"в Санкт-Петербурге по данным Росреестра. В выборке {deals_count} сделок."
        ),
        "url": abs_url(request, street_path(street)),
        "isPartOf": {"@type": "WebSite", "name": BRAND, "url": public_base_url(request) + "/"},
        "about": {
            "@type": "Place",
            "name": f"{street_display}, Санкт-Петербург",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Санкт-Петербург",
                "addressCountry": "RU",
                "streetAddress": street_display,
            },
        },
        "inLanguage": "ru-RU",
    }
