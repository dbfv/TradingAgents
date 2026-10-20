"""Read-only DNSE OpenAPI adapter for the benchmark.

Everything this adapter calls was read from the official documentation and the
official SDK before it was written; see
`docs/research/dnse_openapi_notes.md`. The short version:

* `get_ohlc(bar_type, query)` → `GET /price/ohlc`, with `symbol`, `type`,
  `resolution` (`1D` for daily), `from` and `to` as epoch seconds.
* The response is **columnar**: `t` (epoch seconds), `o`, `h`, `l`, `c`, `v`,
  plus a `nextTime` cursor (`0` when there is nothing more).
* Prices come back in **thousand dong** and volume in **shares**; both are
  normalised here.
* `get_working_dates()` → `GET /market/working-dates` covers **one year forward
  from today**, so it cannot serve as a historical calendar.
* `get_instruments(index_name="VN30")` → `GET /market/instruments`, used to
  cross-check index membership and ticker metadata.

Only read-only market-data endpoints are called. No order, account, balance,
position or trading-token endpoint is used, and no credential is logged.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time

from ..config import BenchmarkConfig
from ..normalization import (
    PRICE_UNIT_THOUSAND_VND,
    VOLUME_UNIT_SHARES,
    date_to_epoch_start,
    dnse_symbol,
    epoch_to_session_date,
    normalize_price_thousand_vnd,
    normalize_volume,
)
from ..schema import CanonicalRow, PriceType, ProviderError, ProviderResult, RequestStatus
from .base import Provider

logger = logging.getLogger(__name__)

PRODUCTION_BASE_URL = "https://openapi.dnse.com.vn"
SANDBOX_BASE_URL = "https://sb-openapi.dnse.com.vn"
DEFAULT_API_VERSION = "2026-07-23"
OHLC_RESOLUTION = "1D"
OHLC_BAR_TYPE = "STOCK"
#: How many candles to ask for per request. The API paginates with `nextTime`.
_MAX_CANDLES_PER_REQUEST = 5000


class DnseProvider(Provider):
    """Daily bars and reference data from DNSE OpenAPI."""

    name = "dnse"

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
        client=None,
    ) -> None:
        self._api_key = os.environ.get("DNSE_API_KEY") if api_key is None else api_key
        self._api_secret = os.environ.get("DNSE_API_SECRET") if api_secret is None else api_secret
        self._base_url = base_url or os.environ.get("DNSE_BASE_URL", PRODUCTION_BASE_URL)
        self._api_version = api_version or os.environ.get("DNSE_API_VERSION", DEFAULT_API_VERSION)
        #: An injected client is used as-is; that is how the unit tests drive
        #: the adapter without a network.
        self._client = client
        self._client_error: str | None = None

    # -- availability ----------------------------------------------------

    def available(self) -> bool:
        if not self._api_key or not self._api_secret:
            return False
        return self._ensure_client() is not None

    def not_available_status(self) -> RequestStatus:
        if not self._api_key or not self._api_secret:
            return RequestStatus.NOT_CONFIGURED
        return RequestStatus.UNSUPPORTED_OR_UNKNOWN

    def api_version(self) -> str:
        return self._api_version

    def _ensure_client(self):
        """The official SDK client, created once."""
        if self._client is not None or self._client_error is not None:
            return self._client
        if not self._api_key or not self._api_secret:
            self._client_error = "DNSE_API_KEY / DNSE_API_SECRET are not set"
            return None
        try:
            from dnse import DNSEClient
        except Exception as exc:  # noqa: BLE001 - the reason is reported, not raised
            self._client_error = f"the official DNSE SDK is not importable: {exc}"
            return None
        self._client = DNSEClient(
            api_key=self._api_key,
            api_secret=self._api_secret,
            base_url=self._base_url,
            api_version=self._api_version,
        )
        return self._client

    # -- fetching --------------------------------------------------------

    def fetch_daily(
        self,
        symbol: str,
        start: dt.date,
        end: dt.date,
        config: BenchmarkConfig,
    ) -> ProviderResult:
        """Daily OHLCV for `symbol` over the inclusive session range."""
        client = self._ensure_client()
        retrieved_at = dt.datetime.now(dt.UTC)
        result = ProviderResult(
            provider=self.name,
            symbol=symbol,
            request_status=RequestStatus.EMPTY_RESPONSE,
            request_start=dt.datetime.combine(start, dt.time(), tzinfo=dt.UTC),
            request_end=dt.datetime.combine(end, dt.time(), tzinfo=dt.UTC),
            retrieved_at_utc=retrieved_at,
            source_api_version=self._api_version,
        )
        if client is None:
            result.request_status = RequestStatus.NOT_CONFIGURED
            result.error = ProviderError(
                status=RequestStatus.NOT_CONFIGURED,
                http_status=None,
                error_type="MissingDependency",
                message=self._client_error or "the DNSE SDK is not available",
                occurred_at_utc=retrieved_at,
            )
            return result

        # The API's own window is [from, to] in epoch seconds; one day past the
        # window is requested and the result is trimmed back, so the boundary
        # cannot decide whether the last session is included.
        cursor = date_to_epoch_start(start)
        limit = date_to_epoch_start(end + dt.timedelta(days=1))
        payload: dict = {}
        candles: list[tuple] = []
        attempts = 0
        max_attempts = config.max_retries + 1

        while cursor < limit and attempts <= config.max_retries + 2:
            attempts += 1
            status, body = self._call(client, symbol, cursor, limit, config, attempts, max_attempts)
            if status is None:
                # Every attempt failed with a transient error.
                result.request_status = RequestStatus.NETWORK_ERROR
                result.error = ProviderError(
                    status=RequestStatus.NETWORK_ERROR,
                    http_status=None,
                    error_type="RetriesExhausted",
                    message=f"the request failed after {attempts} attempts",
                    occurred_at_utc=retrieved_at,
                )
                return result
            if status != 200:
                error = _error_for_status(status, body, retrieved_at)
                result.request_status = error.status
                result.error = error
                return result
            try:
                page = json.loads(body) if isinstance(body, str) else body
            except (TypeError, ValueError) as exc:
                result.request_status = RequestStatus.UNSUPPORTED_OR_UNKNOWN
                result.error = ProviderError(
                    status=RequestStatus.UNSUPPORTED_OR_UNKNOWN,
                    http_status=status,
                    error_type="InvalidJSON",
                    message=f"the response was not JSON: {exc}",
                    occurred_at_utc=retrieved_at,
                )
                return result

            if not isinstance(page, dict) or "t" not in page:
                result.request_status = RequestStatus.UNSUPPORTED_OR_UNKNOWN
                result.error = ProviderError(
                    status=RequestStatus.UNSUPPORTED_OR_UNKNOWN,
                    http_status=status,
                    error_type="UnexpectedSchema",
                    message=f"the response has no candle arrays: {str(page)[:200]}",
                    occurred_at_utc=retrieved_at,
                )
                return result

            payload = page
            stamps = page.get("t") or []
            for index, stamp in enumerate(stamps):
                candles.append(
                    (
                        epoch_to_session_date(stamp),
                        _at(page, "o", index),
                        _at(page, "h", index),
                        _at(page, "l", index),
                        _at(page, "c", index),
                        _at(page, "v", index),
                        stamp,
                    )
                )
            next_time = page.get("nextTime") or 0
            if not next_time or next_time <= cursor:
                break
            cursor = int(next_time)
            time.sleep(config.dnse_request_delay_s)

        for session, open_, high, low, close, volume, stamp in candles:
            if not (start <= session <= end):
                continue
            result.rows.append(
                CanonicalRow(
                    provider=self.name,
                    symbol=symbol,
                    exchange="HOSE",
                    date=session,
                    open=normalize_price_thousand_vnd(open_),
                    high=normalize_price_thousand_vnd(high),
                    low=normalize_price_thousand_vnd(low),
                    close=normalize_price_thousand_vnd(close),
                    volume=normalize_volume(volume),
                    # DNSE publishes raw OHLC only. There is no adjusted series
                    # in the market-data API, so the rows are labelled raw and
                    # the adjusted comparison is reported as not comparable.
                    price_type=PriceType.RAW,
                    currency="VND",
                    price_unit=PRICE_UNIT_THOUSAND_VND,
                    volume_unit=VOLUME_UNIT_SHARES,
                    retrieved_at_utc=retrieved_at,
                    source_api_version=self._api_version,
                    request_status=RequestStatus.SUCCESS,
                    raw={
                        "t": stamp,
                        "o": open_,
                        "h": high,
                        "l": low,
                        "c": close,
                        "v": volume,
                        "price_unit": "thousand_VND",
                    },
                )
            )

        result.request_status = RequestStatus.SUCCESS if result.rows else RequestStatus.EMPTY_RESPONSE
        result.raw = {
            "endpoint": "GET /price/ohlc",
            "query": {
                "symbol": dnse_symbol(symbol),
                "type": OHLC_BAR_TYPE,
                "resolution": OHLC_RESOLUTION,
                "from": cursor,
                "to": limit,
            },
            "nextTime": payload.get("nextTime"),
            "candles_returned": len(candles),
        }
        return result

    # -- internals -------------------------------------------------------

    def _call(
        self,
        client,
        symbol: str,
        cursor: int,
        limit: int,
        config: BenchmarkConfig,
        attempt: int,
        max_attempts: int,
    ):
        """One `get_ohlc` call, with a bounded retry on a transient failure.

        A 429 or a 5xx is the server asking for patience; an auth or a symbol
        error is an answer and is returned as it is. The retry never turns a
        symbol that does not exist into a request that is repeated.
        """
        status, body = client.get_ohlc(
            bar_type=OHLC_BAR_TYPE,
            query={
                "symbol": dnse_symbol(symbol),
                "resolution": OHLC_RESOLUTION,
                "from": cursor,
                "to": limit,
            },
        )
        if status == 200 or attempt >= max_attempts:
            return status, body
        if status == 429 or 500 <= status < 600:
            time.sleep(config.retry_base_delay_s * (2 ** (attempt - 1)))
            return self._call(client, symbol, cursor, limit, config, attempt + 1, max_attempts)
        return status, body

    # -- reference data --------------------------------------------------

    def working_dates(self) -> tuple[list[dt.date], ProviderError | None]:
        """The official working dates, as returned by the API.

        Verified to cover one year **forward** from today, so it is a calendar
        for the future, not for a back-test window.
        """
        client = self._ensure_client()
        if client is None:
            return [], ProviderError(
                status=RequestStatus.NOT_CONFIGURED,
                http_status=None,
                error_type="MissingDependency",
                message=self._client_error or "the DNSE SDK is not available",
                occurred_at_utc=dt.datetime.now(dt.UTC),
            )
        status, body = client.get_working_dates()
        if status != 200:
            return [], _error_for_status(status, body, dt.datetime.now(dt.UTC))
        try:
            page = json.loads(body) if isinstance(body, str) else body
        except (TypeError, ValueError) as exc:
            return [], ProviderError(
                status=RequestStatus.UNSUPPORTED_OR_UNKNOWN,
                http_status=status,
                error_type="InvalidJSON",
                message=str(exc)[:300],
                occurred_at_utc=dt.datetime.now(dt.UTC),
            )
        raw_dates = page.get("workingDates") if isinstance(page, dict) else page
        dates = [dt.date.fromisoformat(value) for value in (raw_dates or [])]
        return dates, None

    def index_members(self, index_name: str = "VN30") -> tuple[list[dict], ProviderError | None]:
        """The members of an index, as the API reports them.

        `GET /market/instruments?indexName=VN30` is a server-side index filter,
        so it answers "which symbols are in VN30" rather than "which symbols
        exist".
        """
        client = self._ensure_client()
        if client is None:
            return [], ProviderError(
                status=RequestStatus.NOT_CONFIGURED,
                http_status=None,
                error_type="MissingDependency",
                message=self._client_error or "the DNSE SDK is not available",
                occurred_at_utc=dt.datetime.now(dt.UTC),
            )
        status, body = client.get_instruments(index_name=index_name, limit=100, page=1)
        if status != 200:
            return [], _error_for_status(status, body, dt.datetime.now(dt.UTC))
        try:
            page = json.loads(body) if isinstance(body, str) else body
        except (TypeError, ValueError) as exc:
            return [], ProviderError(
                status=RequestStatus.UNSUPPORTED_OR_UNKNOWN,
                http_status=status,
                error_type="InvalidJSON",
                message=str(exc)[:300],
                occurred_at_utc=dt.datetime.now(dt.UTC),
            )
        data = page.get("data") if isinstance(page, dict) else page
        return list(data or []), None

    def session_trades(self, symbol: str, day: dt.date) -> tuple[dict | None, ProviderError | None]:
        """The session's own trade summary, from the trade feed.

        This is a *third* dataset, independent of the daily bar, so it can
        say which of the two daily series reflects what actually traded. Each
        trade carries the session's running open, high and low, and the last
        trade of the session carries the final close and the session's
        cumulative volume.
        """
        client = self._ensure_client()
        occurred = dt.datetime.now(dt.UTC)
        if client is None:
            return None, ProviderError(
                status=RequestStatus.NOT_CONFIGURED,
                http_status=None,
                error_type="MissingDependency",
                message=self._client_error or "the DNSE SDK is not available",
                occurred_at_utc=occurred,
            )
        start = date_to_epoch_start(day)
        end = date_to_epoch_start(day + dt.timedelta(days=1))
        status, body = client.get_trades(
            dnse_symbol(symbol),
            board_id=None,
            from_date=start,
            to_date=end,
            limit=500,
            order="DESC",
        )
        if status != 200:
            return None, _error_for_status(status, body, occurred)
        try:
            page = json.loads(body) if isinstance(body, str) else body
        except (TypeError, ValueError) as exc:
            return None, ProviderError(
                status=RequestStatus.UNSUPPORTED_OR_UNKNOWN,
                http_status=status,
                error_type="InvalidJSON",
                message=str(exc)[:300],
                occurred_at_utc=occurred,
            )
        trades = (page or {}).get("trades") or []
        if not trades:
            return None, None
        # The feed mixes the trading boards, and each board reports its own
        # running total, so the largest cumulative volume on the page is the
        # closest thing to the session's end that one page can give.
        last = max(
            trades,
            key=lambda t: float(t.get("totalVolumeTraded") or 0),
        )
        return (
            {
                "trades_returned": len(trades),
                "last_time": last.get("time"),
                "last_price": last.get("matchPrice"),
                "total_volume_traded": last.get("totalVolumeTraded"),
                "boards": sorted({t.get("boardId") for t in trades if t.get("boardId")}),
            },
            None,
        )

    def security_definition(self, symbol: str) -> tuple[list[dict], ProviderError | None]:
        """The per-board security definition of a symbol."""
        client = self._ensure_client()
        if client is None:
            return [], ProviderError(
                status=RequestStatus.NOT_CONFIGURED,
                http_status=None,
                error_type="MissingDependency",
                message=self._client_error or "the DNSE SDK is not available",
                occurred_at_utc=dt.datetime.now(dt.UTC),
            )
        status, body = client.get_security_definition(symbol=dnse_symbol(symbol))
        if status != 200:
            return [], _error_for_status(status, body, dt.datetime.now(dt.UTC))
        try:
            page = json.loads(body) if isinstance(body, str) else body
        except (TypeError, ValueError) as exc:
            return [], ProviderError(
                status=RequestStatus.UNSUPPORTED_OR_UNKNOWN,
                http_status=status,
                error_type="InvalidJSON",
                message=str(exc)[:300],
                occurred_at_utc=dt.datetime.now(dt.UTC),
            )
        return list(page or []), None


# -- helpers ---------------------------------------------------------------


def _at(page: dict, key: str, index: int):
    values = page.get(key) or []
    if index >= len(values):
        return None
    return values[index]


def _error_for_status(status: int, body, occurred_at: dt.datetime) -> ProviderError:
    """Name an HTTP status, so the report can count the causes separately."""
    message = ""
    code = ""
    if body:
        message = str(body)[:300]
        try:
            page = json.loads(body) if isinstance(body, str) else body
            if isinstance(page, dict):
                message = str(page.get("message") or message)[:300]
                code = str(page.get("code") or "")
        except (TypeError, ValueError):
            pass

    if status in (401,):
        outcome = RequestStatus.AUTH_ERROR
    elif status in (403,):
        outcome = RequestStatus.PERMISSION_ERROR
    elif status == 404:
        outcome = RequestStatus.SYMBOL_NOT_FOUND
    elif status == 429:
        outcome = RequestStatus.RATE_LIMITED
    elif status == 400:
        outcome = RequestStatus.INVALID_PARAMETER
    elif 500 <= status < 600:
        outcome = RequestStatus.NETWORK_ERROR
    else:
        outcome = RequestStatus.UNSUPPORTED_OR_UNKNOWN

    return ProviderError(
        status=outcome,
        http_status=status,
        error_type=code or f"HTTP{status}",
        message=message,
        occurred_at_utc=occurred_at,
    )
