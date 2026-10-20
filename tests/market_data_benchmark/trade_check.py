"""The third-source cross-check: what actually traded.

The two daily series disagree, and the benchmark cannot say which of them is
right by looking at them alone. DNSE's **trade feed** is a third dataset for
the same session: it reports the session's running open, high and low, and
the last trade of the session reports the close and the session's cumulative
volume.

That makes it possible to say, for a sample of sessions, which of the two
daily series matches what traded. The result is not assumed in either
direction: the trade feed is read from the same API, and the volume unit it
uses is measured against the daily bar rather than assumed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .providers.dnse_provider import DnseProvider
from .schema import CanonicalRow

#: The trade feed reports the session's cumulative volume in units of ten
#: shares. Measured, not documented: for every session checked, the daily
#: bar's volume is exactly ten times the trade feed's `totalVolumeTraded`.
TRADE_VOLUME_UNITS_PER_SHARE = 10


@dataclass
class TradeCrossCheck:
    """One session, as the three datasets describe it."""

    symbol: str
    date: dt.date
    trades_returned: int
    trade_open: float | None
    trade_high: float | None
    trade_low: float | None
    trade_close: float | None
    trade_volume: float | None
    dnse_open: float | None
    dnse_high: float | None
    dnse_low: float | None
    dnse_close: float | None
    dnse_volume: float | None
    yahoo_open: float | None
    yahoo_high: float | None
    yahoo_low: float | None
    yahoo_close: float | None
    yahoo_volume: float | None
    #: Which daily series matches the trade feed, if either does.
    matches: str = "neither"

    def as_record(self) -> dict:
        return {
            "symbol": self.symbol,
            "date": self.date.isoformat(),
            "trades_returned": self.trades_returned,
            "trade_open": self.trade_open,
            "trade_high": self.trade_high,
            "trade_low": self.trade_low,
            "trade_close": self.trade_close,
            "trade_volume": self.trade_volume,
            "dnse_open": self.dnse_open,
            "dnse_high": self.dnse_high,
            "dnse_low": self.dnse_low,
            "dnse_close": self.dnse_close,
            "dnse_volume": self.dnse_volume,
            "yahoo_open": self.yahoo_open,
            "yahoo_high": self.yahoo_high,
            "yahoo_low": self.yahoo_low,
            "yahoo_close": self.yahoo_close,
            "yahoo_volume": self.yahoo_volume,
            "matches": self.matches,
        }


def _close(a: float | None, b: float | None, tolerance: float = 1.0) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerance


def _same_session(
    trade: tuple[float | None, float | None],
    daily: tuple[float | None, float | None],
    price_tolerance: float = 1.0,
    volume_tolerance: int = 0,
) -> bool:
    """Whether a daily bar describes the same session as the trade summary.

    Only two things are compared: the price of the session's last trade (the
    closing auction) and the session's cumulative volume. The trade feed's
    `openPrice`, `highestPrice` and `lowestPrice` fields are not used: their
    meaning is not documented, and on some sessions they are not even
    internally consistent (a "high" below the "low").
    """
    trade_close, trade_volume = trade
    daily_close, daily_volume = daily
    if not _close(trade_close, daily_close, price_tolerance):
        return False
    if trade_volume is None or daily_volume is None:
        return False
    return abs(trade_volume - daily_volume) <= volume_tolerance


def cross_check_session(
    provider: DnseProvider,
    symbol: str,
    day: dt.date,
    dnse_row: CanonicalRow | None,
    yahoo_row: CanonicalRow | None,
    price_tolerance: float = 1.0,
) -> TradeCrossCheck:
    """Compare one session's trade summary with the two daily bars."""
    summary, error = provider.session_trades(symbol, day)
    if error is not None or summary is None:
        return TradeCrossCheck(
            symbol=symbol,
            date=day,
            trades_returned=0,
            trade_open=None,
            trade_high=None,
            trade_low=None,
            trade_close=None,
            trade_volume=None,
            dnse_open=dnse_row.open if dnse_row else None,
            dnse_high=dnse_row.high if dnse_row else None,
            dnse_low=dnse_row.low if dnse_row else None,
            dnse_close=dnse_row.close if dnse_row else None,
            dnse_volume=dnse_row.volume if dnse_row else None,
            yahoo_open=yahoo_row.open if yahoo_row else None,
            yahoo_high=yahoo_row.high if yahoo_row else None,
            yahoo_low=yahoo_row.low if yahoo_row else None,
            yahoo_close=yahoo_row.close if yahoo_row else None,
            yahoo_volume=yahoo_row.volume if yahoo_row else None,
            matches="no trade data",
        )

    scale = 1000.0  # the trade feed quotes in thousand dong, like the daily bar
    trade_close = (summary.get("last_price") or 0) * scale or None
    trade_volume = (
        float(summary["total_volume_traded"]) * TRADE_VOLUME_UNITS_PER_SHARE
        if summary.get("total_volume_traded") is not None
        else None
    )
    trade = (trade_close, trade_volume)
    dnse = (
        dnse_row.close if dnse_row else None,
        float(dnse_row.volume) if dnse_row else None,
    )
    yahoo = (
        yahoo_row.close if yahoo_row else None,
        float(yahoo_row.volume) if yahoo_row else None,
    )

    dnse_matches = _same_session(trade, dnse, price_tolerance)
    yahoo_matches = _same_session(trade, yahoo, price_tolerance)
    if dnse_matches and yahoo_matches:
        matches = "both"
    elif dnse_matches:
        matches = "dnse"
    elif yahoo_matches:
        matches = "yahoo"
    else:
        matches = "neither"

    return TradeCrossCheck(
        symbol=symbol,
        date=day,
        trades_returned=int(summary.get("trades_returned") or 0),
        trade_open=None,
        trade_high=None,
        trade_low=None,
        trade_close=trade_close,
        trade_volume=trade_volume,
        dnse_open=dnse_row.open if dnse_row else None,
        dnse_high=dnse_row.high if dnse_row else None,
        dnse_low=dnse_row.low if dnse_row else None,
        dnse_close=dnse[0],
        dnse_volume=dnse[1],
        yahoo_open=yahoo_row.open if yahoo_row else None,
        yahoo_high=yahoo_row.high if yahoo_row else None,
        yahoo_low=yahoo_row.low if yahoo_row else None,
        yahoo_close=yahoo[0],
        yahoo_volume=yahoo[1],
        matches=matches,
    )
