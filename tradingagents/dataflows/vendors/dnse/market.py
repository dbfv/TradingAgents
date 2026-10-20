"""Daily bars for a Vietnamese listing, read from the DNSE OpenAPI.

The output is the same CSV the Yahoo vendor produces, so the market analyst
and every downstream reader see one format: a comment header, then a
``Date,Open,High,Low,Close,Volume`` table.

Two things are normalised on the way out, because the API reports them
differently from Yahoo:

* **Prices** come back in *thousand dong* and are multiplied by 1000, so the
  CSV is in dong like every other price in the project.
* **Volume** comes back in *shares* and is used as it is.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Annotated

import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.vendors.dnse.common import (
    BAR_TYPE_STOCK,
    RESOLUTION_DAILY,
    dnse_client,
    dnse_retry,
    dnse_symbol,
    epoch_for,
    raise_for_empty,
    raise_for_status,
    session_date,
)

logger = logging.getLogger(__name__)

#: How many candles one request may return before the cursor is followed.
MAX_CANDLES_PER_REQUEST = 5000


def get_DNSE_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):
    """Daily OHLCV for a Vietnamese listing, as a CSV string.

    ``end_date`` is inclusive, like the Yahoo vendor's: the API is asked for
    one day more and the answer is trimmed back, so the last session is
    never dropped by a boundary the API does not document.
    """
    start_dt = dt.datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = dt.datetime.strptime(end_date, "%Y-%m-%d")
    if end_dt < start_dt:
        raise NoMarketDataError(symbol, symbol, f"end date {end_date} precedes start date {start_date}")

    bare = dnse_symbol(symbol)
    client = dnse_client()

    # The API's own window is [from, to] in epoch seconds; one day past the
    # window is requested and the answer trimmed, so the boundary cannot
    # decide whether the last session is included.
    cursor = epoch_for(start_dt.date())
    limit = epoch_for(end_dt.date() + dt.timedelta(days=1))
    rows: list[dict] = []
    seen: set[tuple] = set()

    while cursor < limit:
        status, body = dnse_retry(
            lambda cursor=cursor: client.get_ohlc(
                bar_type=BAR_TYPE_STOCK,
                query={
                    "symbol": bare,
                    "resolution": RESOLUTION_DAILY,
                    "from": cursor,
                    "to": limit,
                },
            )
        )
        if status != 200:
            raise_for_status(status, body, bare)

        try:
            page = json.loads(body) if isinstance(body, str) else body
        except (TypeError, ValueError) as exc:
            raise VendorUnavailableError(
                f"the DNSE response was not JSON: {exc}"
            ) from exc
        if not isinstance(page, dict) or "t" not in page:
            raise VendorUnavailableError(
                f"the DNSE response carried no candles: {str(page)[:200]}"
            )

        stamps = page.get("t") or []
        if len(stamps) > MAX_CANDLES_PER_REQUEST:
            raise VendorUnavailableError(
                f"the DNSE response carried {len(stamps)} candles, more than one request allows"
            )
        for index, stamp in enumerate(stamps):
            day = session_date(stamp)
            if not (start_dt.date() <= day <= end_dt.date()):
                continue
            key = (day, stamp)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "Date": day.isoformat(),
                    "Open": _price(page, "o", index),
                    "High": _price(page, "h", index),
                    "Low": _price(page, "l", index),
                    "Close": _price(page, "c", index),
                    "Volume": _volume(page, "v", index),
                }
            )

        next_time = page.get("nextTime") or 0
        if not next_time or int(next_time) <= cursor:
            break
        cursor = int(next_time)

    if not rows:
        raise_for_empty(symbol, f"rows between {start_date} and {end_date}")

    frame = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    csv_string = frame.to_csv(index=False)

    label = symbol.upper() if dnse_symbol(symbol) == symbol.upper() else f"{bare} (from {symbol})"
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(frame)}\n"
    header += f"# Source: DNSE OpenAPI (api_version={_api_version()})\n\n"

    return header + csv_string


def _api_version() -> str:
    from tradingagents.dataflows.vendors.dnse.common import dnse_api_version

    return dnse_api_version()


def _price(page: dict, key: str, index: int) -> float:
    """A price, in dong."""
    values = page.get(key) or []
    if index >= len(values) or values[index] is None:
        return float("nan")
    return round(float(values[index]) * 1000.0)


def _volume(page: dict, key: str, index: int) -> int:
    values = page.get(key) or []
    if index >= len(values) or values[index] is None:
        return 0
    return int(round(float(values[index])))
