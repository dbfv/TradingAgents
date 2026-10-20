"""The VN30 snapshot the benchmark runs against."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.market_data_benchmark.config import EXCHANGE, INDEX_NAME
from tests.market_data_benchmark.universe import load_universe


@pytest.mark.unit
class TestUniverse:
    def test_the_snapshot_holds_thirty_members(self):
        universe = load_universe()
        assert len(universe) == 30
        assert len(set(universe.symbols)) == 30

    def test_every_member_is_on_the_right_exchange_and_index(self):
        for member in load_universe():
            assert member.exchange == EXCHANGE
            assert member.index == INDEX_NAME

    def test_every_member_carries_its_provenance(self):
        for member in load_universe():
            assert member.effective_from == dt.date(2026, 8, 3)
            assert member.source_url.startswith("https://")
            assert member.retrieved_at

    def test_every_member_has_a_yahoo_symbol(self):
        for member in load_universe():
            assert member.yahoo_symbol == f"{member.symbol}.VN"

    def test_the_snapshot_does_not_contain_the_stale_members(self):
        # DGC, PLX and TPB left the index; BSR, MCH and TCX joined it.
        symbols = set(load_universe().symbols)
        assert "DGC" not in symbols
        assert "PLX" not in symbols
        assert "TPB" not in symbols
        assert {"BSR", "MCH", "TCX"} <= symbols
