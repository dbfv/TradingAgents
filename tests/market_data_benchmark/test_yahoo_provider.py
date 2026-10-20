"""The Yahoo adapter, driven with a mocked `yfinance`.

The mocks are here to check the adapter's own logic — how it labels a series,
how it reads a status, how it retries. They are not evidence that Yahoo
behaves this way; the integration test is.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tests.market_data_benchmark.config import BenchmarkConfig
from tests.market_data_benchmark.providers import YahooProvider
from tests.market_data_benchmark.schema import PriceType, RequestStatus

START = dt.date(2026, 10, 13)
END = dt.date(2026, 10, 16)


def _frame(rows: list[dict], tz: str = "Asia/Ho_Chi_Minh") -> pd.DataFrame:
    index = pd.DatetimeIndex([row["date"] for row in rows], tz=tz, name="Date")
    return pd.DataFrame(
        [
            {
                "Open": row["open"],
                "High": row["high"],
                "Low": row["low"],
                "Close": row["close"],
                "Adj Close": row.get("adj_close", row["close"]),
                "Volume": row["volume"],
                "Dividends": row.get("dividends", 0.0),
                "Stock Splits": row.get("splits", 0.0),
            }
            for row in rows
        ],
        index=index,
    )


class _FakeTicker:
    def __init__(self, symbol, frames, errors=None):
        self.symbol = symbol
        self._frames = frames
        self._errors = errors or {}

    def history(self, start=None, end=None, auto_adjust=True, raise_errors=True):
        error = self._errors.get(auto_adjust)
        if error is not None:
            raise error
        return self._frames[auto_adjust]


def _install(monkeypatch, frames, errors=None):
    import yfinance

    seen: list[str] = []

    def factory(symbol):
        seen.append(symbol)
        return _FakeTicker(symbol, frames, errors)

    monkeypatch.setattr(yfinance, "Ticker", factory)
    return seen


@pytest.mark.unit
class TestYahooProvider:
    def test_a_successful_fetch_returns_both_series(self, monkeypatch):
        rows = [
            {
                "date": "2026-10-13",
                "open": 218000.0,
                "high": 224000.0,
                "low": 215300.0,
                "close": 222000.0,
                "volume": 3133800,
            },
            {
                "date": "2026-10-14",
                "open": 222000.0,
                "high": 222000.0,
                "low": 217300.0,
                "close": 218400.0,
                "volume": 3116100,
            },
        ]
        seen = _install(monkeypatch, {False: _frame(rows), True: _frame(rows)})

        provider = YahooProvider()
        result = provider.fetch_daily("VIC", START, END, BenchmarkConfig())

        assert result.request_status is RequestStatus.SUCCESS
        assert seen == ["VIC.VN", "VIC.VN"]
        assert {row.price_type for row in result.rows} == {PriceType.RAW, PriceType.ADJUSTED}
        raw = [row for row in result.rows if row.price_type is PriceType.RAW]
        assert len(raw) == 2
        assert raw[0].date == dt.date(2026, 10, 13)
        assert raw[0].close == 222000.0
        assert raw[0].volume == 3133800
        assert raw[0].price_unit == "VND"
        assert raw[0].volume_unit == "shares"

    def test_the_raw_and_adjusted_series_are_never_merged(self, monkeypatch):
        rows = [
            {
                "date": "2026-10-13",
                "open": 218000.0,
                "high": 224000.0,
                "low": 215300.0,
                "close": 222000.0,
                "adj_close": 210000.0,
                "volume": 3133800,
            }
        ]
        _install(monkeypatch, {False: _frame(rows), True: _frame(rows)})

        result = YahooProvider().fetch_daily("VIC", START, END, BenchmarkConfig())
        by_type = {row.price_type: row for row in result.rows}
        assert by_type[PriceType.RAW].close == 222000.0
        assert by_type[PriceType.ADJUSTED].close == 222000.0

    def test_an_empty_answer_is_reported_as_empty(self, monkeypatch):
        _install(monkeypatch, {False: pd.DataFrame(), True: pd.DataFrame()})
        result = YahooProvider().fetch_daily("VIC", START, END, BenchmarkConfig())
        assert result.request_status is RequestStatus.EMPTY_RESPONSE
        assert result.rows == []

    def test_a_missing_symbol_is_not_retried(self, monkeypatch):
        from yfinance.exceptions import YFPricesMissingError

        calls: list[int] = []

        class Ticker:
            def __init__(self, symbol):
                pass

            def history(self, start=None, end=None, auto_adjust=True, raise_errors=True):
                calls.append(1)
                raise YFPricesMissingError("NOPE", "", yahoo_reason="Data doesn't exist")

        import yfinance

        monkeypatch.setattr(yfinance, "Ticker", Ticker)
        result = YahooProvider().fetch_daily("NOPE", START, END, BenchmarkConfig())
        assert result.request_status is RequestStatus.SYMBOL_NOT_FOUND
        assert result.error.http_status == 404
        # The provider stops at the first definitive answer: a missing symbol
        # is not a network fault, so it is not retried, and the second series
        # is not even requested.
        assert len(calls) == 1

    def test_a_rate_limit_is_reported_as_a_rate_limit(self, monkeypatch):
        from yfinance.exceptions import YFRateLimitError

        class Ticker:
            def __init__(self, symbol):
                pass

            def history(self, start=None, end=None, auto_adjust=True, raise_errors=True):
                raise YFRateLimitError()

        import yfinance

        monkeypatch.setattr(yfinance, "Ticker", Ticker)
        monkeypatch.setattr("tests.market_data_benchmark.providers.yahoo_provider._sleep", lambda s: None)
        result = YahooProvider().fetch_daily("VIC", START, END, BenchmarkConfig())
        assert result.request_status is RequestStatus.RATE_LIMITED
        assert result.error.http_status == 429

    def test_a_timezone_failure_is_reported_as_such(self, monkeypatch):
        from yfinance.exceptions import YFTzMissingError

        class Ticker:
            def __init__(self, symbol):
                pass

            def history(self, start=None, end=None, auto_adjust=True, raise_errors=True):
                raise YFTzMissingError("VIC.VN")

        import yfinance

        monkeypatch.setattr(yfinance, "Ticker", Ticker)
        result = YahooProvider().fetch_daily("VIC", START, END, BenchmarkConfig())
        assert result.request_status is RequestStatus.TIMEZONE_ERROR

    def test_the_holiday_filler_row_is_kept_but_marked_by_its_zero_volume(self, monkeypatch):
        # Yahoo emits a bar for every weekday; the holiday one carries no trade.
        rows = [
            {
                "date": "2026-10-13",
                "open": 218000.0,
                "high": 224000.0,
                "low": 215300.0,
                "close": 222000.0,
                "volume": 3133800,
            },
            {
                "date": "2026-10-14",
                "open": 218000.0,
                "high": 224000.0,
                "low": 215300.0,
                "close": 218000.0,
                "volume": 0,
            },
        ]
        _install(monkeypatch, {False: _frame(rows), True: _frame(rows)})
        result = YahooProvider().fetch_daily("VIC", START, END, BenchmarkConfig())
        raw = [row for row in result.rows if row.price_type is PriceType.RAW]
        assert [row.volume for row in raw] == [3133800, 0]
