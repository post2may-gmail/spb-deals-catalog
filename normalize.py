from __future__ import annotations

import re


def normalize_street(name: str) -> str:
    text = (name or "").strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text


def street_letter(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return "#"
    ch = text[0].upper().replace("Ё", "Е")
    if "А" <= ch <= "Я":
        return ch
    if ch.isdigit():
        return "0-9"
    return "#"


RUSSIAN_LETTERS = [
    "А",
    "Б",
    "В",
    "Г",
    "Д",
    "Е",
    "Ж",
    "З",
    "И",
    "К",
    "Л",
    "М",
    "Н",
    "О",
    "П",
    "Р",
    "С",
    "Т",
    "У",
    "Ф",
    "Х",
    "Ц",
    "Ч",
    "Ш",
    "Щ",
    "Э",
    "Ю",
    "Я",
]

ALPHABET = ["0-9", *RUSSIAN_LETTERS]
