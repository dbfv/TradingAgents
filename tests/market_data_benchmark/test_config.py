"""The window the benchmark compares over."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.market_data_benchmark.config import REFERENCE_RUN_DATE, resolve_window


@pytest.mark.unit
class TestWindow:
    def test_the_window_ends_on_the_last_day_before_the_run(self):
        window = resolve_window()
        assert window.end == REFERENCE_RUN_DATE - dt.timedelta(days=1)
        assert window.end == dt.date(2026, 10, 19)

    def test_the_window_spans_twelve_months(self):
        window = resolve_window()
        assert window.start == dt.date(2025, 10, 20)

    def test_the_run_day_is_excluded(self):
        # The session of the run day is incomplete for at least one provider.
        window = resolve_window()
        assert not window.contains(REFERENCE_RUN_DATE)

    def test_a_shorter_window_can_be_asked_for(self):
        window = resolve_window(months=3)
        assert window.start == dt.date(2026, 7, 20)
        assert window.end == dt.date(2026, 10, 19)

    def test_the_window_is_inclusive(self):
        window = resolve_window()
        assert window.contains(window.start)
        assert window.contains(window.end)
        assert not window.contains(window.start - dt.timedelta(days=1))
