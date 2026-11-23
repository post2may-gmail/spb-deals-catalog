"""Чистка кривых строк выгрузки Росреестра.

В открытых таблицах часто встречаются:
- площади целых корпусов / пакетов (тысячи и миллионы м²);
- цены 1 ₽ или десятки миллиардов;
- «город» = муниципальный округ;
- «улица» = территория предприятия / губа / км шоссе;
- год постройки вида «1975-1980», «до 1909-1913»;
- материал стен как длинный список через «;».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# Жилое помещение / комната / доля — реалистичный диапазон площади.
AREA_MIN = 8.0
AREA_MAX = 300.0

# Нижняя граница цены сделки (отсекает «1 рубль» и явный мусор).
PRICE_MIN = 100_000.0

# ₽/м² по СПб: ниже — заниженные/пакетные, выше — сорванные цифры.
PRICE_M2_MIN = 30_000.0
PRICE_M2_MAX = 700_000.0

_FAKE_STREET = re.compile(
    r"(территор|предприят|н\.?\s*губа|невская губа|км\s+\d|шоссе\s+санкт|"
    r"земельн|кадастров\w*\s+квартал|без\s+улиц)",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"(?:^|[^\d])((?:1[7-9]\d{2})|(?:20[0-2]\d))(?:[^\d]|$)")


@dataclass
class CleanResult:
    ok: bool
    row: dict[str, Any] | None = None
    reason: str | None = None


def normalize_city(city: str) -> str:
    text = (city or "").strip()
    if not text:
        return "Санкт-Петербург"
    low = text.lower().replace("ё", "е")
    if low.startswith("муниципальн") or "округ" in low and "санкт" not in low:
        return "Санкт-Петербург"
    if low in {"спб", "г. санкт-петербург", "город санкт-петербург", "петербург"}:
        return "Санкт-Петербург"
    # поселок Парголово → Парголово
    for prefix in ("поселок ", "посёлок ", "город ", "г. "):
        if low.startswith(prefix.replace("ё", "е")):
            return text[len(prefix) :].strip() or "Санкт-Петербург"
    return text


def normalize_year_build(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return text if 1700 <= year <= 2027 else ""
    m = _YEAR_RE.search(text)
    if not m:
        return ""
    year = int(m.group(1))
    return str(year) if 1700 <= year <= 2027 else ""


def normalize_floor(raw: str) -> str:
    text = " ".join((raw or "").strip().split())
    if not text:
        return ""
    low = text.lower().replace("ё", "е")
    aliases = {
        "мансардный этаж": "мансарда",
        "мансардный": "мансарда",
        "технический чердак": "чердак",
        "технический этаж": "техэтаж",
        "технический": "техэтаж",
        "мезонин-надстройка": "мезонин",
        "цокольный": "цоколь",
    }
    if low in aliases:
        return aliases[low]
    if low == "мансарда":
        return "мансарда"
    # «5, Антресоль 5 этажа» → «5»
    m = re.match(r"^(\d+)\s*,", text)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)\b", text)
    if m and len(text) > 4:
        # оставляем короткое описание, если это не просто число
        return text
    return text


def normalize_wall_material(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    parts: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[;|/]+", text):
        p = " ".join(part.strip().split())
        if not p:
            continue
        key = p.lower().replace("ё", "е")
        if key in seen:
            continue
        seen.add(key)
        parts.append(p)
    return "; ".join(parts)


def is_plausible_street(street: str) -> bool:
    text = (street or "").strip()
    if len(text) < 2:
        return False
    if _FAKE_STREET.search(text):
        return False
    return True


def clean_deal_row(row: dict[str, Any]) -> CleanResult:
    """Проверяет и нормализует одну сделку. Не меняет исходный dict."""
    try:
        area = float(str(row.get("area") or "0").replace(",", "."))
        price = float(str(row.get("deal_price") or "0").replace(",", "."))
    except (TypeError, ValueError):
        return CleanResult(False, reason="bad_number")

    if area < AREA_MIN or area > AREA_MAX:
        return CleanResult(False, reason="area")
    if price < PRICE_MIN:
        return CleanResult(False, reason="price_low")
    pm2 = price / area
    if pm2 < PRICE_M2_MIN or pm2 > PRICE_M2_MAX:
        return CleanResult(False, reason="price_m2")

    street = (row.get("street") or "").strip()
    if not is_plausible_street(street):
        return CleanResult(False, reason="street")

    cleaned = dict(row)
    cleaned["street"] = street
    cleaned["city"] = normalize_city(str(row.get("city") or ""))
    cleaned["wall_material"] = normalize_wall_material(str(row.get("wall_material") or ""))
    cleaned["year_build"] = normalize_year_build(str(row.get("year_build") or ""))
    cleaned["floor"] = normalize_floor(str(row.get("floor") or ""))
    cleaned["area"] = f"{area:.4f}".rstrip("0").rstrip(".")
    cleaned["deal_price"] = f"{price:.2f}".rstrip("0").rstrip(".")
    return CleanResult(True, row=cleaned)
