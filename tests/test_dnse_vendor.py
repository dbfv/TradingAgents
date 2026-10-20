"""The DNSE vendor: how it is routed, and what it does with the API's answer.

The API is mocked here, so these tests check the adapter's own logic — the
routing decision, the unit conversion, the error mapping. They are not
evidence that the API behaves this way; the integration test is.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorUnavailableError,
)
from tradingagents.dataflows.ohlcv import (
    fallback_vendor_chain,
    market_vendor_chain,
    symbol_suffix,
)
from tradingagents.dataflows.router import VENDOR_METHODS, route_to_vendor
from tradingagents.dataflows.vendors.dnse import common as dnse_common, market as dnse_market

SYMBOL = "VIC.VN"
START = "2026-10-13"
END = "2026-10-16"


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
    def __init__(self, ohlc=None, status=200, body=None):
        self._ohlc = ohlc
        self._status = status
        self._body = body
        self.calls = []

    def get_ohlc(self, bar_type=None, query=None, dry_run=False):
        self.calls.append({"bar_type": bar_type, "query": dict(query or {})})
        if self._status != 200:
            return self._status, self._body or json.dumps(
                {"status": self._status, "code": "OA-003", "message": "invalid request"}
            )
        return 200, self._ohlc


@pytest.fixture
def dnse_client(monkeypatch):
    """Replace the SDK client with a fake one."""
    client = _FakeClient(
        ohlc=_payload(
            [1791856800, 1791943200],  # 2026-10-13, 2026-10-14
            [218, 222],
            [224, 222],
            [215.3, 217.3],
            [222, 218.4],
            [3133800, 3116100],
        )
    )
    monkeypatch.setattr(dnse_market, "dnse_client", lambda: client)
    monkeypatch.setenv("DNSE_API_KEY", "key")
    monkeypatch.setenv("DNSE_API_SECRET", "secret")
    return client


def _set_yahoo_vendor(monkeypatch, implementation) -> None:
    """Replace the Yahoo implementation the router calls."""
    monkeypatch.setitem(VENDOR_METHODS["get_stock_data"], "yfinance", implementation)


@pytest.mark.unit
class TestSymbolRules:
    def test_a_vn_suffix_marks_a_vietnamese_symbol(self):
        assert dnse_common.is_vietnamese_symbol("VIC.VN")
        assert not dnse_common.is_vietnamese_symbol("VIC")
        assert not dnse_common.is_vietnamese_symbol("AAPL")

    def test_the_api_is_queried_with_the_bare_symbol(self):
        assert dnse_common.dnse_symbol("VIC.VN") == "VIC"
        assert dnse_common.dnse_symbol("vic.vn") == "VIC"

    def test_the_suffix_is_read_off_the_symbol(self):
        assert symbol_suffix("VIC.VN") == ".VN"
        assert symbol_suffix("0700.HK") == ".HK"
        assert symbol_suffix("AAPL") == ""

    def test_the_session_date_is_the_local_date_of_the_stamp(self):
        # 2026-10-19 02:00 UTC is 09:00 in Vietnam: the session's own day.
        assert dnse_common.session_date(1792375200) == dt.date(2026, 10, 19)
        # 2012-03-20 00:00 UTC is 07:00 in Vietnam: still that day.
        assert dnse_common.session_date(1332192000) == dt.date(2012, 3, 20)


@pytest.mark.unit
class TestRouting:
    def test_a_vietnamese_symbol_goes_to_dnse_first(self):
        assert market_vendor_chain(SYMBOL)[0] == "dnse"

    def test_a_us_symbol_keeps_its_configured_chain(self):
        assert market_vendor_chain("AAPL") == ["yfinance"]

    def test_the_dnse_vendor_serves_the_daily_bars(self):
        assert "dnse" in VENDOR_METHODS["get_stock_data"]

    def test_a_bare_symbol_falls_back_to_dnse_only_after_the_configured_chain(self):
        assert market_vendor_chain("VIC") == ["yfinance"]
        assert fallback_vendor_chain("VIC") == ["dnse"]

    def test_a_vietnamese_symbol_does_not_need_the_fallback(self):
        assert fallback_vendor_chain(SYMBOL) == []

    def test_the_market_rule_is_configurable(self):
        set_config({"market_vendors": {".VN": "yfinance,dnse"}})
        assert market_vendor_chain(SYMBOL) == ["yfinance", "dnse"]


@pytest.mark.unit
class TestTheAdapter:
    def test_the_csv_carries_dong_and_shares(self, dnse_client):
        out = dnse_market.get_DNSE_data_online(SYMBOL, START, END)
        assert "# Source: DNSE OpenAPI" in out
        assert "2026-10-13,218000,224000,215300,222000,3133800" in out
        assert "2026-10-14,222000,222000,217300,218400,3116100" in out

    def test_the_request_carries_the_documented_parameters(self, dnse_client):
        dnse_market.get_DNSE_data_online(SYMBOL, START, END)
        call = dnse_client.calls[0]
        assert call["bar_type"] == "STOCK"
        assert call["query"]["symbol"] == "VIC"
        assert call["query"]["resolution"] == "1D"
        assert "from" in call["query"] and "to" in call["query"]

    def test_an_empty_answer_is_reported_as_no_data(self, monkeypatch):
        client = _FakeClient(ohlc=_payload([], [], [], [], [], []))
        monkeypatch.setattr(dnse_market, "dnse_client", lambda: client)
        with pytest.raises(NoMarketDataError):
            dnse_market.get_DNSE_data_online(SYMBOL, START, END)

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, VendorNotConfiguredError),
            (403, VendorUnavailableError),
            (404, NoMarketDataError),
            (429, VendorUnavailableError),
            (400, NoMarketDataError),
            (500, VendorUnavailableError),
        ],
    )
    def test_an_http_status_is_classified(self, monkeypatch, status, expected):
        client = _FakeClient(status=status)
        monkeypatch.setattr(dnse_market, "dnse_client", lambda: client)
        with pytest.raises(expected):
            dnse_market.get_DNSE_data_online(SYMBOL, START, END)

    def test_without_credentials_the_vendor_says_it_is_not_configured(self, monkeypatch):
        monkeypatch.setenv("DNSE_API_KEY", "")
        monkeypatch.setenv("DNSE_API_SECRET", "")
        with pytest.raises(VendorNotConfiguredError):
            dnse_common.dnse_client()


@pytest.mark.unit
class TestTheRoutedTool:
    def test_a_vietnamese_symbol_is_served_by_dnse(self, dnse_client, monkeypatch):
        def _fail(*args, **kwargs):
            raise AssertionError("Yahoo must not be asked for a Vietnamese listing")

        _set_yahoo_vendor(monkeypatch, _fail)
        out = route_to_vendor("get_stock_data", SYMBOL, START, END)
        assert "# Source: DNSE OpenAPI" in out

    def test_a_us_symbol_is_still_served_by_yahoo(self, monkeypatch):
        _set_yahoo_vendor(monkeypatch, lambda *a, **k: "# from yahoo\n")
        out = route_to_vendor("get_stock_data", "AAPL", START, END)
        assert out == "# from yahoo\n"

    def test_a_bare_symbol_reaches_dnse_after_yahoo_says_no(self, monkeypatch, dnse_client):
        def _no_data(*args, **kwargs):
            raise NoMarketDataError("VIC", "VIC", "no rows between 2026-10-13 and 2026-10-16")

        _set_yahoo_vendor(monkeypatch, _no_data)
        out = route_to_vendor("get_stock_data", "VIC", START, END)
        assert "# Source: DNSE OpenAPI" in out

    def test_a_symbol_neither_vendor_has_is_reported_as_unavailable(self, monkeypatch):
        def _no_data(*args, **kwargs):
            raise NoMarketDataError("ZZZZ", "ZZZZ", "no rows")

        _set_yahoo_vendor(monkeypatch, _no_data)
        out = route_to_vendor("get_stock_data", "ZZZZ", START, END)
        assert out.startswith("NO_DATA_AVAILABLE")
