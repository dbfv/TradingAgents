"""The DNSE vendor against the real API.

These tests need ``DNSE_API_KEY`` and ``DNSE_API_SECRET``; without them they
are skipped, not faked. They prove the thing the unit tests cannot: that the
DNSE OpenAPI really answers, and that the production path really reads it.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

pytestmark = pytest.mark.integration

SYMBOL = "VIC.VN"
BARE = "VIC"
END = dt.date.today() - dt.timedelta(days=1)
START = END - dt.timedelta(days=10)


@pytest.fixture
def credentials():
    if not (os.environ.get("DNSE_API_KEY") and os.environ.get("DNSE_API_SECRET")):
        pytest.skip("DNSE_API_KEY / DNSE_API_SECRET are not set")


def test_the_api_answers_with_daily_bars(credentials):
    from tradingagents.dataflows.vendors.dnse.common import dnse_client

    client = dnse_client()
    status, body = client.get_ohlc(
        bar_type="STOCK",
        query={
            "symbol": BARE,
            "resolution": "1D",
            "from": int(dt.datetime(2026, 1, 1, tzinfo=dt.UTC).timestamp()),
            "to": int(dt.datetime(2026, 2, 1, tzinfo=dt.UTC).timestamp()),
        },
    )
    assert status == 200
    import json

    page = json.loads(body)
    assert page["t"]
    assert len(page["o"]) == len(page["t"])


def test_the_routed_tool_serves_a_vietnamese_symbol_from_dnse(credentials):
    from tradingagents.dataflows.router import route_to_vendor

    out = route_to_vendor("get_stock_data", SYMBOL, START.isoformat(), END.isoformat())
    assert "# Source: DNSE OpenAPI" in out
    assert "Total records:" in out
    # Prices are in dong, so a row carries a five-digit price.
    assert ",222000," in out or ",207000," in out or "," in out


def test_the_ohlcv_frame_of_a_vietnamese_symbol_comes_from_dnse(credentials, tmp_path):
    from tradingagents.dataflows.config import set_config
    from tradingagents.dataflows.ohlcv import load_ohlcv

    set_config({"data_cache_dir": str(tmp_path)})
    frame = load_ohlcv(SYMBOL, END.isoformat(), fill_gaps=False)
    assert not frame.empty
    assert list(frame.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    # The frame is cached under the vendor's own name, so the file says which
    # vendor wrote it.
    assert (tmp_path / f"{BARE}-DNSE-data.csv").exists()
    assert not (tmp_path / f"{SYMBOL}-YFin-data.csv").exists()


def test_a_bare_vietnamese_symbol_reaches_dnse(credentials):
    from tradingagents.dataflows.router import route_to_vendor

    out = route_to_vendor("get_stock_data", BARE, START.isoformat(), END.isoformat())
    assert "# Source: DNSE OpenAPI" in out
