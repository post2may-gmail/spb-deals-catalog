"""Канонические имена улиц: слияние дублей и разведение одноимённых.

Правила нужны, потому что в выгрузке Росреестра тип улицы часто отброшен,
а одинаковые названия встречаются в разных районах / кадастровых кварталах.
"""

from __future__ import annotations

import re

# Кадастровые районы СПб: 78:31 Центральный, 78:37 Колпинский, 78:42 Пушкинский (Шушары).
_TVERSKAYA_KOLPINO_PREFIXES = ("78:37:",)
_PUSHKINSKAYA_SHUSHARY_PREFIXES = ("78:42:",)
_PUSHKINSKAYA_KOLPINO_PREFIXES = ("78:37:",)
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
_PAREN = re.compile(r"\s*\([^)]*\)\s*")

# Слияние вариантов написания / уточнений в скобках → канон каталога.
_MERGE_BY_CORE: dict[str, str] = {
    "непокоренных": "Непокоренных",
    "английский": "Английский",
    "переведенская": "Переведенская",
    "пискаревский": "Пискаревский",
    "полоцкая": "Полоцкая",
    "политрука пасечника": "Политрука Пасечника",
    "приозерское": "Приозерское",
    "пугачева": "Пугачева",
    "анциферовская": "Анциферовская",
    "большая озерная": "Большая Озерная",
    "бородачева": "Бородачева",
    "взлетная": "Взлетная",
    "героев": "Героев",
    "грибалевой": "Грибалевой",
    "долгоозерная": "Долгоозерная",
}


def _norm_key(name: str) -> str:
    return " ".join((name or "").strip().lower().replace("ё", "е").split())


def _base_without_type(name: str) -> str:
    text = " ".join((name or "").strip().split())
    return _TYPE_SUFFIX.sub("", text).strip() or text


def _core_name(name: str) -> str:
    """Имя без типа улицы и без уточнения в скобках."""
    text = _PAREN.sub(" ", name or "")
    return _norm_key(_base_without_type(text))


def canonicalize_street(street: str, quarter_cad_number: str = "") -> str:
    """Возвращает каноническое имя улицы для каталога и поиска."""
    name = " ".join((street or "").strip().split())
    if not name:
        return name

    key = _norm_key(name)
    base = _norm_key(_base_without_type(name))
    core = _core_name(name)
    quarter = (quarter_cad_number or "").strip()

    # Тверская: Центр (78:31) vs Колпино (78:37).
    if core == "тверская":
        if any(quarter.startswith(p) for p in _TVERSKAYA_KOLPINO_PREFIXES):
            return "Тверская (Колпино)"
        if "колпино" in key:
            return "Тверская (Колпино)"
        return "Тверская"

    # Пушкинская: Центр (78:31) vs Шушары/Пушкинский (78:42) vs Колпино (78:37).
    if core == "пушкинская":
        if "шушар" in key or any(
            quarter.startswith(p) for p in _PUSHKINSKAYA_SHUSHARY_PREFIXES
        ):
            return "Пушкинская (Шушары)"
        if "колпино" in key or any(
            quarter.startswith(p) for p in _PUSHKINSKAYA_KOLPINO_PREFIXES
        ):
            return "Пушкинская (Колпино)"
        return "Пушкинская"

    # Заводская: несколько одноимённых улиц в разных районах.
    # 78:12 Усть-Славянка, 78:36 Парголово, 78:37 Понтонный, 78:38 Песочный.
    if core == "заводская":
        if "усть" in key or "слав" in key or quarter.startswith("78:12:"):
            return "Заводская (Усть-Славянка)"
        if "песочн" in key or quarter.startswith("78:38:"):
            return "Заводская (Песочный)"
        if "парголов" in key or quarter.startswith("78:36:"):
            return "Заводская (Парголово)"
        if "понтон" in key or quarter.startswith("78:37:"):
            return "Заводская (Понтонный)"
        return "Заводская"

    # Аврова: площадь и улица в Петергофе — разные топонимы.
    if core == "аврова":
        if "площад" in key:
            return "Аврова площадь"
        if "улиц" in key:
            return "Аврова"
        if any(quarter.startswith(q) for q in _AVROVA_SQUARE_QUARTERS):
            return "Аврова площадь"
        if any(quarter.startswith(q) for q in _AVROVA_STREET_QUARTERS):
            return "Аврова"
        return "Аврова"

    # Простые слияния дублей (ё/е, скобки, с типом / без типа).
    merged = _MERGE_BY_CORE.get(core)
    if merged:
        return merged

    # Совместимость со старыми ключами до _core_name.
    if base == "непокоренных" or key in {
        "непокоренных",
        "непокоренных проспект",
        "непокорённых",
        "непокорённых проспект",
    }:
        return "Непокоренных"
    if base == "английский" or key in {"английский", "английский проспект"}:
        return "Английский"

    return name
