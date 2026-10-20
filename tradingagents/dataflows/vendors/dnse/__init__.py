"""DNSE OpenAPI: Vietnamese daily market data.

The DNSE OpenAPI serves the Vietnamese exchanges (HOSE, HNX, UPCOM) and, for
the daily bars, it is the better source than Yahoo Finance: the benchmark in
``tests/market_data_benchmark/`` measured it answering every ticker of the
VN30 with 99.9% session coverage, while Yahoo's ``.VN`` series carries a
per-symbol adjustment that differs from DNSE's own and is missing history for
symbols that listed recently.

Only the read-only market-data endpoints are used here. No order, account,
balance, position or trading-token endpoint is called, and the credentials are
read from the environment and never logged.
"""

from tradingagents.dataflows.vendors.dnse.market import get_DNSE_data_online
from tradingagents.dataflows.vendors.dnse.ohlcv import load_dnse_ohlcv

__all__ = ["get_DNSE_data_online", "load_dnse_ohlcv"]
