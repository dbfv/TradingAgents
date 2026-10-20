"""What the benchmark compares, over which window, and against which thresholds.

Every tunable lives here so a re-run can change the window or the thresholds
without touching the comparison code, and so `run_metadata.json` can record
exactly what was compared.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_ROOT.parents[1]

FIXTURES_DIR = BENCHMARK_ROOT / "fixtures"
UNIVERSE_FIXTURE = FIXTURES_DIR / "vn30_universe.csv"
ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "market_data_benchmark"

#: The market the benchmark covers.
EXCHANGE = "HOSE"
#: The index whose constituents are the subject of the benchmark.
INDEX_NAME = "VN30"
#: Yahoo's suffix for Vietnamese listings.
YAHOO_SUFFIX = ".VN"
#: Every price in this benchmark is normalised to Vietnamese dong.
CURRENCY = "VND"
#: The timezone the Vietnamese market trades in (UTC+7, no DST).
MARKET_TIMEZONE = "Asia/Ho_Chi_Minh"
#: The market's fixed offset from UTC, in hours.
MARKET_UTC_OFFSET_HOURS = 7

#: The day the benchmark is run. The session of this day is still open or only
#: partially reported, so it is excluded from the comparison window: the window
#: ends on the last *completed* session before it.
REFERENCE_RUN_DATE = dt.date(2026, 10, 20)
#: How much history the comparison covers.
WINDOW_MONTHS = 12


@dataclass(frozen=True)
class Window:
    """The session window both providers are measured over.

    `start` and `end` are inclusive session dates. `end` is the last session
    before the run date, so both providers describe the same closed sessions.
    """

    start: dt.date
    end: dt.date

    def contains(self, day: dt.date) -> bool:
        return self.start <= day <= self.end

    def as_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def _shift_months(day: dt.date, months: int) -> dt.date:
    """The same day-of-month, `months` earlier, clamped to the month's length."""
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    last_day = _days_in_month(year, month)
    return dt.date(year, month, min(day.day, last_day))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (dt.date(year, month + 1, 1) - dt.timedelta(days=1)).day


def resolve_window(run_date: dt.date | None = None, months: int = WINDOW_MONTHS) -> Window:
    """The window ending on the last day before `run_date`.

    `run_date` itself is excluded: the session of that day is incomplete for at
    least one of the two providers, and comparing an incomplete bar against a
    complete one would manufacture a mismatch.

    The window spans `months` months inclusive of both ends: with
    `run_date = 2026-10-20` and `months = 12` it covers 2025-10-20 → 2026-10-19.
    """
    run_date = run_date or REFERENCE_RUN_DATE
    end = run_date - dt.timedelta(days=1)
    start = _shift_months(end, -months) + dt.timedelta(days=1)
    return Window(start=start, end=end)


@dataclass(frozen=True)
class Thresholds:
    """The MVP criteria from the benchmark plan. Configurable, never enforced
    by discarding data: a miss is reported, not hidden."""

    min_ticker_coverage: int = 30
    min_session_coverage: float = 0.99
    min_request_success_rate: float = 0.99
    max_critical_unresolved: int = 0
    #: Prices are compared in dong; one dong of slack absorbs the float residue
    #: of multiplying a thousand-dong float by 1000.
    price_abs_tolerance_vnd: float = 1.0
    #: Volume is compared exactly; a difference is a difference.
    volume_abs_tolerance_shares: int = 0

    def as_dict(self) -> dict:
        return {
            "min_ticker_coverage": self.min_ticker_coverage,
            "min_session_coverage": self.min_session_coverage,
            "min_request_success_rate": self.min_request_success_rate,
            "max_critical_unresolved": self.max_critical_unresolved,
            "price_abs_tolerance_vnd": self.price_abs_tolerance_vnd,
            "volume_abs_tolerance_shares": self.volume_abs_tolerance_shares,
        }


@dataclass(frozen=True)
class BenchmarkConfig:
    """Everything a run needs to know about itself."""

    window: Window = field(default_factory=resolve_window)
    thresholds: Thresholds = field(default_factory=Thresholds)
    #: How many symbols to re-fetch for the stability check.
    stability_sample_size: int = 5
    #: How many times the stability sample is fetched.
    stability_repeats: int = 3
    #: How many symbols the third-source (trade feed) cross-check covers.
    trade_check_symbols: int = 4
    #: How many sessions of the window the cross-check probes.
    trade_check_sample_size: int = 4
    #: Seconds between two DNSE requests, to stay under the rate limit.
    dnse_request_delay_s: float = 0.4
    #: Seconds between two Yahoo requests.
    yahoo_request_delay_s: float = 0.5
    #: Timeout for a single provider request.
    request_timeout_s: float = 60.0
    #: Retries for a transient failure. A missing symbol is never retried.
    max_retries: int = 3
    retry_base_delay_s: float = 2.0
    #: Write the raw provider payloads next to the reports.
    write_raw_snapshots: bool = False

    def as_dict(self) -> dict:
        return {
            "window": self.window.as_dict(),
            "thresholds": self.thresholds.as_dict(),
            "stability_sample_size": self.stability_sample_size,
            "stability_repeats": self.stability_repeats,
            "trade_check_symbols": self.trade_check_symbols,
            "trade_check_sample_size": self.trade_check_sample_size,
            "dnse_request_delay_s": self.dnse_request_delay_s,
            "yahoo_request_delay_s": self.yahoo_request_delay_s,
            "request_timeout_s": self.request_timeout_s,
            "max_retries": self.max_retries,
            "retry_base_delay_s": self.retry_base_delay_s,
            "write_raw_snapshots": self.write_raw_snapshots,
            "exchange": EXCHANGE,
            "index": INDEX_NAME,
            "currency": CURRENCY,
            "market_timezone": MARKET_TIMEZONE,
            "reference_run_date": REFERENCE_RUN_DATE.isoformat(),
        }
