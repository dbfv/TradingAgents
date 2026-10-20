"""The canonical shape both providers return, and the statuses a request can end in.

One row is one (provider, symbol, session, price_type). `price_type` keeps the
unadjusted and the adjusted series apart; they are never merged, because a
comparison between them means nothing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum


class RequestStatus(StrEnum):
    """How a single provider request ended.

    The benchmark keeps these apart on purpose. The production router collapses
    everything into "no data" or "vendor unavailable", which cannot tell a
    benchmark whether a ticker is missing, a rate limit was hit, or the vendor
    was down.
    """

    SUCCESS = "SUCCESS"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEZONE_ERROR = "TIMEZONE_ERROR"
    UNSUPPORTED_OR_UNKNOWN = "UNSUPPORTED_OR_UNKNOWN"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    NOT_CONFIGURED = "NOT_CONFIGURED"

    @property
    def is_success(self) -> bool:
        return self is RequestStatus.SUCCESS

    @property
    def is_credential_problem(self) -> bool:
        """Whether the request failed on the credential, not on the market."""
        return self in (
            RequestStatus.AUTH_ERROR,
            RequestStatus.PERMISSION_ERROR,
            RequestStatus.NOT_CONFIGURED,
        )


class PriceType(StrEnum):
    """Which price series a row belongs to."""

    RAW = "raw"
    ADJUSTED = "adjusted"


@dataclass(frozen=True)
class CanonicalRow:
    """One session of one symbol from one provider.

    Prices are in VND (see `config.CURRENCY`), volume in shares. `raw` keeps the
    provider's own values so a difference can always be traced back to what the
    source actually said.
    """

    provider: str
    symbol: str
    exchange: str
    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: int
    price_type: PriceType
    currency: str
    price_unit: str
    volume_unit: str
    retrieved_at_utc: dt.datetime
    source_api_version: str
    request_status: RequestStatus
    raw: dict = field(default_factory=dict)

    def as_record(self) -> dict:
        """A flat dict, ready for a CSV row."""
        return {
            "provider": self.provider,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "date": self.date.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "price_type": self.price_type.value,
            "currency": self.currency,
            "price_unit": self.price_unit,
            "volume_unit": self.volume_unit,
            "retrieved_at_utc": self.retrieved_at_utc.isoformat(),
            "source_api_version": self.source_api_version,
            "request_status": self.request_status.value,
        }


@dataclass(frozen=True)
class ProviderError:
    """What went wrong, in enough detail to classify it, and no more."""

    status: RequestStatus
    http_status: int | None
    error_type: str
    message: str
    occurred_at_utc: dt.datetime

    def as_record(self) -> dict:
        return {
            "status": self.status.value,
            "http_status": self.http_status,
            "error_type": self.error_type,
            "message": self.message,
            "occurred_at_utc": self.occurred_at_utc.isoformat(),
        }


@dataclass
class ProviderResult:
    """The outcome of one (provider, symbol) request."""

    provider: str
    symbol: str
    request_status: RequestStatus
    rows: list[CanonicalRow] = field(default_factory=list)
    request_start: dt.datetime | None = None
    request_end: dt.datetime | None = None
    retrieved_at_utc: dt.datetime | None = None
    source_api_version: str = ""
    error: ProviderError | None = None
    elapsed_s: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def first_date(self) -> dt.date | None:
        return min((r.date for r in self.rows), default=None)

    @property
    def last_date(self) -> dt.date | None:
        return max((r.date for r in self.rows), default=None)

    @property
    def row_count(self) -> int:
        return len(self.rows)
