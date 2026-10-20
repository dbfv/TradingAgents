"""The normalisation rules, which every comparison depends on."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.market_data_benchmark.normalization import (
    bare_symbol,
    date_to_epoch_start,
    dnse_symbol,
    epoch_to_session_date,
    normalize_price_thousand_vnd,
    normalize_price_vnd,
    normalize_volume,
    yahoo_symbol,
)


@pytest.mark.unit
class TestSymbols:
    def test_yahoo_symbol_gets_the_vn_suffix(self):
        assert yahoo_symbol("VIC") == "VIC.VN"
        assert yahoo_symbol("vic") == "VIC.VN"

    def test_yahoo_symbol_is_not_doubled(self):
        assert yahoo_symbol("VIC.VN") == "VIC.VN"

    def test_dnse_symbol_is_bare(self):
        assert dnse_symbol("vic") == "VIC"
        assert dnse_symbol("VIC.VN") == "VIC.VN"

    def test_bare_symbol_strips_the_suffix(self):
        assert bare_symbol("VIC.VN") == "VIC"
        assert bare_symbol("VIC") == "VIC"


@pytest.mark.unit
class TestSessionDates:
    def test_a_recent_dnse_stamp_is_the_session_day(self):
        # 2026-10-19 02:00 UTC is 09:00 in Vietnam: the session opened that day.
        assert epoch_to_session_date(1792375200) == dt.date(2026, 10, 19)

    def test_an_older_dnse_stamp_is_still_the_session_day(self):
        # 2012-03-20 00:00 UTC is 07:00 in Vietnam: the same calendar day.
        assert epoch_to_session_date(1332192000) == dt.date(2012, 3, 20)

    def test_a_stamp_just_before_midnight_utc_stays_on_its_own_day(self):
        # 2026-10-19 16:00 UTC is 23:00 in Vietnam on the same day.
        stamp = int(dt.datetime(2026, 10, 19, 16, 0, tzinfo=dt.UTC).timestamp())
        assert epoch_to_session_date(stamp) == dt.date(2026, 10, 19)

    def test_epoch_start_is_the_midnight_of_the_day_in_market_time(self):
        stamp = date_to_epoch_start(dt.date(2026, 10, 19))
        assert dt.datetime.fromtimestamp(stamp, dt.UTC) == dt.datetime(
            2026, 10, 18, 17, 0, tzinfo=dt.UTC
        )


@pytest.mark.unit
class TestUnits:
    def test_thousand_dong_becomes_dong(self):
        assert normalize_price_thousand_vnd(218.4) == 218400.0
        assert normalize_price_thousand_vnd(207) == 207000.0

    def test_the_float_residue_is_removed(self):
        # 130.7 * 1000 is 130699.99999999999 in binary floating point.
        assert normalize_price_thousand_vnd(130.7) == 130700.0
        assert normalize_price_thousand_vnd(256.1) == 256100.0

    def test_dong_is_only_rounded(self):
        assert normalize_price_vnd(218400.4) == 218400.0
        assert normalize_price_vnd(218399.5) == 218400.0

    def test_volume_is_a_whole_number_of_shares(self):
        assert normalize_volume(3133800) == 3133800
        assert normalize_volume(3133800.4) == 3133800

    def test_a_missing_value_stays_missing(self):
        import math

        assert math.isnan(normalize_price_thousand_vnd(None))
        assert math.isnan(normalize_price_vnd(float("nan")))
