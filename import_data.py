"""Импорт таблиц Росреестра → catalog/data/processed/spb_living_premises.csv(.gz).

Примеры:
  python -m catalog.import_data /path/to/*.xlsx
  python -m catalog.import_data /path/to/folder --rebuild-db
"""

from __future__ import annotations

import argparse
import csv
import gzip
import sys
from pathlib import Path
from typing import Any, Iterable

from clean import clean_deal_row
from db import CSV_GZ, CSV_PLAIN, rebuild_from_csv
from normalize import normalize_street
from street_canon import canonicalize_street

# Возможные заголовки в выгрузках Росреестра / Excel с компьютера пользователя
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "city": ("city", "город", "населенный пункт", "населённый пункт", "муниципальное образование"),
    "district": ("district", "район", "муниципальный район"),
    "street": ("street", "улица", "адрес", "наименование улицы"),
    "quarter_cad_number": (
        "quarter_cad_number",
        "кадастровый квартал",
        "кадастровый номер квартала",
        "квартал",
    ),
    "realestate_type": (
        "realestate_type",
        "realestate_type_code",
        "тип объекта",
        "вид объекта недвижимости",
        "объект",
    ),
    "purpose": ("purpose", "purpose_code", "вид объекта", "назначение", "назначение объекта"),
    "wall_material": (
        "wall_material",
        "wall_material_code",
        "материал стен",
        "материал дома",
        "материал наружных стен",
        "стены",
    ),
    "year_build": ("year_build", "год постройки", "год завершения строительства", "год"),
    "floor": ("floor", "этаж"),
    "area": ("area", "площадь", "площадь объекта", "площадь, м2", "площадь м2"),
    "deal_price": (
        "deal_price",
        "цена",
        "цена сделки",
        "стоимость",
        "цена договора",
        "цена, руб",
    ),
    "period_start_date": (
        "period_start_date",
        "period_start",
        "дата",
        "период",
        "дата начала периода",
        "квартал сделки",
    ),
    "doc_type": (
        "doc_type",
        "тип документа",
        "вид договора",
        "документ",
        "договор",
    ),
    "region_code": ("region_code", "код региона", "субъект", "регион"),
}


OUT_FIELDS = [
    "period_start_date",
    "city",
    "district",
    "street",
    "quarter_cad_number",
    "realestate_type",
    "purpose",
    "wall_material",
    "year_build",
    "floor",
    "area",
    "deal_price",
    "currency",
    "doc_type",
]

# Коды из открытых выгрузок Росреестра (CSV с разделителем «~»).
REALESTATE_TYPE_CODES: dict[str, str] = {
    "002001001000": "Земельный участок",
    "002001002000": "Здание",
    "002001003000": "Помещения",
    "002001004000": "Сооружение",
    "002001009000": "Машино-место",
}

PURPOSE_CODES: dict[str, str] = {
    "206002000000": "Жилое",
    "206001000000": "Нежилое",
    "204002000000": "Жилое",
    "204001000000": "Нежилое",
    "204003000000": "Многоквартирный дом",
}

WALL_MATERIAL_CODES: dict[str, str] = {
    "061001001000": "Каменные",
    "061001001001": "Кирпичные",
    "061001001002": "Кирпичные облегченные",
    "061001001003": "Из природного камня",
    "061001002000": "Деревянные",
    "061001002001": "Рубленые",
    "061001002002": "Каркасно-засыпные",
    "061001002003": "Каркасно-обшивные",
    "061001002004": "Сборно-щитовые",
    "061001002005": "Дощатые",
    "061001002006": "Деревянный каркас без обшивки",
    "061001003000": "Смешанные",
    "061001003001": "Каменные и деревянные",
    "061001003002": "Каменные и бетонные",
    "061001004000": "Легкие из местных материалов",
    "061001005000": "Из прочих материалов",
    "061001006000": "Бетонные",
    "061001006001": "Монолитные",
    "061001006002": "Из мелких бетонных блоков",
    "061001006003": "Из легкобетонных панелей",
    "061001007000": "Железобетонные",
    "061001007001": "Крупнопанельные",
    "061001007002": "Каркасно-панельные",
    "061001007003": "Монолитные",
    "061001007004": "Крупноблочные",
    "061001007005": "Из унифицированных железобетонных элементов",
    "061001007006": "Из железобетонных сегментов",
    "061001008000": "Шлакобетонные",
    "061001009000": "Металлические",
    "061001999000": "Иное",
}


def _decode_code(raw: str, table: dict[str, str]) -> str:
    text = (raw or "").strip().strip('"')
    if not text:
        return ""
    if not text.replace(".", "").isdigit() and ";" not in text:
        return text
    parts: list[str] = []
    for part in text.split(";"):
        code = part.strip().strip('"')
        if not code:
            continue
        parts.append(table.get(code, code))
    return "; ".join(parts)


def _norm_header(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("ё", "е").split())


def _map_headers(headers: Iterable[str]) -> dict[str, str]:
    """canonical -> original header"""
    by_norm = {_norm_header(h): h for h in headers if h}
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = _norm_header(alias)
            if key in by_norm:
                mapping[canonical] = by_norm[key]
                break
    return mapping


def _cell(row: dict[str, Any], mapping: dict[str, str], key: str) -> str:
    src = mapping.get(key)
    if not src:
        return ""
    val = row.get(src)
    if val is None:
        return ""
    return str(val).strip()


def _is_spb(row: dict[str, Any], mapping: dict[str, str]) -> bool:
    region = _cell(row, mapping, "region_code").replace(".0", "")
    if region in {"78", "078"}:
        return True
    if region and region not in {"78", "078"}:
        return False
    city = _cell(row, mapping, "city").lower().replace("ё", "е")
    if not city:
        # Файл уже отфильтрован пользователем / без колонки города.
        return True
    markers = (
        "санкт-петербург",
        "санкт петербург",
        "г. санкт-петербург",
        "спб",
        "петербург",
    )
    if any(m in city for m in markers):
        return True
    satellites = {
        "колпино",
        "пушкин",
        "петергоф",
        "петродворец",
        "сестрорецк",
        "кронштадт",
        "ломоносов",
        "павловск",
        "стрельна",
        "парголово",
        "шушары",
        "красное село",
        "металлострой",
        "лисий нос",
        "усть-славянка",
    }
    return city in satellites


def _is_sale(doc: str) -> bool:
    d = doc.lower().replace("ё", "е")
    if not d:
        return True
    if any(x in d for x in ("аренда", "найм", "наем")):
        return False
    return True


def _is_living_premise(row: dict[str, Any], mapping: dict[str, str]) -> bool:
    retype_raw = _cell(row, mapping, "realestate_type").strip().strip('"')
    purpose_raw = _cell(row, mapping, "purpose").strip().strip('"')
    retype = _decode_code(retype_raw, REALESTATE_TYPE_CODES).lower().replace("ё", "е")
    purpose = _decode_code(purpose_raw, PURPOSE_CODES).lower().replace("ё", "е")
    # Коды выгрузки: помещение + жилое
    if retype_raw == "002001003000" and purpose_raw == "206002000000":
        return True
    # Предпочтительный случай: тип «Помещения» + назначение «Жилое»
    if "помещ" in retype and purpose in {"жилое", "жилое помещение"}:
        return True
    if purpose in {"жилое", "жилое помещение"} and (
        not retype or "помещ" in retype or "квартир" in retype or "комнат" in retype
    ):
        return True
    if any(k in purpose for k in ("квартир", "комнат", "доля", "долев")):
        return True
    if "нежил" in purpose:
        return False
    return False


def _to_float(raw: str) -> float | None:
    text = (raw or "").replace("\xa0", " ").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _iter_rows_from_csv(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        # Выгрузки Росреестра часто идут с «~» вместо запятой.
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;~\t")
        except csv.Error:
            dialect = csv.excel
            if sample.count("~") > sample.count(","):
                dialect.delimiter = "~"
        reader = csv.DictReader(fh, dialect=dialect)
        for row in reader:
            yield dict(row)


def _iter_rows_from_xlsx(path: Path) -> Iterable[dict[str, Any]]:
    """Читает Excel Росреестра, пропуская пустые/служебные колонки (сдвиг сетки)."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Для Excel нужен openpyxl: pip install openpyxl"
        ) from exc
    # read_only=False нужен, чтобы видеть merged-ячейки и не «расползшуюся» сетку
    wb = load_workbook(path, read_only=False, data_only=True)
    ws = wb.active
    # снимаем объединения — иначе значения только в первой ячейке, остальные None
    merged = list(ws.merged_cells.ranges)
    for cell_range in merged:
        ws.unmerge_cells(str(cell_range))

    rows_iter = ws.iter_rows(values_only=True)
    raw_headers = next(rows_iter)
    headers: list[str] = []
    keep_idx: list[int] = []
    seen_names: set[str] = set()
    for i, h in enumerate(raw_headers):
        name = str(h).strip() if h is not None else ""
        if not name or name.lower().startswith("unnamed"):
            continue
        # дубли заголовков после сдвига колонок
        key = name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        headers.append(name)
        keep_idx.append(i)

    for values in rows_iter:
        if values is None:
            continue
        row = {headers[j]: values[keep_idx[j]] for j in range(len(headers)) if keep_idx[j] < len(values)}
        if any(v is not None and str(v).strip() for v in row.values()):
            yield row


def iter_source_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.glob("**/*.{csv,CSV,xlsx,XLSX}")))
            files.extend(sorted(p.rglob("*.csv")))
            files.extend(sorted(p.rglob("*.xlsx")))
        elif p.is_file():
            files.append(p)
    # unique preserve order
    seen: set[Path] = set()
    out: list[Path] = []
    for f in files:
        rp = f.resolve()
        if rp in seen:
            continue
        if f.suffix.lower() not in {".csv", ".xlsx"}:
            continue
        seen.add(rp)
        out.append(f)
    return out


def filter_row(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, str] | None:
    if not _is_spb(row, mapping):
        # если в файле нет города/региона — пропускаем только явно чужие
        city = _cell(row, mapping, "city")
        region = _cell(row, mapping, "region_code")
        if city or region:
            return None
    if not _is_living_premise(row, mapping):
        return None
    doc = _cell(row, mapping, "doc_type")
    if not _is_sale(doc):
        return None
    street = _cell(row, mapping, "street")
    if not street or not normalize_street(street):
        return None
    area = _to_float(_cell(row, mapping, "area"))
    price = _to_float(_cell(row, mapping, "deal_price"))
    if area is None or price is None or area <= 0 or price <= 0:
        return None
    city = _cell(row, mapping, "city") or "Санкт-Петербург"
    period = _cell(row, mapping, "period_start_date")
    retype = _decode_code(_cell(row, mapping, "realestate_type"), REALESTATE_TYPE_CODES)
    purpose = _decode_code(_cell(row, mapping, "purpose"), PURPOSE_CODES)
    walls = _decode_code(_cell(row, mapping, "wall_material"), WALL_MATERIAL_CODES)
    quarter_cad = _cell(row, mapping, "quarter_cad_number")
    street = canonicalize_street(street, quarter_cad)
    candidate = {
        "period_start_date": period,
        "city": city,
        "district": _cell(row, mapping, "district"),
        "street": street,
        "quarter_cad_number": quarter_cad,
        "realestate_type": retype or "Помещения",
        "purpose": purpose or "Жилое",
        "wall_material": walls,
        "year_build": _cell(row, mapping, "year_build"),
        "floor": _cell(row, mapping, "floor"),
        "area": str(area),
        "deal_price": str(price),
        "currency": "рубль",
        "doc_type": (doc or "ДКП").upper(),
    }
    cleaned = clean_deal_row(candidate)
    return cleaned.row if cleaned.ok else None


def _write_processed(rows: Iterable[dict[str, str]]) -> int:
    CSV_PLAIN.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with CSV_PLAIN.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=OUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in OUT_FIELDS})
            kept += 1
    with CSV_PLAIN.open("rb") as src, gzip.open(CSV_GZ, "wb", compresslevel=9) as dst:
        dst.write(src.read())
    return kept


def _load_existing_rows() -> list[dict[str, str]]:
    src = CSV_GZ if CSV_GZ.exists() else CSV_PLAIN
    if not src.exists():
        return []
    opener = gzip.open if src.suffix == ".gz" else open
    with opener(src, "rt", encoding="utf-8", newline="") as fh:  # type: ignore[arg-type]
        return [dict(row) for row in csv.DictReader(fh)]


def _row_key(row: dict[str, str]) -> tuple[str, ...]:
    return (
        (row.get("period_start_date") or "").strip(),
        (row.get("street") or "").strip().lower(),
        (row.get("quarter_cad_number") or "").strip(),
        (row.get("area") or "").strip(),
        (row.get("deal_price") or "").strip(),
        (row.get("floor") or "").strip(),
        (row.get("doc_type") or "").strip().upper(),
    )


def import_files(
    paths: list[Path], rebuild_db: bool = True, append: bool = False
) -> int:
    files = iter_source_files(paths)
    if not files:
        raise SystemExit("Не найдены CSV/XLSX файлы")

    existing = _load_existing_rows() if append else []
    seen = {_row_key(r) for r in existing}
    merged: list[dict[str, str]] = list(existing)
    added = 0

    for path in files:
        print(f"Читаю {path} …", flush=True)
        rows = (
            _iter_rows_from_xlsx(path)
            if path.suffix.lower() == ".xlsx"
            else _iter_rows_from_csv(path)
        )
        mapping: dict[str, str] | None = None
        file_kept = 0
        for row in rows:
            if mapping is None:
                mapping = _map_headers(row.keys())
                missing = [k for k in ("street", "area", "deal_price") if k not in mapping]
                if missing:
                    print(f"  предупреждение: не найдены колонки {missing}", flush=True)
            filtered = filter_row(row, mapping or {})
            if filtered is None:
                continue
            key = _row_key(filtered)
            if key in seen:
                continue
            seen.add(key)
            merged.append(filtered)
            file_kept += 1
            added += 1
        print(f"  → {file_kept} новых жилых помещений (продажа, СПб)", flush=True)

    total = _write_processed(merged)
    print(
        f"Сохранено {total} строк (+{added} новых) → {CSV_PLAIN} и {CSV_GZ}",
        flush=True,
    )

    if rebuild_db:
        n = rebuild_from_csv(CSV_GZ)
        print(f"База пересобрана: {n} сделок", flush=True)
    return added


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Импорт таблиц Росреестра для каталога СПб")
    parser.add_argument("paths", nargs="+", type=Path, help="Файлы или папки с CSV/XLSX")
    parser.add_argument("--rebuild-db", action="store_true", default=True)
    parser.add_argument("--no-rebuild-db", action="store_true")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Добавить к существующему processed CSV (не перезаписывать)",
    )
    args = parser.parse_args(argv)
    rebuild = not args.no_rebuild_db
    import_files(args.paths, rebuild_db=rebuild, append=args.append)


if __name__ == "__main__":
    main(sys.argv[1:])
