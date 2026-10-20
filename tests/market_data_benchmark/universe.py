"""The VN30 universe: the dated snapshot the benchmark runs against.

The list is not a "latest VN30" typed into a file. It is the HOSE index review
that took effect on 2026-08-03, read from the HOSE "Công bố thông tin"
document, and stored with its reference date and source so a later run can
reproduce exactly the same 30 symbols.

The benchmark uses the *current* composition, not a point-in-time history. That
is stated in `run_metadata.json` as `universe_kind: current_composition`, and
it matters: a symbol that entered the index during the comparison window is
traded under the index for only part of it.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from .config import EXCHANGE, INDEX_NAME, UNIVERSE_FIXTURE
from .normalization import yahoo_symbol


@dataclass(frozen=True)
class UniverseMember:
    """One member of the index, with the provenance of the list."""

    symbol: str
    yahoo_symbol: str
    exchange: str
    index: str
    name: str
    effective_from: dt.date
    source_url: str
    retrieved_at: str
    index_shares: int | None = None
    free_float_pct: float | None = None
    cap_limit_pct: float | None = None

    def as_record(self) -> dict:
        return {
            "symbol": self.symbol,
            "yahoo_symbol": self.yahoo_symbol,
            "exchange": self.exchange,
            "index": self.index,
            "name": self.name,
            "effective_from": self.effective_from.isoformat(),
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "index_shares": self.index_shares,
            "free_float_pct": self.free_float_pct,
            "cap_limit_pct": self.cap_limit_pct,
        }


@dataclass(frozen=True)
class Universe:
    members: list[UniverseMember]
    effective_from: dt.date
    source_url: str
    retrieved_at: str
    kind: str = "current_composition"

    @property
    def symbols(self) -> list[str]:
        return [m.symbol for m in self.members]

    def __len__(self) -> int:
        return len(self.members)

    def __iter__(self):
        return iter(self.members)


def _optional_int(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def _optional_float(value: str) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


def load_universe(path: Path | None = None) -> Universe:
    """Read the VN30 snapshot.

    Raises when the snapshot is not a well-formed 30-member VN30 list: a
    benchmark that silently runs against a half-loaded universe would report
    coverage numbers that mean nothing.
    """
    path = path or UNIVERSE_FIXTURE
    members: list[UniverseMember] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            members.append(
                UniverseMember(
                    symbol=symbol,
                    yahoo_symbol=(row.get("yahoo_symbol") or "").strip().upper()
                    or yahoo_symbol(symbol),
                    exchange=(row.get("exchange") or EXCHANGE).strip().upper(),
                    index=(row.get("index") or INDEX_NAME).strip().upper(),
                    name=(row.get("name") or "").strip(),
                    effective_from=dt.date.fromisoformat(row["effective_from"].strip()),
                    source_url=(row.get("source_url") or "").strip(),
                    retrieved_at=(row.get("retrieved_at") or "").strip(),
                    index_shares=_optional_int(row.get("index_shares", "")),
                    free_float_pct=_optional_float(row.get("free_float_pct", "")),
                    cap_limit_pct=_optional_float(row.get("cap_limit_pct", "")),
                )
            )

    if not members:
        raise ValueError(f"{path} contains no members")
    symbols = [m.symbol for m in members]
    if len(set(symbols)) != len(symbols):
        duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
        raise ValueError(f"{path} lists duplicate symbols: {duplicates}")
    for member in members:
        if member.index != INDEX_NAME:
            raise ValueError(f"{member.symbol} is listed under index {member.index!r}")
        if member.exchange != EXCHANGE:
            raise ValueError(f"{member.symbol} is listed on exchange {member.exchange!r}")
    effective_dates = {m.effective_from for m in members}
    if len(effective_dates) != 1:
        raise ValueError(f"{path} mixes reference dates: {sorted(effective_dates)}")
    source_urls = {m.source_url for m in members}
    if len(source_urls) != 1:
        raise ValueError(f"{path} mixes source documents: {sorted(source_urls)}")

    return Universe(
        members=members,
        effective_from=next(iter(effective_dates)),
        source_url=next(iter(source_urls)),
        retrieved_at=members[0].retrieved_at,
    )
