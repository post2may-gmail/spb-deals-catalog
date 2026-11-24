"""Пересобирает очищенный CSV.GZ и SQLite из текущей (грязной) выборки.

  python -m catalog.repair_dataset
"""

from __future__ import annotations

import csv
import gzip
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from clean import clean_deal_row
from db import CSV_GZ, CSV_PLAIN, rebuild_from_csv
from import_data import OUT_FIELDS
from street_canon import canonicalize_street


def repair(source: Path | None = None) -> tuple[int, int, Counter[str]]:
    src = source or (CSV_GZ if CSV_GZ.exists() else CSV_PLAIN)
    if src is None or not src.exists():
        raise FileNotFoundError("Нет исходного CSV/CSV.GZ для очистки")

    opener = gzip.open if src.suffix == ".gz" else open
    kept = 0
    total = 0
    reasons: Counter[str] = Counter()

    CSV_PLAIN.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", delete=False, dir=str(CSV_PLAIN.parent)
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=OUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        with opener(src, "rt", encoding="utf-8") as fh:  # type: ignore[arg-type]
            for row in csv.DictReader(fh):
                total += 1
                result = clean_deal_row(row)
                if not result.ok or result.row is None:
                    reasons[result.reason or "unknown"] += 1
                    continue
                out = {k: result.row.get(k, "") for k in OUT_FIELDS}
                if not out.get("currency"):
                    out["currency"] = "рубль"
                out["street"] = canonicalize_street(
                    out.get("street") or "",
                    out.get("quarter_cad_number") or "",
                )
                writer.writerow(out)
                kept += 1
        tmp_path = Path(tmp.name)

    shutil.move(str(tmp_path), CSV_PLAIN)
    with CSV_PLAIN.open("rb") as plain, gzip.open(CSV_GZ, "wb", compresslevel=9) as gz:
        gz.write(plain.read())
    rebuild_from_csv(CSV_GZ)
    return kept, total, reasons


def main() -> None:
    kept, total, reasons = repair()
    print(f"Очищено: {kept} из {total} (отброшено {total - kept})")
    for reason, n in reasons.most_common():
        print(f"  {reason}: {n}")
    print(f"Записано → {CSV_GZ}")


if __name__ == "__main__":
    main()
