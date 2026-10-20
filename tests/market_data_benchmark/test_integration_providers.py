"""Integration tests against the real APIs.

These are the only tests that may touch the network, and they only run when
asked for:

    pytest -m integration tests/market_data_benchmark

The DNSE ones need `DNSE_API_KEY` and `DNSE_API_SECRET` in the environment.
Without them they are skipped, not faked: a skipped test is honest about not
having run, where a mocked one would look like evidence.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

from tests.market_data_benchmark.config import BenchmarkConfig
from tests.market_data_benchmark.providers import DnseProvider, YahooProvider
from tests.market_data_benchmark.schema import PriceType, RequestStatus

pytestmark = pytest.mark.integration

START = dt.date(2026, 10, 13)
END = dt.date(2026, 10, 16)


def _dnse_client():
    dnse = pytest.importorskip("dnse", reason="the official DNSE SDK is not installed")
    if not (os.environ.get("DNSE_API_KEY") and os.environ.get("DNSE_API_SECRET")):
        pytest.skip("DNSE_API_KEY / DNSE_API_SECRET are not set")
    return dnse


@pytest.mark.integration
def test_dnse_returns_daily_bars():
    _dnse_client()
    provider = DnseProvider()
    result = provider.fetch_daily("VIC", START, END, BenchmarkConfig())

    assert result.request_status is RequestStatus.SUCCESS
    assert result.rows
    row = result.rows[0]
    assert row.price_type is PriceType.RAW
    assert row.price_unit == "thousand_VND"
    assert row.volume_unit == "shares"
    assert row.low <= row.open <= row.high
    assert row.low <= row.close <= row.high
    assert row.volume > 0


@pytest.mark.integration
def test_dnse_working_dates_cover_one_year_forward():
    _dnse_client()
    provider = DnseProvider()
    dates, error = provider.working_dates()
    assert error is None
    assert dates
    # Verified behaviour: the window runs forward from today, not backward.
    assert min(dates) >= dt.date.today()
    assert max(dates) <= dt.date.today() + dt.timedelta(days=366)


@pytest.mark.integration
def test_dnse_reports_the_vn30_members():
    _dnse_client()
    provider = DnseProvider()
    members, error = provider.index_members("VN30")
    assert error is None
    assert members
    assert all(member.get("symbol") for member in members)


@pytest.mark.integration
def test_yahoo_returns_bars_for_a_vn_symbol():
    provider = YahooProvider()
    result = provider.fetch_daily("VIC", START, END, BenchmarkConfig())

    assert result.request_status is RequestStatus.SUCCESS
    assert result.rows
    row = result.rows[0]
    assert row.price_unit == "VND"
    assert row.volume_unit == "shares"
    assert row.close > 0


@pytest.mark.integration
def test_a_bare_vietnamese_ticker_yields_nothing():
    """The reason the benchmark uses the `.VN` convention at all."""
    import yfinance as yf

    try:
        frame = yf.Ticker("VIC").history(start=START.isoformat(), end=END.isoformat())
    except Exception:
        # yfinance raises rather than returning an empty frame for a symbol
        # that does not exist; that is the same answer.
        frame = None
    assert frame is None or frame.empty
