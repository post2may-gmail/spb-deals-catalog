"""Канонические имена улиц: слияние дублей и разведение одноимённых.

Правила нужны, потому что в выгрузке Росреестра тип улицы часто отброшен,
а одинаковые названия встречаются в разных районах / кадастровых кварталах.
"""

from __future__ import annotations

import re

# Кадастровые районы СПб: 78:31 Центральный, 78:37 Колпинский.
_TVERSKAYA_KOLPINO_PREFIXES = ("78:37:",)
# Кварталы площади Аврова vs улицы Аврова (Петергоф, 78:40).
_AVROVA_SQUARE_QUARTERS = (
    "78:40:0019249",
    "78:40:0019251",
)
_AVROVA_STREET_QUARTERS = (
    "78:40:0019284",
    "78:40:0019259",
)

_TYPE_SUFFIX = re.compile(
    r"\s+(?:проспект|улица|площадь|переулок|набережная|бульвар|шоссе|"
    r"аллея|проезд|дорога)$",
    re.IGNORECASE,
)


def _norm_key(name: str) -> str:
    return " ".join((name or "").strip().lower().replace("ё", "е").split())


def _base_without_type(name: str) -> str:
    text = " ".join((name or "").strip().split())
    return _TYPE_SUFFIX.sub("", text).strip() or text


def canonicalize_street(street: str, quarter_cad_number: str = "") -> str:
    """Возвращает каноническое имя улицы для каталога и поиска."""
    name = " ".join((street or "").strip().split())
    if not name:
        return name

    key = _norm_key(name)
    base = _norm_key(_base_without_type(name))
    quarter = (quarter_cad_number or "").strip()

    # Непокоренных / Непокорённых → одна улица.
    if base == "непокоренных" or key in {
        "непокоренных",
        "непокоренных проспект",
        "непокорённых",
        "непокорённых проспект",
    }:
        return "Непокоренных"

    # Английский проспект: слить «Английский» и «Английский проспект».
    # «Английская» (дорога) не трогаем.
    if base == "английский" or key in {"английский", "английский проспект"}:
        return "Английский"

    # Тверская: Центр (78:31) vs Колпино (78:37).
    if base == "тверская" or key in {"тверская", "тверская улица"}:
        if any(quarter.startswith(p) for p in _TVERSKAYA_KOLPINO_PREFIXES):
            return "Тверская (Колпино)"
        # Уже размеченная Колпино-версия.
        if "колпино" in key:
            return "Тверская (Колпино)"
        return "Тверская"

    # Аврова: площадь и улица в Петергофе — разные топонимы.
    if base in {"аврова", "аврова площадь", "аврова улица"} or key in {
        "аврова",
        "аврова площадь",
        "аврова улица",
    }:
        if "площад" in key:
            return "Аврова площадь"
        if "улиц" in key:
            return "Аврова"
        if any(quarter.startswith(q) for q in _AVROVA_SQUARE_QUARTERS):
            return "Аврова площадь"
        if any(quarter.startswith(q) for q in _AVROVA_STREET_QUARTERS):
            return "Аврова"
        # Без квартала по умолчанию — улица (длинный линейный топоним).
        return "Аврова"

    return name
