"""The comparison itself: what a difference is, and how it is counted."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.market_data_benchmark.metrics import (
    KIND_EXACT,
    KIND_MERGED,
    KIND_MISSING_DNSE,
    KIND_MISSING_YAHOO,
    KIND_PRICE_DIFFERENCE,
    KIND_SCALED_PRICE,
    KIND_SHIFTED,
    KIND_SHIFTED_SCALED,
    KIND_VOLUME_DIFFERENCE,
    agreement_by_period,
    build_pairs,
    classify_session,
    field_metrics,
    index_rows,
    mismatch_records,
    stability_report,
    symbol_metrics,
    validate_ohlc,
)
from tests.market_data_benchmark.schema import (
    CanonicalRow,
    PriceType,
    ProviderResult,
    RequestStatus,
)

DAY_1 = dt.date(2026, 9, 1)
DAY_2 = dt.date(2026, 9, 2)
DAY_3 = dt.date(2026, 9, 3)
SESSIONS = [DAY_1, DAY_2, DAY_3]

#: Three bars with genuinely different shapes and volumes, so that a constant
#: factor cannot turn one into another, and so that no two volumes are equal
#: or add up to another.
BARS = {
    DAY_1: (90.0, 95.0, 88.0, 92.0, 1000),
    DAY_2: (100.0, 106.0, 99.0, 104.0, 2000),
    DAY_3: (104.0, 108.0, 102.0, 105.0, 5000),
}


def _bar(
    day: dt.date,
    provider: str = "yahoo",
    scale: float = 1.0,
    volume: int | None = None,
    shape_from: dt.date | None = None,
) -> CanonicalRow:
    """A bar dated `day`, with the shape of `shape_from` (or of `day` itself)."""
    open_, high, low, close, base_volume = BARS[shape_from or day]
    return CanonicalRow(
        provider=provider,
        symbol="VIC",
        exchange="HOSE",
        date=day,
        open=open_ * scale,
        high=high * scale,
        low=low * scale,
        close=close * scale,
        volume=base_volume if volume is None else volume,
        price_type=PriceType.RAW,
        currency="VND",
        price_unit="VND",
        volume_unit="shares",
        retrieved_at_utc=dt.datetime(2026, 10, 20, tzinfo=dt.UTC),
        source_api_version="test",
        request_status=RequestStatus.SUCCESS,
        raw={"Close": close * scale, "Volume": base_volume},
    )


def _row(
    day: dt.date,
    close: float,
    volume: int = 1000,
    provider: str = "yahoo",
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> CanonicalRow:
    """A bar. The default is a realistic one — a bar that is flat cannot tell
    a scale apart from a shift, so the tests that need that distinction give
    each bar its own shape."""
    open_ = close * 0.99 if open_ is None else open_
    high = close * 1.01 if high is None else high
    low = close * 0.98 if low is None else low
    return CanonicalRow(
        provider=provider,
        symbol="VIC",
        exchange="HOSE",
        date=day,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        price_type=PriceType.RAW,
        currency="VND",
        price_unit="VND",
        volume_unit="shares",
        retrieved_at_utc=dt.datetime(2026, 10, 20, tzinfo=dt.UTC),
        source_api_version="test",
        request_status=RequestStatus.SUCCESS,
        raw={"Close": close, "Volume": volume},
    )


def _index(rows):
    return index_rows(rows, PriceType.RAW)


@pytest.mark.unit
class TestPairs:
    def test_only_shared_sessions_are_compared(self):
        yahoo = _index([_row(DAY_3, 100.0)])
        dnse = _index([_row(DAY_3, 100.0, provider="dnse")])
        pairs = build_pairs(yahoo, dnse, ["VIC"])
        assert {p.field for p in pairs} == {"open", "high", "low", "close", "volume"}
        assert all(p.matches for p in pairs)

    def test_a_session_only_one_side_has_is_not_compared(self):
        yahoo = _index([_row(DAY_3, 100.0)])
        dnse = _index([])
        assert build_pairs(yahoo, dnse, ["VIC"]) == []


@pytest.mark.unit
class TestFieldMetrics:
    def test_the_metrics_summarise_the_pairs(self):
        yahoo = _index([_row(DAY_3, 100.0, volume=1000)])
        dnse = _index([_row(DAY_3, 100.0, volume=1000, provider="dnse")])
        metrics = {m["field"]: m for m in field_metrics(build_pairs(yahoo, dnse, ["VIC"]))}
        assert metrics["close"]["comparable_pairs"] == 1
        assert metrics["close"]["exact_matches"] == 1
        assert metrics["close"]["mean_absolute_error"] == 0.0
        assert metrics["close"]["max_absolute_error"] == 0.0

    def test_the_percentage_error_is_absent_when_the_reference_is_zero(self):
        yahoo = _index([_row(DAY_3, 100.0, volume=500)])
        dnse = _index([_row(DAY_3, 100.0, volume=0, provider="dnse")])
        metrics = {m["field"]: m for m in field_metrics(build_pairs(yahoo, dnse, ["VIC"]))}
        assert metrics["volume"]["mean_absolute_percentage_error"] is None
        assert metrics["volume"]["pairs_without_a_reference"] == 1
        assert metrics["volume"]["exact_matches"] == 0


@pytest.mark.unit
class TestSessionClassification:
    def test_the_same_bar_is_exact(self):
        yahoo = _index([_bar(DAY_2)])
        dnse = _index([_bar(DAY_2, provider="dnse")])
        verdict = classify_session("VIC", DAY_2, yahoo[("VIC", DAY_2)], dnse, SESSIONS)
        assert verdict.kind == KIND_EXACT

    def test_a_bar_that_belongs_to_the_previous_session_is_a_shift(self):
        # Yahoo's bar for the second day is the first day's bar.
        yahoo = _index([_bar(DAY_2, shape_from=DAY_1)])
        dnse = _index(
            [
                _bar(DAY_1, provider="dnse"),
                _bar(DAY_2, provider="dnse"),
                _bar(DAY_3, provider="dnse"),
            ]
        )
        verdict = classify_session("VIC", DAY_2, yahoo[("VIC", DAY_2)], dnse, SESSIONS)
        assert verdict.kind == KIND_SHIFTED
        assert verdict.matched_session == DAY_1

    def test_a_shifted_bar_with_scaled_prices_is_named_as_such(self):
        yahoo = _index([_bar(DAY_2, shape_from=DAY_1, scale=1.02)])
        dnse = _index(
            [
                _bar(DAY_1, provider="dnse"),
                _bar(DAY_2, provider="dnse"),
                _bar(DAY_3, provider="dnse"),
            ]
        )
        verdict = classify_session("VIC", DAY_2, yahoo[("VIC", DAY_2)], dnse, SESSIONS)
        assert verdict.kind == KIND_SHIFTED_SCALED
        assert verdict.scale == pytest.approx(1.02, rel=1e-3)
        assert verdict.matched_session == DAY_1

    def test_the_same_session_with_scaled_prices_is_a_scaled_price(self):
        yahoo = _index([_bar(DAY_2, scale=1.02)])
        dnse = _index([_bar(DAY_2, provider="dnse")])
        verdict = classify_session("VIC", DAY_2, yahoo[("VIC", DAY_2)], dnse, SESSIONS)
        assert verdict.kind == KIND_SCALED_PRICE
        assert verdict.scale == pytest.approx(1.02, rel=1e-3)

    def test_a_price_difference_with_no_explanation_is_named(self):
        # The same volume, but a price set that is not a rescaling of the other's.
        yahoo = _index([_row(DAY_2, 150.0, volume=2000)])
        dnse = _index(
            [
                _bar(DAY_1, provider="dnse"),
                _bar(DAY_2, provider="dnse"),
                _bar(DAY_3, provider="dnse"),
            ]
        )
        verdict = classify_session("VIC", DAY_2, yahoo[("VIC", DAY_2)], dnse, SESSIONS)
        assert verdict.kind == KIND_PRICE_DIFFERENCE

    def test_two_sessions_merged_into_one_bar_is_named(self):
        yahoo = _index([_bar(DAY_2, volume=3000)])
        dnse = _index(
            [
                _bar(DAY_1, provider="dnse"),
                _bar(DAY_2, provider="dnse"),
                _bar(DAY_3, provider="dnse"),
            ]
        )
        # 3000 is the first session's volume plus the second one's.
        verdict = classify_session("VIC", DAY_2, yahoo[("VIC", DAY_2)], dnse, SESSIONS)
        assert verdict.kind == KIND_MERGED

    def test_a_volume_difference_on_its_own_is_named(self):
        yahoo = _index([_row(DAY_2, 100.0, volume=999)])
        dnse = _index([_row(DAY_2, 100.0, volume=1000, provider="dnse")])
        verdict = classify_session("VIC", DAY_2, yahoo[("VIC", DAY_2)], dnse, SESSIONS)
        assert verdict.kind == KIND_VOLUME_DIFFERENCE

    def test_a_row_only_one_side_has_is_named(self):
        yahoo = _index([_row(DAY_2, 100.0)])
        dnse = _index([])
        assert (
            classify_session("VIC", DAY_2, yahoo[("VIC", DAY_2)], dnse, SESSIONS).kind
            == KIND_MISSING_DNSE
        )
        assert (
            classify_session("VIC", DAY_2, None, yahoo, SESSIONS).kind == KIND_MISSING_YAHOO
        )


@pytest.mark.unit
class TestMismatchRecords:
    def test_only_the_differences_are_recorded(self):
        yahoo = _index([_row(DAY_3, 100.0, volume=1000)])
        dnse = _index([_row(DAY_3, 100.0, volume=2000, provider="dnse")])
        pairs = build_pairs(yahoo, dnse, ["VIC"], volume_tolerance=0)
        verdicts = {
            ("VIC", DAY_3): classify_session("VIC", DAY_3, yahoo[("VIC", DAY_3)], dnse, [DAY_3])
        }
        records = mismatch_records(pairs, verdicts, yahoo, dnse)
        assert [r["field"] for r in records] == ["volume"]
        assert records[0]["abs_diff"] == 1000
        assert records[0]["difference_kind"] == KIND_VOLUME_DIFFERENCE


@pytest.mark.unit
class TestSymbolAndPeriodMetrics:
    def test_the_symbol_summary_reports_the_dominant_kind(self):
        yahoo = _index([_row(DAY_2, 100.0)])
        dnse = _index([_row(DAY_2, 100.0, provider="dnse")])
        verdicts = [classify_session("VIC", DAY_2, yahoo[("VIC", DAY_2)], dnse, SESSIONS)]
        records = symbol_metrics(verdicts, yahoo, ["VIC"])
        assert records[0]["exact_sessions"] == 1
        assert records[0]["exact_rate"] == 1.0
        assert records[0]["dominant_kind"] == KIND_EXACT

    def test_the_period_summary_buckets_by_month(self):
        day = dt.date(2026, 9, 15)
        yahoo = _index([_row(day, 100.0)])
        dnse = _index([_row(day, 100.0, provider="dnse")])
        verdicts = [classify_session("VIC", day, yahoo[("VIC", day)], dnse, [day])]
        records = agreement_by_period(verdicts, months=1)
        assert records[0]["period"] == "2026-09"
        assert records[0]["exact_rate"] == 1.0


@pytest.mark.unit
class TestOhlcValidation:
    def test_an_inconsistent_bar_is_flagged(self):
        row = CanonicalRow(
            provider="yahoo",
            symbol="VIC",
            exchange="HOSE",
            date=DAY_3,
            open=100.0,
            high=90.0,
            low=80.0,
            close=95.0,
            volume=10,
            price_type=PriceType.RAW,
            currency="VND",
            price_unit="VND",
            volume_unit="shares",
            retrieved_at_utc=dt.datetime(2026, 10, 20, tzinfo=dt.UTC),
            source_api_version="test",
            request_status=RequestStatus.SUCCESS,
        )
        records = validate_ohlc([row])
        assert records[0]["ohlc_range_violations"] == 1

    def test_a_consistent_bar_is_not_flagged(self):
        records = validate_ohlc([_row(DAY_3, 100.0)])
        assert records[0]["ohlc_range_violations"] == 0
        assert records[0]["duplicate_dates"] == 0

    def test_the_two_series_are_checked_separately(self):
        raw = _row(DAY_3, 100.0)
        adjusted = CanonicalRow(
            provider="yahoo",
            symbol="VIC",
            exchange="HOSE",
            date=DAY_3,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=10,
            price_type=PriceType.ADJUSTED,
            currency="VND",
            price_unit="VND",
            volume_unit="shares",
            retrieved_at_utc=dt.datetime(2026, 10, 20, tzinfo=dt.UTC),
            source_api_version="test",
            request_status=RequestStatus.SUCCESS,
        )
        records = validate_ohlc([raw, adjusted])
        # The same day in two series is not a duplicate date.
        assert all(r["duplicate_dates"] == 0 for r in records)
        assert {r["price_type"] for r in records} == {"raw", "adjusted"}


@pytest.mark.unit
class TestStability:
    def test_a_repeated_fetch_reports_the_same_values(self):
        def result(value: float) -> ProviderResult:
            return ProviderResult(
                provider="yahoo",
                symbol="VIC",
                request_status=RequestStatus.SUCCESS,
                rows=[_row(DAY_3, value)],
            )

        runs = [
            {("yahoo", "VIC"): result(100.0)},
            {("yahoo", "VIC"): result(100.0)},
        ]
        records = stability_report(runs, ["VIC"])
        assert records[0]["values_changed_between_repetitions"] == 0
        assert records[0]["values_compared"] == 1

    def test_a_revised_value_is_reported_as_a_change(self):
        def result(value: float) -> ProviderResult:
            return ProviderResult(
                provider="yahoo",
                symbol="VIC",
                request_status=RequestStatus.SUCCESS,
                rows=[_row(DAY_3, value)],
            )

        runs = [
            {("yahoo", "VIC"): result(100.0)},
            {("yahoo", "VIC"): result(101.0)},
        ]
        records = stability_report(runs, ["VIC"])
        assert records[0]["values_changed_between_repetitions"] == 1
