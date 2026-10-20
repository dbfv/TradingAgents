"""Which vendor serves a symbol's daily prices.

The routing layer picks a vendor per *tool*, but a symbol's market decides
which vendor can serve it at all: Yahoo Finance has no Vietnamese listing
under a bare ticker, and the DNSE OpenAPI has no US listing. So the vendor
chain is resolved per symbol, from the ``market_vendors`` configuration:

    "market_vendors": {
        ".VN": "dnse,yfinance",   # Vietnamese listings: DNSE first, Yahoo after
    }

A symbol with no suffix is ambiguous — it may be a US listing — so it follows
the configured chain, and only reaches DNSE through the ``fallback_vendors``
path, when the configured vendors answer that they have no such symbol.
"""

from __future__ import annotations

import logging

import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorError
from tradingagents.dataflows.vendors.dnse.common import VN_SUFFIX, is_vietnamese_symbol

logger = logging.getLogger(__name__)

#: The vendors that can serve daily OHLCV, and how to ask each for it.
#: The loader is imported lazily so a missing optional dependency cannot
#: break the import of the package.
_OHLCV_LOADERS = {
    "dnse": "tradingagents.dataflows.vendors.dnse.ohlcv:load_dnse_ohlcv",
    "yfinance": "tradingagents.dataflows.vendors.yahoo.ohlcv:load_ohlcv",
}


def symbol_suffix(symbol: str) -> str:
    """The symbol's market suffix, upper-cased (``.VN`` for ``VIC.VN``)."""
    if not isinstance(symbol, str):
        return ""
    bare = symbol.strip().upper()
    if "." not in bare:
        return ""
    return "." + bare.rsplit(".", 1)[1]


def market_vendor_chain(symbol: str, method: str = "get_stock_data") -> list[str]:
    """The vendors to try for this symbol, in order.

    The market rule wins for the symbols it covers: a Vietnamese listing goes
    to DNSE first even when the category default says Yahoo, because Yahoo
    cannot serve it. The configured chain follows, so a symbol the market rule
    does not cover keeps exactly the chain it had.
    """
    config = get_config()
    chain = _configured_chain(config, method)
    market_chain = _parse_chain(config.get("market_vendors", {}).get(symbol_suffix(symbol)))
    if not market_chain:
        return chain
    supported = _supported_vendors(method)
    market_chain = [v for v in market_chain if v in supported]
    if not market_chain:
        return chain
    return market_chain + [v for v in chain if v not in market_chain]


def fallback_vendor_chain(symbol: str, method: str = "get_stock_data") -> list[str]:
    """The vendors to try when the configured ones have no data for the symbol.

    These run only after every configured vendor has answered "I have no such
    symbol" — the case where a bare Vietnamese ticker lands, since Yahoo
    answers a bare ``VIC`` with a 404 and DNSE lists it.
    """
    config = get_config()
    chain = _parse_chain(config.get("fallback_vendors", {}).get(method))
    supported = _supported_vendors(method)
    return [v for v in chain if v in supported and v not in market_vendor_chain(symbol, method)]


def _configured_chain(config: dict, method: str) -> list[str]:
    """The chain the tool/category configuration asks for, unchanged."""
    from tradingagents.dataflows.router import VENDOR_METHODS, get_category_for_method, get_vendor

    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method)
    primary = [v.strip() for v in vendor_config.split(",")]
    available = list(VENDOR_METHODS[method].keys())
    explicit = [v for v in primary if v and v != "default"]
    if explicit:
        return [v for v in explicit if v in available]
    return available


def _parse_chain(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _supported_vendors(method: str) -> set[str]:
    from tradingagents.dataflows.router import VENDOR_METHODS

    return set(VENDOR_METHODS.get(method, {}).keys())

def load_ohlcv(symbol: str, as_of_date: str, fill_gaps: bool = True) -> pd.DataFrame:
    """Daily OHLCV for a symbol, from the vendor that serves its market.

    This is the single entry point the indicators and the verified market
    snapshot read, so the prices an agent is told to trust and the prices it
    is shown come from the same source.
    """
    chain = market_vendor_chain(symbol, "get_stock_data")
    last_error: VendorError | None = None
    for vendor in chain:
        loader = _loader_for(vendor)
        if loader is None:
            continue
        try:
            frame = loader(symbol, as_of_date, fill_gaps=fill_gaps)
        except VendorError as exc:
            last_error = exc
            logger.warning(
                "Vendor %r could not serve %s (%s); trying the next one.", vendor, symbol, exc
            )
            continue
        logger.info("Daily prices for %s were served by the %r vendor.", symbol, vendor)
        return frame
    if last_error is not None:
        raise last_error
    raise NoMarketDataError(symbol, symbol, "no vendor is configured to serve daily prices")


def _loader_for(vendor: str):
    """The OHLCV loader of a vendor, or None when it has none."""
    target = _OHLCV_LOADERS.get(vendor)
    if not target:
        return None
    module_path, _, attribute = target.partition(":")
    module = __import__(module_path, fromlist=[attribute])
    return getattr(module, attribute)


__all__ = [
    "VN_SUFFIX",
    "fallback_vendor_chain",
    "is_vietnamese_symbol",
    "load_ohlcv",
    "market_vendor_chain",
    "symbol_suffix",
]
