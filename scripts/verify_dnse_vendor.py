"""Prove which vendor actually served the daily market data.

Run it with the credentials of the source you expect to serve:

    python scripts/verify_dnse_vendor.py --symbol VIC.VN

It instruments the two HTTP transports (the DNSE SDK's urllib3 pool and
yfinance's curl_cffi session), exercises each stage of the price path in
turn, and reports for every stage which vendor answered and how many requests
each host received. The cache is cleared before every stage, so a stage that
served the DNSE API really did call it rather than read yesterday's file.

Exit code 0 means every stage was served by the expected vendor; 1 means one
of them was not.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DNSE_HOST_MARKERS = ("dnse.com.vn",)
YAHOO_HOST_MARKERS = ("finance.yahoo.com", "yahoo.com")

_counts: dict[str, int] = {"dnse": 0, "yahoo": 0, "other": 0}


def _classify(url: str) -> str:
    lowered = (url or "").lower()
    if any(marker in lowered for marker in DNSE_HOST_MARKERS):
        return "dnse"
    if any(marker in lowered for marker in YAHOO_HOST_MARKERS):
        return "yahoo"
    return "other"


def install_counters() -> None:
    """Count the requests each host receives, without changing their behaviour."""
    import urllib3

    original_pool_request = urllib3.PoolManager.request

    def pool_request(self, method, url, *args, **kwargs):
        _counts[_classify(url)] = _counts.get(_classify(url), 0) + 1
        return original_pool_request(self, method, url, *args, **kwargs)

    urllib3.PoolManager.request = pool_request

    try:
        from curl_cffi import requests as curl_requests
    except ImportError:  # pragma: no cover - yfinance is a dependency
        return

    original_session_request = curl_requests.Session.request

    def session_request(self, method, url, *args, **kwargs):
        _counts[_classify(url)] = _counts.get(_classify(url), 0) + 1
        return original_session_request(self, method, url, *args, **kwargs)

    curl_requests.Session.request = session_request


def reset_counts() -> None:
    for key in _counts:
        _counts[key] = 0


def counts() -> dict:
    return dict(_counts)


def clear_caches(symbol: str) -> None:
    """Remove the cached downloads of a symbol, so a stage really calls out."""
    from tradingagents.dataflows.config import get_config

    cache_dir = Path(get_config()["data_cache_dir"])
    bare = symbol.upper().removesuffix(".VN")
    for name in (
        f"{symbol.upper()}-DNSE-data.csv",
        f"{bare}-DNSE-data.csv",
        f"{symbol.upper()}-YFin-data.csv",
        f"{bare}-YFin-data.csv",
    ):
        path = cache_dir / name
        if path.exists():
            path.unlink()


def load_dotenv() -> None:
    env = REPO_ROOT / ".env"
    if env.exists():
        try:
            from dotenv import load_dotenv as _load
        except ImportError:  # pragma: no cover
            return
        _load(env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="VIC.VN", help="the symbol to check")
    parser.add_argument("--start", default=None, help="start date (yyyy-mm-dd)")
    parser.add_argument("--end", default=None, help="end date (yyyy-mm-dd)")
    parser.add_argument(
        "--expect",
        default="dnse",
        choices=("dnse", "yahoo"),
        help="the vendor that is expected to serve the data",
    )
    args = parser.parse_args(argv)

    load_dotenv()
    install_counters()

    import datetime as dt

    today = dt.date.today()
    end = dt.date.fromisoformat(args.end) if args.end else today - dt.timedelta(days=1)
    start = dt.date.fromisoformat(args.start) if args.start else end - dt.timedelta(days=20)
    symbol = args.symbol

    print(f"Verifying which vendor serves the daily market data for {symbol}")
    print(f"  window: {start.isoformat()} -> {end.isoformat()}")
    print(f"  expected: {args.expect}")
    if not os.environ.get("DNSE_API_KEY"):
        print("  note: DNSE_API_KEY is not set; the DNSE vendor cannot answer.")
    print()

    stages: list[tuple[str, callable, str]] = []

    def stage_get_stock_data() -> str:
        from tradingagents.dataflows.router import route_to_vendor

        out = route_to_vendor("get_stock_data", symbol, start.isoformat(), end.isoformat())
        for line in out.splitlines():
            if line.startswith("# Source:"):
                return line.split(":", 1)[1].strip().split(" ")[0].lower()
        return "unspecified"

    def stage_ohlcv() -> str:
        from tradingagents.dataflows.config import get_config
        from tradingagents.dataflows.ohlcv import load_ohlcv

        frame = load_ohlcv(symbol, end.isoformat(), fill_gaps=False)
        if frame.empty:
            return "empty"
        cache = Path(get_config()["data_cache_dir"])
        bare = symbol.upper().removesuffix(".VN")
        if (cache / f"{bare}-DNSE-data.csv").exists():
            return "dnse"
        if (cache / f"{symbol.upper()}-YFin-data.csv").exists():
            return "yahoo"
        return "unknown"

    def stage_snapshot() -> str:
        from tradingagents.dataflows.ohlcv import load_ohlcv
        from tradingagents.dataflows.vendors.yahoo.snapshot import build_verified_market_snapshot

        text = build_verified_market_snapshot(symbol, end.isoformat(), look_back_days=3)
        if "Latest verified OHLCV row" not in text:
            return "no snapshot"
        # The snapshot quotes the latest close; compare it with the frame the
        # dispatcher serves, so the snapshot's source is the dispatcher's.
        frame = load_ohlcv(symbol, end.isoformat(), fill_gaps=False)
        if frame.empty:
            return "empty"
        latest = float(frame.iloc[-1]["Close"])
        for line in text.splitlines():
            if line.startswith("| Close |"):
                quoted = float(line.split("|")[2].strip())
                return "consistent" if abs(quoted - latest) < 0.51 else "mismatch"
        return "unknown"

    def stage_closes() -> str:
        from tradingagents.dataflows.vendors.yahoo.market import get_closes

        closes = get_closes(symbol, start.isoformat(), end.isoformat())
        return "read" if not closes.empty else "empty"

    stages = [
        ("get_stock_data (routed tool)", stage_get_stock_data),
        ("OHLCV frame (indicators)", stage_ohlcv),
        ("verified market snapshot", stage_snapshot),
        ("closes (memory-log settlement)", stage_closes),
    ]

    rows = []
    for name, run in stages:
        clear_caches(symbol)
        reset_counts()
        try:
            hint = run()
        except Exception as exc:  # noqa: BLE001 - the report says what happened
            hint = f"failed: {type(exc).__name__}: {exc}"
        observed = counts()
        dnse, yahoo = observed.get("dnse", 0), observed.get("yahoo", 0)
        if dnse and not yahoo:
            source = "dnse"
        elif yahoo and not dnse:
            source = "yahoo"
        elif dnse and yahoo:
            source = "both"
        else:
            source = "neither"
        rows.append((name, source, hint, dnse, yahoo))

    print(f"{'stage':<34} {'served by':<10} {'hint':<16} {'dnse reqs':>10} {'yahoo reqs':>11}")
    print("-" * 86)
    for name, source, hint, dnse, yahoo in rows:
        print(f"{name:<34} {source:<10} {hint:<16} {dnse:>10} {yahoo:>11}")
    print()

    served_by_dnse = [r for r in rows if r[1] == "dnse"]
    if args.expect == "dnse":
        ok = len(served_by_dnse) == len(rows)
        if ok:
            print("VERDICT: the DNSE OpenAPI served every stage.")
        else:
            print(
                f"VERDICT: FAILED — {len(served_by_dnse)} of {len(rows)} stages were served by "
                "DNSE. The stages that were not are in the table above."
            )
        return 0 if ok else 1

    served_by_yahoo = [r for r in rows if r[1] == "yahoo"]
    if len(served_by_yahoo) == len(rows):
        print("VERDICT: Yahoo served every stage, as expected.")
        return 0
    if served_by_yahoo:
        print(
            f"VERDICT: FAILED — only {len(served_by_yahoo)} of {len(rows)} stages were served "
            "by Yahoo. The stages that were not are in the table above."
        )
        return 1
    print("VERDICT: no vendor served the data.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
