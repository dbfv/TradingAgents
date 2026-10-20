"""The contract both provider adapters implement.

A provider is read-only, returns the canonical shape, and says plainly how each
request ended. The comparison code never learns which vendor it is talking to.
"""

from __future__ import annotations

import datetime as dt

from ..config import BenchmarkConfig
from ..schema import ProviderResult, RequestStatus


class Provider:
    """What a market-data source must be able to do for this benchmark."""

    #: Short name used in the artifacts.
    name: str = ""

    def available(self) -> bool:
        """Whether this provider can be used at all (credentials, dependency)."""
        raise NotImplementedError

    def not_available_status(self) -> RequestStatus:
        """Why it cannot be used, when `available()` is false."""
        return RequestStatus.NOT_CONFIGURED

    def fetch_daily(
        self,
        symbol: str,
        start: dt.date,
        end: dt.date,
        config: BenchmarkConfig,
    ) -> ProviderResult:
        """Daily OHLCV for `symbol` over the inclusive session range."""
        raise NotImplementedError

    def api_version(self) -> str:
        """The API version the provider was asked for."""
        return ""
