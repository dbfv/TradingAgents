"""Read-only Yahoo Finance adapter for the benchmark.

Two things make this different from the production Yahoo path:

* The production path collapses every failure into "no data" or "vendor
  unavailable". A benchmark has to say *which* failure it was, so this adapter
  keeps the statuses apart.
* It fetches two series per symbol — the unadjusted one and the adjusted one —
  and labels them, because a comparison between an adjusted and an unadjusted
  series would mean nothing.

Nothing here mutates the production vendor configuration.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

from ..config import BenchmarkConfig
from ..normalization import (
    PRICE_UNIT_VND,
    VOLUME_UNIT_SHARES,
    epoch_to_session_date,
    normalize_price_vnd,
    normalize_volume,
    yahoo_symbol,
)
from ..schema import CanonicalRow, PriceType, ProviderError, ProviderResult, RequestStatus
from .base import Provider

logger = logging.getLogger(__name__)

YAHOO_API_VERSION = "yfinance"
YAHOO_HOST = "https://query2.finance.yahoo.com"


class YahooProvider(Provider):
    """Daily bars for a Vietnamese listing, read through `yfinance`."""

    name = "yahoo"

    def __init__(self) -> None:
        self._yfinance = None
        self._version = ""

    # -- availability ----------------------------------------------------

    def available(self) -> bool:
        try:
            import yfinance  # noqa: F401
        except ImportError:
            return False
        return True

    def api_version(self) -> str:
        if not self._version:
            try:
                import yfinance

                self._version = f"yfinance=={yfinance.__version__}"
            except Exception:  # pragma: no cover - defensive
                self._version = "yfinance"
        return self._version

    # -- fetching --------------------------------------------------------

    def fetch_daily(
        self,
        symbol: str,
        start: dt.date,
        end: dt.date,
        config: BenchmarkConfig,
    ) -> ProviderResult:
        """Fetch the unadjusted and the adjusted daily series for `symbol`.

        `end` is inclusive; `yfinance`'s `end=` is exclusive, so one day is
        added and the result is trimmed back to the window.
        """
        import yfinance as yf
        from yfinance.exceptions import YFPricesMissingError, YFRateLimitError

        ticker_symbol = yahoo_symbol(symbol)
        requested_start = start
        # yfinance's end is exclusive; ask for one day more and trim.
        exclusive_end = end + dt.timedelta(days=1)
        retrieved_at = dt.datetime.now(dt.UTC)

        result = ProviderResult(
            provider=self.name,
            symbol=symbol,
            request_status=RequestStatus.EMPTY_RESPONSE,
            request_start=requested_start,
            request_end=exclusive_end,
            retrieved_at_utc=retrieved_at,
            source_api_version=self.api_version(),
        )

        frames: dict[PriceType, object] = {}
        for price_type, auto_adjust in ((PriceType.RAW, False), (PriceType.ADJUSTED, True)):
            frame = self._fetch_frame(
                yf,
                ticker_symbol,
                requested_start,
                exclusive_end,
                auto_adjust,
                config,
                YFRateLimitError,
                YFPricesMissingError,
            )
            if isinstance(frame, ProviderError):
                result.request_status = frame.status
                result.error = frame
                return result
            frames[price_type] = frame

        for price_type, frame in frames.items():
            for row in self._to_rows(frame, symbol, price_type, retrieved_at):
                result.rows.append(row)

        if not result.rows:
            result.request_status = RequestStatus.EMPTY_RESPONSE
        else:
            result.request_status = RequestStatus.SUCCESS
        result.raw = {
            "yahoo_symbol": ticker_symbol,
            "requested_start": requested_start.isoformat(),
            "requested_end": exclusive_end.isoformat(),
            "raw_price_frames": {
                price_type.value: {
                    "rows": 0 if frame is None else len(frame),
                    "first": _first_date(frame),
                    "last": _last_date(frame),
                }
                for price_type, frame in frames.items()
            },
        }
        return result

    # -- internals -------------------------------------------------------

    def _fetch_frame(
        self,
        yf,
        ticker_symbol: str,
        start: dt.date,
        end: dt.date,
        auto_adjust: bool,
        config: BenchmarkConfig,
        rate_limit_error,
        missing_error,
    ):
        """One `history()` call, with a bounded retry on transient failures."""
        last_error: Exception | None = None
        for attempt in range(config.max_retries + 1):
            try:
                frame = yf.Ticker(ticker_symbol).history(
                    start=start.isoformat(),
                    end=end.isoformat(),
                    auto_adjust=auto_adjust,
                    raise_errors=True,
                )
                if frame is None or getattr(frame, "empty", True):
                    return None
                return frame
            except rate_limit_error as exc:
                last_error = exc
                if attempt >= config.max_retries:
                    return _error(
                        RequestStatus.RATE_LIMITED, exc, 429, dt.datetime.now(dt.UTC)
                    )
                _sleep(config.retry_base_delay_s * (2**attempt))
            except missing_error as exc:
                # yfinance raises this both for "this symbol does not exist" and
                # for "that request failed". Only the first is an answer about
                # the symbol; the second carries a status code and is a
                # transient failure, so it is classified rather than retried
                # forever.
                status = _status_of_missing(exc)
                if status is RequestStatus.SYMBOL_NOT_FOUND:
                    return _error(status, exc, 404, dt.datetime.now(dt.UTC))
                last_error = exc
                if attempt >= config.max_retries:
                    return _error(status, exc, _http_status_of(exc), dt.datetime.now(dt.UTC))
                _sleep(config.retry_base_delay_s * (2**attempt))
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = exc
                status = _classify_yahoo_exception(exc)
                if status is RequestStatus.SYMBOL_NOT_FOUND:
                    return _error(status, exc, 404, dt.datetime.now(dt.UTC))
                if attempt >= config.max_retries:
                    return _error(
                        status, exc, _http_status_of(exc), dt.datetime.now(dt.UTC)
                    )
                _sleep(config.retry_base_delay_s * (2**attempt))
        return _error(
            RequestStatus.UNSUPPORTED_OR_UNKNOWN,
            last_error,
            None,
            dt.datetime.now(dt.UTC),
        )

    def _to_rows(self, frame, symbol: str, price_type: PriceType, retrieved_at):
        """The frame's rows as canonical rows.

        Yahoo's index is tz-aware at the exchange's offset; the date is taken in
        that offset, so a positive-offset market does not shift a day.
        """
        if frame is None:
            return
        for timestamp, row in frame.iterrows():
            session = _session_date(timestamp)
            close = row.get("Close")
            if close is None or _is_nan(close):
                continue
            yield CanonicalRow(
                provider=self.name,
                symbol=symbol,
                exchange="HOSE",
                date=session,
                open=normalize_price_vnd(row.get("Open")),
                high=normalize_price_vnd(row.get("High")),
                low=normalize_price_vnd(row.get("Low")),
                close=normalize_price_vnd(close),
                volume=normalize_volume(row.get("Volume")),
                price_type=price_type,
                currency="VND",
                price_unit=PRICE_UNIT_VND,
                volume_unit=VOLUME_UNIT_SHARES,
                retrieved_at_utc=retrieved_at,
                source_api_version=self.api_version(),
                request_status=RequestStatus.SUCCESS,
                raw={
                    "Open": _plain(row.get("Open")),
                    "High": _plain(row.get("High")),
                    "Low": _plain(row.get("Low")),
                    "Close": _plain(close),
                    "Adj Close": _plain(row.get("Adj Close")),
                    "Volume": _plain(row.get("Volume")),
                    "Dividends": _plain(row.get("Dividends")),
                    "Stock Splits": _plain(row.get("Stock Splits")),
                    "yahoo_index_timestamp": str(timestamp),
                },
            )


# -- helpers ---------------------------------------------------------------


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _error(status, exc, http_status, occurred_at) -> ProviderError:
    return ProviderError(
        status=status,
        http_status=http_status,
        error_type=type(exc).__name__ if exc is not None else "UnknownError",
        message=str(exc)[:300] if exc is not None else "",
        occurred_at_utc=occurred_at,
    )


def _status_of_missing(exc) -> RequestStatus:
    """Classify a `YFPricesMissingError`.

    yfinance raises it both for "this symbol has no data" (an answer) and for
    "that request failed" (carrying a status code). The second must not be
    reported as a missing symbol.
    """
    debug_info = getattr(exc, "debug_info", "") or ""
    if "status_code" in debug_info:
        status = _http_status_of(exc)
        if status == 404:
            return RequestStatus.SYMBOL_NOT_FOUND
        if status == 429:
            return RequestStatus.RATE_LIMITED
        return RequestStatus.NETWORK_ERROR
    reason = getattr(exc, "yahoo_reason", None)
    if reason is None or str(reason).startswith("Data doesn't exist"):
        return RequestStatus.SYMBOL_NOT_FOUND
    return RequestStatus.EMPTY_RESPONSE


def _plain(value):
    """A numpy/pandas scalar as a plain Python value, for the raw trail."""
    if value is None:
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:  # pragma: no cover - defensive
            return str(value)
    return value


def _is_nan(value) -> bool:
    try:
        return value != value  # NaN is the only value unequal to itself
    except Exception:  # pragma: no cover - defensive
        return False


def _session_date(timestamp) -> dt.date:
    """The session date of a Yahoo index stamp.

    The index is tz-aware at the exchange's offset (UTC+7 for Vietnam), so the
    local date is the session date. A naive stamp is taken as-is.
    """
    try:
        return timestamp.date()
    except AttributeError:  # pragma: no cover - defensive
        return epoch_to_session_date(timestamp)


def _first_date(frame) -> str | None:
    if frame is None or getattr(frame, "empty", True):
        return None
    return _session_date(frame.index[0]).isoformat()


def _last_date(frame) -> str | None:
    if frame is None or getattr(frame, "empty", True):
        return None
    return _session_date(frame.index[-1]).isoformat()


def _http_status_of(exc) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def _classify_yahoo_exception(exc: Exception) -> RequestStatus:
    """Name the failure, so the report can count causes separately."""
    name = type(exc).__name__
    message = str(exc).lower()
    if "timezone" in name.lower() or "no timezone found" in message:
        return RequestStatus.TIMEZONE_ERROR
    if "ratelimit" in name.lower() or "429" in message:
        return RequestStatus.RATE_LIMITED
    if "not found" in message or "404" in message:
        return RequestStatus.SYMBOL_NOT_FOUND
    if "timeout" in name.lower() or "timed out" in message:
        return RequestStatus.NETWORK_ERROR
    if any(
        token in name.lower()
        for token in ("connection", "timeout", "network", "ssl", "proxy", "curl")
    ):
        return RequestStatus.NETWORK_ERROR
    return RequestStatus.UNSUPPORTED_OR_UNKNOWN
