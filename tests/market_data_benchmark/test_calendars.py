"""The session calendar: how it is derived, and how it is judged."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.market_data_benchmark.calendars import (
    compare_to_official,
    derive_sessions,
    merge_sessions,
    neighbour_anchored_gaps,
)
from tests.market_data_benchmark.schema import CanonicalRow, PriceType, RequestStatus


def _row(symbol: str, day: dt.date, volume: int) -> CanonicalRow:
    return CanonicalRow(
        provider="yahoo",
        symbol=symbol,
        exchange="HOSE",
        date=day,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=volume,
        price_type=PriceType.RAW,
        currency="VND",
        price_unit="VND",
        volume_unit="shares",
        retrieved_at_utc=dt.datetime(2026, 10, 20, tzinfo=dt.UTC),
        source_api_version="test",
        request_status=RequestStatus.SUCCESS,
    )


@pytest.mark.unit
class TestDeriveSessions:
    def test_a_row_without_volume_is_not_a_session(self):
        # Yahoo's holiday filler: a bar with no trade in it.
        rows = [_row("VIC", dt.date(2026, 9, 1), 0)]
        assert derive_sessions(rows) == set()

    def test_a_row_with_volume_is_a_session(self):
        rows = [_row("VIC", dt.date(2026, 9, 3), 1000)]
        assert derive_sessions(rows) == {dt.date(2026, 9, 3)}

    def test_the_union_keeps_a_session_one_provider_dropped(self):
        yahoo = [_row("VIC", dt.date(2026, 9, 3), 0)]
        dnse = [_row("VIC", dt.date(2026, 9, 3), 1000)]
        merged = merge_sessions(derive_sessions(yahoo), derive_sessions(dnse))
        assert merged == [dt.date(2026, 9, 3)]


@pytest.mark.unit
class TestAnchoredGaps:
    def test_a_gap_between_two_traded_sessions_is_a_gap(self):
        sessions = [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]
        present = {sessions[0], sessions[2]}
        assert neighbour_anchored_gaps(present, sessions) == [sessions[1]]

    def test_a_missing_first_or_last_session_is_not_a_gap(self):
        sessions = [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]
        present = {sessions[1], sessions[2]}
        assert neighbour_anchored_gaps(present, sessions) == []

    def test_a_suspended_ticker_is_not_counted_as_a_gap(self):
        # The ticker did not trade on the session before either.
        sessions = [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]
        present = {sessions[0], sessions[2]}
        assert neighbour_anchored_gaps(present, sessions) == [sessions[1]]
        present = {sessions[2]}
        assert neighbour_anchored_gaps(present, sessions) == []


@pytest.mark.unit
class TestOfficialComparison:
    def test_an_identical_calendar_agrees_completely(self):
        days = [dt.date(2026, 9, 1), dt.date(2026, 9, 2)]
        result = compare_to_official(days, days)
        assert result["validated"] is True
        assert result["agreement"] == 1.0
        assert result["only_derived"] == []
        assert result["only_official"] == []

    def test_a_difference_is_reported_on_both_sides(self):
        derived = [dt.date(2026, 9, 1), dt.date(2026, 9, 2)]
        official = [dt.date(2026, 9, 2), dt.date(2026, 9, 3)]
        result = compare_to_official(derived, official)
        assert result["agreement"] == pytest.approx(1 / 3)
        assert result["only_derived"] == [dt.date(2026, 9, 1)]
        assert result["only_official"] == [dt.date(2026, 9, 3)]

    def test_without_an_official_calendar_nothing_is_claimed(self):
        result = compare_to_official([dt.date(2026, 9, 1)], [])
        assert result["validated"] is False
        assert result["agreement"] is None
