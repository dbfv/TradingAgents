"""What every DNSE request shares: the client, its retry, and how it fails.

The API's shape was read from the official documentation and the official SDK
before this was written; see ``docs/research/dnse_openapi_notes.md``. In short:

* ``GET /price/ohlc`` with ``symbol``, ``type``, ``resolution`` (``1D`` for
  daily), ``from`` and ``to`` as epoch seconds. The response is **columnar**:
  ``t`` (epoch seconds), ``o``, ``h``, ``l``, ``c``, ``v``, plus a ``nextTime``
  cursor that is ``0`` when there is nothing more.
* Prices come back in **thousand dong** and volume in **shares**.
* The request is signed by the SDK; this module never signs anything itself.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time

from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorUnavailableError,
)

logger = logging.getLogger(__name__)

#: Yahoo's suffix for Vietnamese listings. A symbol carrying it is served by
#: DNSE (see ``market_vendors`` in the default config).
VN_SUFFIX = ".VN"

#: The API's own base URL and API version, per the SDK README.
PRODUCTION_BASE_URL = "https://openapi.dnse.com.vn"
SANDBOX_BASE_URL = "https://sb-openapi.dnse.com.vn"
DEFAULT_API_VERSION = "2026-07-23"

#: The only bar type the market-data endpoints need here.
BAR_TYPE_STOCK = "STOCK"
#: The only resolution that produces one bar per session.
RESOLUTION_DAILY = "1D"

#: The market trades in UTC+7 all year (no daylight saving).
MARKET_UTC_OFFSET = dt.timedelta(hours=7)

#: The price unit the API returns prices in.
PRICE_UNIT_THOUSAND_VND = "thousand_VND"
#: The volume unit the API returns volumes in.
VOLUME_UNIT_SHARES = "shares"


def is_vietnamese_symbol(symbol: str) -> bool:
    """Whether the symbol names a Vietnamese listing.

    Yahoo's convention for Vietnam is the ``.VN`` suffix (``VIC.VN``); a bare
    ``VIC`` is a 404 at Yahoo, so the suffix is what marks a symbol as
    Vietnamese.
    """
    return isinstance(symbol, str) and symbol.strip().upper().endswith(VN_SUFFIX)


def dnse_symbol(symbol: str) -> str:
    """The symbol the API is queried with: the bare exchange symbol."""
    bare = symbol.strip().upper()
    if bare.endswith(VN_SUFFIX):
        bare = bare[: -len(VN_SUFFIX)]
    return bare


def epoch_for(day: dt.date) -> int:
    """Epoch seconds for the start of ``day`` in market time."""
    midnight = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone(MARKET_UTC_OFFSET))
    return int(midnight.timestamp())


def session_date(timestamp: float | int) -> dt.date:
    """The session date of an epoch-seconds candle stamp, in market time.

    The API stamps a daily bar at 07:00 or 09:00 local depending on the
    period, so the time of day is not assumed — only the local date is taken,
    and both stamps fall on the session's own day.
    """
    return dt.datetime.fromtimestamp(float(timestamp), dt.timezone(MARKET_UTC_OFFSET)).date()


def dnse_client():
    """The official SDK client, built from the environment.

    Raises ``VendorNotConfiguredError`` when the credentials or the SDK are
    missing, so the routing layer treats it as "this vendor cannot answer"
    and moves to the next one — the same treatment every other keyed vendor
    gets.
    """
    api_key = os.environ.get("DNSE_API_KEY")
    api_secret = os.environ.get("DNSE_API_SECRET")
    if not api_key or not api_secret:
        raise VendorNotConfiguredError(
            "DNSE_API_KEY / DNSE_API_SECRET are not set; the DNSE OpenAPI cannot be used"
        )
    try:
        from dnse import DNSEClient
    except ImportError as exc:  # pragma: no cover - the SDK is a dependency
        raise VendorNotConfiguredError(
            f"the official DNSE SDK is not installed: {exc}"
        ) from exc
    return DNSEClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=os.environ.get("DNSE_BASE_URL", PRODUCTION_BASE_URL),
        api_version=os.environ.get("DNSE_API_VERSION", DEFAULT_API_VERSION),
    )


def dnse_api_version() -> str:
    """The API version the client will ask for."""
    return os.environ.get("DNSE_API_VERSION", DEFAULT_API_VERSION)


def dnse_retry(func, max_retries: int = 3, base_delay: float = 2.0):
    """Run a DNSE call, retrying a throttled or briefly failed one.

    Only a rate limit and a server error are retried: they are the server
    asking for patience. A rejected credential, a symbol the API does not know
    and a malformed request are answers, and answering them again would only
    waste requests.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except VendorUnavailableError:
            if attempt >= max_retries:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "DNSE OpenAPI unavailable, retrying in %.0fs (attempt %d/%d)",
                delay,
                attempt + 1,
                max_retries,
            )
            time.sleep(delay)


def raise_for_empty(symbol: str, what: str) -> None:
    """Report an empty answer as an absence of data for that symbol."""
    raise NoMarketDataError(symbol, symbol, what)


def raise_for_status(status: int, body: str | None, symbol: str) -> None:
    """Raise the error a DNSE HTTP status means.

    The API answers errors as ``{"status": …, "code": …, "message": …}``; the
    code and message are kept in the text so the log says what the API said.
    """
    detail = ""
    code = ""
    if body:
        try:
            page = json.loads(body) if isinstance(body, str) else body
            if isinstance(page, dict):
                detail = str(page.get("message") or "")[:200]
                code = str(page.get("code") or "")
        except (TypeError, ValueError):
            detail = str(body)[:200]
    text = f"{code}: {detail}" if code else detail

    if status == 401:
        raise VendorNotConfiguredError(f"DNSE rejected the credentials ({text})")
    if status == 403:
        raise VendorUnavailableError(f"DNSE refused the request: permission ({text})")
    if status == 404:
        raise NoMarketDataError(symbol, symbol, f"DNSE has no such symbol ({text})")
    if status == 429:
        raise VendorUnavailableError(f"DNSE rate limited the request ({text})")
    if status == 400:
        raise NoMarketDataError(symbol, symbol, f"DNSE rejected the request ({text})")
    raise VendorUnavailableError(f"DNSE request failed with status {status} ({text})")
