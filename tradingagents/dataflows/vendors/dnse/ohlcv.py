"""The OHLCV frame the indicators and the verified snapshot are computed from.

The Yahoo loader caches a five-year download per symbol and fills the gaps;
this one does the same for a Vietnamese listing, so the two paths return the
same shape (``Date, Open, High, Low, Close, Volume``), the same look-ahead
cutoff, and the same staleness guard.

The only differences are where the numbers come from and one of them matters:
the DNSE API reports **prices in thousand dong**, and the frame here is in
**dong**, like every other price in the project.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os

import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.files import replace_file
from tradingagents.dataflows.symbols import safe_ticker_component
from tradingagents.dataflows.vendors.dnse.common import (
    BAR_TYPE_STOCK,
    RESOLUTION_DAILY,
    dnse_client,
    dnse_retry,
    dnse_symbol,
    epoch_for,
    raise_for_empty,
    raise_for_status,
    session_date,
)
from tradingagents.dataflows.vendors.yahoo.ohlcv import (
    _assert_ohlcv_not_stale,
    _cache_is_fresh,
)

logger = logging.getLogger(__name__)

#: How much history the frame carries, matching the Yahoo loader.
LOOKBACK_YEARS = 5


def load_dnse_ohlcv(symbol: str, as_of_date: str, fill_gaps: bool = True) -> pd.DataFrame:
    """Daily OHLCV for a Vietnamese listing, up to and including ``as_of_date``.

    The frame is cached per symbol, so a second read in the same run does not
    call the API again. Rows after ``as_of_date`` are dropped, so a backtest
    never sees a future price.
    """
    bare = dnse_symbol(symbol)
    safe_symbol = safe_ticker_component(bare)

    config = get_config()
    as_of_dt = pd.to_datetime(as_of_date).normalize()
    now = pd.Timestamp.today()
    start_date = now - pd.DateOffset(years=LOOKBACK_YEARS)

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(config["data_cache_dir"], f"{safe_symbol}-DNSE-data.csv")

    data = None
    if os.path.exists(data_file):
        try:
            cached = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
        except Exception:  # noqa: BLE001 - a damaged cache is a miss, not a failure
            cached = None
        if (
            cached is not None
            and not cached.empty
            and "Close" in cached.columns
            and _cache_is_fresh(data_file, as_of_dt, now)
        ):
            data = cached

    if data is None:
        downloaded = _fetch_daily(symbol, start_date.date(), now.date())
        if downloaded is None or downloaded.empty:
            raise_for_empty(symbol, "price rows")
        replace_file(
            data_file, lambda temp: downloaded.to_csv(temp, index=False, encoding="utf-8")
        )
        data = downloaded

    data = _clean_dataframe(data)
    data = data[data["Date"] <= as_of_dt]

    if data.empty:
        raise NoMarketDataError(symbol, bare, "no price rows on or before the requested date")

    # A closeless newest bar is an unsettled session, not a symbol without data.
    if not data.empty and pd.isna(data["Close"].iloc[-1]):
        settled = data["Close"].notna().to_numpy().nonzero()[0]
        if settled.size == 0:
            raise NoMarketDataError(symbol, bare, "no bar in range has a closing price")
        logger.warning(
            "%s: %d trailing bar(s) have no closing price; using %s as the latest close.",
            bare,
            len(data) - settled[-1] - 1,
            data["Date"].iloc[settled[-1]].date(),
        )

    data = _fill_price_gaps(data) if fill_gaps else data.dropna(subset=["Close"]).copy()

    _assert_ohlcv_not_stale(data, as_of_date, symbol, bare)
    return data


def _fetch_daily(symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame | None:
    """The daily bars for a range, as a frame."""
    client = dnse_client()
    bare = dnse_symbol(symbol)
    cursor = epoch_for(start)
    limit = epoch_for(end + dt.timedelta(days=1))
    rows: list[dict] = []
    seen: set[dt.date] = set()

    while cursor < limit:
        status, body = dnse_retry(
            lambda cursor=cursor: client.get_ohlc(
                bar_type=BAR_TYPE_STOCK,
                query={
                    "symbol": bare,
                    "resolution": RESOLUTION_DAILY,
                    "from": cursor,
                    "to": limit,
                },
            )
        )
        if status != 200:
            raise_for_status(status, body, bare)
        try:
            page = json.loads(body) if isinstance(body, str) else body
        except (TypeError, ValueError) as exc:
            raise VendorUnavailableError(f"the DNSE response was not JSON: {exc}") from exc
        if not isinstance(page, dict) or "t" not in page:
            raise VendorUnavailableError(
                f"the DNSE response carried no candles: {str(page)[:200]}"
            )

        for index, stamp in enumerate(page.get("t") or []):
            day = session_date(stamp)
            if day in seen or not (start <= day <= end):
                continue
            seen.add(day)
            rows.append(
                {
                    "Date": day.isoformat(),
                    "Open": _price(page, "o", index),
                    "High": _price(page, "h", index),
                    "Low": _price(page, "l", index),
                    "Close": _price(page, "c", index),
                    "Volume": _volume(page, "v", index),
                }
            )

        next_time = page.get("nextTime") or 0
        if not next_time or int(next_time) <= cursor:
            break
        cursor = int(next_time)

    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)


def _price(page: dict, key: str, index: int) -> float:
    values = page.get(key) or []
    if index >= len(values) or values[index] is None:
        return float("nan")
    return round(float(values[index]) * 1000.0)


def _volume(page: dict, key: str, index: int) -> int:
    values = page.get(key) or []
    if index >= len(values) or values[index] is None:
        return 0
    return int(round(float(values[index])))


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Parse the date column and coerce prices to numeric, like the Yahoo loader."""
    frame = data.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame = frame.dropna(subset=["Date"]).copy()
    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in frame.columns]
    frame[price_cols] = frame[price_cols].apply(pd.to_numeric, errors="coerce")
    return frame


def _fill_price_gaps(data: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with no close and carry the remaining gaps forward."""
    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data = data.dropna(subset=["Close"]).copy()
    data[price_cols] = data[price_cols].ffill().bfill()
    return data
