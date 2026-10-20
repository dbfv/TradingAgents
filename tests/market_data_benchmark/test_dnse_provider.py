"""The DNSE adapter, driven with a mocked SDK client.

The mock reproduces the shapes the official SDK returns, as read from the
official OpenAPI specification: the columnar `t`/`o`/`h`/`l`/`c`/`v` payload,
the `nextTime` cursor, and the HTTP-status-plus-body error convention.

These mocks test the adapter's own logic. They are not evidence that the DNSE
API behaves this way — the integration test is.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from tests.market_data_benchmark.config import BenchmarkConfig
from tests.market_data_benchmark.providers import DnseProvider
from tests.market_data_benchmark.schema import PriceType, RequestStatus

START = dt.date(2026, 10, 13)
END = dt.date(2026, 10, 16)


def _payload(timestamps, opens, highs, lows, closes, volumes, next_time=0):
    return json.dumps(
        {
            "t": timestamps,
            "o": opens,
            "h": highs,
            "l": lows,
            "c": closes,
            "v": volumes,
            "nextTime": next_time,
        }
    )


class _FakeClient:
    """Stands in for `dnse.DNSEClient`."""

    def __init__(self, ohlc=None, working_dates=None, instruments=None, error_status=None):
        self._ohlc = ohlc
        self._working_dates = working_dates
        self._instruments = instruments
        self._error_status = error_status
        self.calls: list[dict] = []

    def get_ohlc(self, bar_type=None, query=None, dry_run=False):
        self.calls.append({"method": "get_ohlc", "bar_type": bar_type, "query": dict(query or {})})
        if self._error_status is not None:
            return self._error_status, json.dumps({"code": "OA-003", "message": "invalid"})
        return 200, self._ohlc

    def get_working_dates(self, dry_run=False):
        self.calls.append({"method": "get_working_dates"})
        if self._error_status is not None:
            return self._error_status, json.dumps({"code": "OA-003", "message": "invalid"})
        return 200, json.dumps({"workingDates": self._working_dates or []})

    def get_instruments(self, symbol=None, market_id=None, security_group_id=None,
                        index_name=None, limit=None, page=None, version=None, dry_run=False):
        self.calls.append({"method": "get_instruments", "index_name": index_name})
        if self._error_status is not None:
            return self._error_status, json.dumps({"code": "OA-003", "message": "invalid"})
        return 200, json.dumps(
            {"data": self._instruments or [], "total": len(self._instruments or []), "page": 1, "limit": limit}
        )

    def get_security_definition(self, symbol, board_id=None, dry_run=False):
        self.calls.append({"method": "get_security_definition", "symbol": symbol})
        return 200, json.dumps([{"symbol": symbol, "marketId": "STO"}])


def _provider(client) -> DnseProvider:
    return DnseProvider(api_key="key", api_secret="secret", client=client)


@pytest.mark.unit
class TestDnseProvider:
    def test_a_successful_fetch_is_normalised(self):
        # 2026-10-13 02:00 UTC, 2026-10-14 02:00 UTC.
        client = _FakeClient(
            ohlc=_payload(
                [1791856800, 1791943200],
                [218, 222],
                [224, 222],
                [215.3, 217.3],
                [222, 218.4],
                [3133800, 3116100],
            )
        )
        result = _provider(client).fetch_daily("VIC", START, END, BenchmarkConfig())

        assert result.request_status is RequestStatus.SUCCESS
        assert [row.date for row in result.rows] == [
            dt.date(2026, 10, 13),
            dt.date(2026, 10, 14),
        ]
        first = result.rows[0]
        assert first.open == 218000.0
        assert first.high == 224000.0
        assert first.low == 215300.0
        assert first.close == 222000.0
        assert first.volume == 3133800
        # DNSE quotes thousand dong; the canonical row is in dong.
        assert first.price_unit == "thousand_VND"
        assert first.currency == "VND"
        assert first.volume_unit == "shares"
        # DNSE has no adjusted series, so every row is raw.
        assert {row.price_type for row in result.rows} == {PriceType.RAW}

    def test_the_request_carries_the_documented_parameters(self):
        client = _FakeClient(ohlc=_payload([1791856800], [218], [224], [215.3], [222], [3133800]))
        _provider(client).fetch_daily("VIC", START, END, BenchmarkConfig())

        call = client.calls[0]
        assert call["bar_type"] == "STOCK"
        assert call["query"]["symbol"] == "VIC"
        assert call["query"]["resolution"] == "1D"
        assert call["query"]["from"] == int(
            dt.datetime(2026, 10, 13, tzinfo=dt.timezone(dt.timedelta(hours=7))).timestamp()
        )

    def test_rows_outside_the_window_are_dropped(self):
        client = _FakeClient(
            ohlc=_payload(
                [1791856800, 1792375200],  # 2026-10-13 and 2026-10-19
                [218, 219],
                [224, 219],
                [215.3, 207],
                [222, 207],
                [3133800, 2096600],
            )
        )
        result = _provider(client).fetch_daily("VIC", START, dt.date(2026, 10, 16), BenchmarkConfig())
        assert [row.date for row in result.rows] == [dt.date(2026, 10, 13)]

    def test_the_next_time_cursor_is_followed(self):
        first = _payload([1791856800], [218], [224], [215.3], [222], [3133800], next_time=1791943200)
        second = _payload([1791943200], [222], [222], [217.3], [218.4], [3116100], next_time=0)

        class _Paged(_FakeClient):
            def get_ohlc(self, bar_type=None, query=None, dry_run=False):
                self.calls.append({"method": "get_ohlc", "query": dict(query or {})})
                if len(self.calls) == 1:
                    return 200, first
                return 200, second

        client = _Paged(ohlc=first)
        result = _provider(client).fetch_daily("VIC", START, END, BenchmarkConfig())
        assert len(client.calls) == 2
        assert client.calls[1]["query"]["from"] == 1791943200
        assert len(result.rows) == 2

    def test_an_empty_answer_is_reported_as_empty(self):
        client = _FakeClient(ohlc=_payload([], [], [], [], [], []))
        result = _provider(client).fetch_daily("VIC", START, END, BenchmarkConfig())
        assert result.request_status is RequestStatus.EMPTY_RESPONSE

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, RequestStatus.AUTH_ERROR),
            (403, RequestStatus.PERMISSION_ERROR),
            (404, RequestStatus.SYMBOL_NOT_FOUND),
            (429, RequestStatus.RATE_LIMITED),
            (400, RequestStatus.INVALID_PARAMETER),
            (500, RequestStatus.NETWORK_ERROR),
            (418, RequestStatus.UNSUPPORTED_OR_UNKNOWN),
        ],
    )
    def test_an_http_status_is_classified(self, status, expected):
        client = _FakeClient(error_status=status)
        result = _provider(client).fetch_daily("VIC", START, END, BenchmarkConfig())
        assert result.request_status is expected
        assert result.error.http_status == status

    def test_without_credentials_the_adapter_says_so(self):
        provider = DnseProvider(api_key="", api_secret="")
        result = provider.fetch_daily("VIC", START, END, BenchmarkConfig())
        assert result.request_status is RequestStatus.NOT_CONFIGURED
        assert provider.not_available_status() is RequestStatus.NOT_CONFIGURED
        assert provider.available() is False

    def test_the_working_dates_are_parsed(self):
        client = _FakeClient(working_dates=["2026-10-20", "2026-10-21"])
        dates, error = _provider(client).working_dates()
        assert error is None
        assert dates == [dt.date(2026, 10, 20), dt.date(2026, 10, 21)]

    def test_the_index_members_are_parsed(self):
        client = _FakeClient(instruments=[{"symbol": "ACB", "marketId": "STO", "indexName": ["VN30"]}])
        members, error = _provider(client).index_members("VN30")
        assert error is None
        assert members == [{"symbol": "ACB", "marketId": "STO", "indexName": ["VN30"]}]
        assert client.calls[0]["index_name"] == "VN30"
