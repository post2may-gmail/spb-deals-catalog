from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_TITLES_PATH = Path(__file__).resolve().parent / "data" / "street_titles.json"

_STREET_TYPES = (
    "проспект",
    "переулок",
    "улица",
    "площадь",
    "набережная",
    "бульвар",
    "шоссе",
    "аллея",
    "проезд",
    "тупик",
    "линия",
    "дорога",
    "спуск",
    "канал",
    "остров",
    "мост",
)


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    if not _TITLES_PATH.exists():
        return {}
    try:
        data = json.loads(_TITLES_PATH.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=1)
def _title_to_street() -> dict[str, str]:
    rev: dict[str, str] = {}
    for street, title in _load().items():
        key = " ".join(title.lower().replace("ё", "е").split())
        rev[key] = street
    return rev


def street_title(street: str) -> str:
    """Полное название с типом: «Невский проспект», иначе исходное имя."""
    name = (street or "").strip()
    if not name:
        return name
    return _load().get(name, name)


def strip_street_type(name: str) -> str:
    """«Тургеневский переулок» → «Тургеневский»."""
    text = " ".join((name or "").strip().split())
    if not text:
        return text
    low = text.lower().replace("ё", "е")
    for kind in _STREET_TYPES:
        suffix = f" {kind}"
        if low.endswith(suffix):
            return text[: -len(suffix)].strip(" ,.-")
        prefix = f"{kind} "
        if low.startswith(prefix):
            return text[len(prefix) :].strip(" ,.-")
    # «пр. Невский», «ул. Таврическая»
    m = re.match(
        r"^(?:ул|улица|пр|просп|проспект|пер|переулок|пл|наб|б-р|бульвар)\.?\s+(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(" ,.-")
    return text


def street_base_name(query: str) -> str:
    """Имя улицы без типа: по справочнику или срезанием суффикса."""
    text = " ".join((query or "").strip().split())
    if not text:
        return text
    key = text.lower().replace("ё", "е")
    mapped = _title_to_street().get(key)
    if mapped:
        return mapped
    return strip_street_type(text)
