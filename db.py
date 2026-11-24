from __future__ import annotations

import csv
import gzip
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

from clean import clean_deal_row
from normalize import normalize_street, street_letter
from periods import Quarter, available_quarters
from street_canon import canonicalize_street

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
PACKAGED_DB = DATA_DIR / "deals.db"
PACKAGED_DB_GZ = DATA_DIR / "deals.db.gz"
CSV_GZ = DATA_DIR / "processed" / "spb_living_premises.csv.gz"
CSV_PLAIN = DATA_DIR / "processed" / "spb_living_premises.csv"

# На хостинге каталог приложения часто только для чтения — рабочая БД в /tmp.
DB_PATH = Path(tempfile.gettempdir()) / "spb_deals.db"

_lock = threading.Lock()
_ready = False


def _connect(path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    db_path = path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if readonly and db_path.exists():
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def _period_yq(period_start: str) -> tuple[int, int]:
    q = Quarter.from_period_start(period_start)
    if q is None:
        return 0, 0
    return q.year, q.quarter


def _count_deals(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        conn = _connect(path, readonly=True)
        n = int(conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0])
        conn.close()
        return n
    except sqlite3.Error:
        return 0


def rebuild_from_csv(csv_path: Path | None = None, db_path: Path | None = None) -> int:
    """Создаёт SQLite из CSV/CSV.GZ. Возвращает число сделок."""
    global DB_PATH
    path = csv_path
    if path is None:
        if CSV_GZ.exists():
            path = CSV_GZ
        elif CSV_PLAIN.exists():
            path = CSV_PLAIN
        else:
            raise FileNotFoundError(
                "Нет файла данных: deals_site/data/processed/spb_living_premises.csv.gz"
            )

    target = db_path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    conn = _connect(target, readonly=False)
    conn.executescript(
        """
        CREATE TABLE deals (
          id INTEGER PRIMARY KEY,
          period_start TEXT NOT NULL,
          year INTEGER NOT NULL,
          quarter INTEGER NOT NULL,
          street TEXT NOT NULL,
          street_norm TEXT NOT NULL,
          street_letter TEXT NOT NULL,
          wall_material TEXT,
          year_build TEXT,
          floor TEXT,
          area REAL NOT NULL,
          deal_price REAL NOT NULL,
          price_m2 REAL
        );
        CREATE TABLE streets (
          street TEXT PRIMARY KEY,
          street_norm TEXT NOT NULL,
          street_letter TEXT NOT NULL,
          deals_count INTEGER NOT NULL
        );
        CREATE INDEX idx_deals_street_norm ON deals(street_norm);
        CREATE INDEX idx_deals_letter ON deals(street_letter);
        CREATE INDEX idx_deals_period ON deals(period_start);
        CREATE INDEX idx_streets_letter ON streets(street_letter);
        CREATE INDEX idx_streets_norm ON streets(street_norm);
        """
    )

    opener = gzip.open if path.suffix == ".gz" else open
    street_counts: dict[str, int] = {}
    batch: list[tuple[Any, ...]] = []
    total = 0

    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[arg-type]
        reader = csv.DictReader(fh)
        for row in reader:
            cleaned = clean_deal_row(row)
            if not cleaned.ok or cleaned.row is None:
                continue
            row = cleaned.row
            area = float(row["area"])
            price = float(row["deal_price"])
            street = canonicalize_street(
                row["street"],
                row.get("quarter_cad_number") or row.get("quarter_cad") or "",
            )
            period = (row.get("period_start_date") or row.get("period_start") or "").strip()
            year, quarter = _period_yq(period)
            sn = normalize_street(street)
            letter = street_letter(street)
            street_counts[street] = street_counts.get(street, 0) + 1
            batch.append(
                (
                    period,
                    year,
                    quarter,
                    street,
                    sn,
                    letter,
                    (row.get("wall_material") or "").strip(),
                    (row.get("year_build") or "").strip(),
                    (row.get("floor") or "").strip(),
                    area,
                    price,
                    round(price / area, 2),
                )
            )
            if len(batch) >= 5000:
                conn.executemany(
                    """
                    INSERT INTO deals(
                      period_start, year, quarter, street, street_norm,
                      street_letter, wall_material, year_build, floor,
                      area, deal_price, price_m2
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    batch,
                )
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                """
                INSERT INTO deals(
                  period_start, year, quarter, street, street_norm,
                  street_letter, wall_material, year_build, floor,
                  area, deal_price, price_m2
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                batch,
            )
            total += len(batch)

    conn.executemany(
        "INSERT INTO streets(street, street_norm, street_letter, deals_count) VALUES (?,?,?,?)",
        [
            (name, normalize_street(name), street_letter(name), count)
            for name, count in street_counts.items()
        ],
    )
    conn.commit()
    conn.close()
    DB_PATH = target
    logger.info("sqlite ready at %s (%s deals)", target, total)
    return total


def _materialize_packaged_db(target: Path) -> bool:
    """Быстро кладёт готовую SQLite в /tmp (без парсинга CSV)."""
    if _count_deals(target) > 0:
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    if PACKAGED_DB_GZ.exists() and PACKAGED_DB_GZ.stat().st_size > 0:
        logger.info("unpacking %s -> %s", PACKAGED_DB_GZ, target)
        with gzip.open(PACKAGED_DB_GZ, "rb") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return _count_deals(target) > 0
    if PACKAGED_DB.exists() and PACKAGED_DB.stat().st_size > 0:
        logger.info("copying %s -> %s", PACKAGED_DB, target)
        shutil.copy2(PACKAGED_DB, target)
        return _count_deals(target) > 0
    return False


def ensure_db() -> None:
    global _ready, DB_PATH
    if _ready and _count_deals(DB_PATH) > 0:
        return
    with _lock:
        if _ready and _count_deals(DB_PATH) > 0:
            return

        tmp_db = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "spb_deals.db"

        # 1) уже собранная БД в /tmp
        if _count_deals(tmp_db) > 0:
            DB_PATH = tmp_db
            _ready = True
            return

        # 2) упакованная SQLite (предпочтительно — быстрый cold start на serverless)
        if _materialize_packaged_db(tmp_db):
            DB_PATH = tmp_db
            _ready = True
            return

        # 3) запасной путь: собрать из CSV.gz (только если положили вручную)
        if CSV_GZ.exists() or CSV_PLAIN.exists():
            n = rebuild_from_csv(db_path=tmp_db)
            if n <= 0:
                raise RuntimeError("Не удалось загрузить сделки: пустой датасет")
            DB_PATH = tmp_db
            _ready = True
            return

        raise RuntimeError(
            "Нет данных сделок: положите data/deals.db.gz (или CSV в data/processed/)"
        )


class DealsRepository:
    def __init__(self) -> None:
        ensure_db()
        self._conn = _connect(DB_PATH, readonly=True)

    def stats(self) -> dict[str, Any]:
        deals = self._conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        streets = self._conn.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
        return {"deals": deals, "streets": streets}

    def quarters(self) -> list[Quarter]:
        rows = self._conn.execute(
            "SELECT DISTINCT period_start FROM deals ORDER BY period_start"
        ).fetchall()
        return available_quarters([r["period_start"] for r in rows])

    def letters_with_counts(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT street_letter AS letter, COUNT(*) AS streets_count, SUM(deals_count) AS deals_count
            FROM streets
            GROUP BY street_letter
            ORDER BY CASE WHEN street_letter = '0-9' THEN 0 ELSE 1 END, street_letter
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def streets_by_letter(self, letter: str) -> list[dict[str, Any]]:
        letter = (letter or "").strip().upper().replace("Ё", "Е")
        if letter == "0":
            letter = "0-9"
        rows = self._conn.execute(
            """
            SELECT street, deals_count
            FROM streets
            WHERE street_letter = ?
            ORDER BY street COLLATE NOCASE
            """,
            (letter,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_streets(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT street, street_letter, deals_count
            FROM streets
            ORDER BY street COLLATE NOCASE
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def suggest_streets(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = normalize_street(query)
        if not q:
            return []
        rows = self._conn.execute(
            """
            SELECT street, deals_count
            FROM streets
            WHERE street_norm LIKE ?
            ORDER BY deals_count DESC, street
            LIMIT ?
            """,
            (f"%{q}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_street(self, query: str) -> str | None:
        from street_titles import street_base_name

        candidates = [query, street_base_name(query)]
        seen: set[str] = set()
        for raw in candidates:
            q = normalize_street(raw)
            if not q or q in seen:
                continue
            seen.add(q)
            exact = self._conn.execute(
                "SELECT street FROM streets WHERE street_norm = ? LIMIT 1", (q,)
            ).fetchone()
            if exact:
                return exact["street"]
            row = self._conn.execute(
                """
                SELECT street FROM streets
                WHERE street_norm LIKE ? OR street_norm LIKE ?
                ORDER BY
                  CASE WHEN street_norm = ? THEN 0
                       WHEN street_norm LIKE ? THEN 1
                       ELSE 2 END,
                  deals_count DESC
                LIMIT 1
                """,
                (f"{q}%", f"%{q}%", q, f"{q}%"),
            ).fetchone()
            if row:
                return row["street"]
        return None

    # Жёсткий потолок строк на страницу улицы (защита от раздувания HTML/памяти).
    DEFAULT_DEALS_LIMIT = 500

    def search_deals(
        self,
        street: str,
        period_from: Quarter | None = None,
        period_to: Quarter | None = None,
        limit: int = DEFAULT_DEALS_LIMIT,
    ) -> list[dict[str, Any]]:
        sn = normalize_street(street)
        clauses = ["street_norm = ?"]
        params: list[Any] = [sn]
        if period_from is not None:
            clauses.append("(year > ? OR (year = ? AND quarter >= ?))")
            params.extend([period_from.year, period_from.year, period_from.quarter])
        if period_to is not None:
            clauses.append("(year < ? OR (year = ? AND quarter <= ?))")
            params.extend([period_to.year, period_to.year, period_to.quarter])
        params.append(limit)
        sql = f"""
            SELECT period_start, year, quarter, street,
                   wall_material, year_build, floor, area, deal_price, price_m2
            FROM deals
            WHERE {' AND '.join(clauses)}
            ORDER BY period_start, deal_price
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def close(self) -> None:
        self._conn.close()
