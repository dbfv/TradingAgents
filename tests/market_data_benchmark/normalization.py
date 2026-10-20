"""Turning what each provider said into one comparable shape.

The rules, and where each one comes from:

* **Symbol.** Yahoo needs the `.VN` suffix; a bare `VIC` returns nothing (a 404
  "Quote not found"). DNSE uses the bare symbol.
* **Date.** Both sides are reduced to the session date in the market's own
  timezone (UTC+7). Yahoo's index is already tz-aware at +07:00; DNSE's `t` is
  epoch seconds, stamped at 07:00 or 09:00 local depending on the period, but
  both fall on the same calendar day, so the local date is the session date.
* **Price.** Normalised to dong. Yahoo already quotes dong. DNSE quotes
  *thousand dong* (see `docs/research/dnse_openapi_notes.md` §7.3), so the
  factor is 1000. The multiplication leaves float residue on some values
  (`130.7 * 1000 = 130699.99999999999`), so the result is rounded to the
  nearest dong.
* **Volume.** Shares on both sides, verified equal on matched sessions.
"""

from __future__ import annotations

import datetime as dt
import math

from .config import (
    CURRENCY,
    MARKET_UTC_OFFSET_HOURS,
    YAHOO_SUFFIX,
)

#: The unit a provider's raw prices are denominated in, before normalisation.
PRICE_UNIT_VND = "VND"
PRICE_UNIT_THOUSAND_VND = "thousand_VND"
VOLUME_UNIT_SHARES = "shares"


def yahoo_symbol(symbol: str) -> str:
    """`VIC` → `VIC.VN`."""
    bare = symbol.strip().upper()
    if bare.endswith(YAHOO_SUFFIX):
        return bare
    return f"{bare}{YAHOO_SUFFIX}"


def dnse_symbol(symbol: str) -> str:
    """`VIC` → `VIC` (DNSE uses the bare exchange symbol)."""
    return symbol.strip().upper()


def bare_symbol(symbol: str) -> str:
    """`VIC.VN` or `vic` → `VIC`."""
    bare = symbol.strip().upper()
    if bare.endswith(YAHOO_SUFFIX):
        bare = bare[: -len(YAHOO_SUFFIX)]
    return bare


def market_tzinfo() -> dt.tzinfo:
    """The market's fixed UTC+7 offset (Vietnam has no daylight saving)."""
    return dt.timezone(dt.timedelta(hours=MARKET_UTC_OFFSET_HOURS))


def epoch_to_session_date(timestamp: float | int) -> dt.date:
    """The session date of an epoch-seconds candle stamp, in market time."""
    return dt.datetime.fromtimestamp(float(timestamp), market_tzinfo()).date()


def timestamp_to_market_datetime(timestamp: float | int) -> dt.datetime:
    """An epoch-seconds stamp, expressed in market time."""
    return dt.datetime.fromtimestamp(float(timestamp), market_tzinfo())


def date_to_epoch_start(day: dt.date) -> int:
    """Epoch seconds for the start of `day` in market time.

    Used to ask a provider for a range. The end of a range is exclusive in
    `yfinance` and ambiguous in the DNSE spec, so the benchmark asks for one
    day past the window and filters the result itself.
    """
    midnight = dt.datetime.combine(day, dt.time.min, tzinfo=market_tzinfo())
    return int(midnight.timestamp())


def normalize_price_thousand_vnd(value: float | int) -> float:
    """Thousand dong → dong, rounded to the nearest dong."""
    if value is None or not _is_finite(value):
        return math.nan
    return float(round(float(value) * 1000.0))


def normalize_price_vnd(value: float | int) -> float:
    """Dong → dong, rounded to the nearest dong."""
    if value is None or not _is_finite(value):
        return math.nan
    return float(round(float(value)))


def normalize_volume(value: float | int) -> int:
    """A provider's volume → shares, as an integer."""
    if value is None or not _is_finite(value):
        return math.nan  # type: ignore[return-value]
    return int(round(float(value)))


def _is_finite(value: object) -> bool:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def price_unit_for(provider: str) -> str:
    """The unit the provider's raw prices are in."""
    return PRICE_UNIT_THOUSAND_VND if provider == "dnse" else PRICE_UNIT_VND


def currency() -> str:
    return CURRENCY
