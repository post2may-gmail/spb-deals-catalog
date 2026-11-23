from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Quarter:
    year: int
    quarter: int

    @property
    def period_start(self) -> str:
        month = 1 + (self.quarter - 1) * 3
        return f"{self.year:04d}-{month:02d}-01"

    @property
    def label(self) -> str:
        return f"{self.quarter} кв. {self.year}"

    @property
    def value(self) -> str:
        return f"{self.year}-Q{self.quarter}"

    @classmethod
    def from_period_start(cls, period_start: str) -> Quarter | None:
        try:
            year_s, month_s, *_ = period_start.split("-")
            year = int(year_s)
            month = int(month_s)
            return cls(year=year, quarter=(month - 1) // 3 + 1)
        except (TypeError, ValueError):
            return None

    @classmethod
    def from_value(cls, value: str) -> Quarter | None:
        value = (value or "").strip().upper()
        if not value:
            return None
        if "-Q" in value:
            try:
                year_s, q_s = value.split("-Q", 1)
                year = int(year_s)
                quarter = int(q_s)
                if 1 <= quarter <= 4:
                    return cls(year=year, quarter=quarter)
            except ValueError:
                return None
        return cls.from_period_start(value)


def available_quarters(period_starts: list[str]) -> list[Quarter]:
    seen: set[Quarter] = set()
    for raw in period_starts:
        q = Quarter.from_period_start(raw)
        if q is not None:
            seen.add(q)
    return sorted(seen)
