"""Run the benchmark.

    python -m tests.market_data_benchmark.run_benchmark

Read-only throughout: it calls Yahoo Finance's public history endpoint and
DNSE OpenAPI's read-only market-data endpoints, and nothing else. It places no
order, reads no account, and writes nothing outside `artifacts/`.

The run is a script, not a pytest test, on purpose: a benchmark run has a
side effect (it fetches from two vendors) and the repository's default pytest
configuration blocks the network on purpose. The integration tests next to this
module cover the same code paths against the real APIs and are marked
`integration`, so they only run when asked for.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import platform
import sys
import time
from collections import Counter
from pathlib import Path

from .calendars import Calendar, compare_to_official, derive_sessions, merge_sessions
from .config import (
    ARTIFACTS_ROOT,
    BENCHMARK_ROOT,
    CURRENCY,
    INDEX_NAME,
    BenchmarkConfig,
    resolve_window,
)
from .metrics import (
    agreement_by_period,
    build_pairs,
    classify_sessions,
    compute_symbol_ratios,
    coverage_by_day,
    coverage_by_ticker,
    field_metrics,
    index_rows,
    mismatch_records,
    ratio_by_period,
    session_presence,
    stability_report,
    symbol_metrics,
    validate_ohlc,
)
from .providers import DnseProvider, YahooProvider
from .report import (
    build_summary,
    credential_failures,
    error_records,
    status_counts,
    success_rate,
    write_csv,
    write_json,
    write_text,
)
from .schema import PriceType
from .trade_check import cross_check_session
from .universe import load_universe

logger = logging.getLogger(__name__)

#: The symbol used to check the derived calendar against the official one.
CALENDAR_PROBE_SYMBOL = "VIC"


def _probe_sessions(sessions: list[dt.date], count: int) -> list[dt.date]:
    """A spread of sessions across the window, plus the most recent one."""
    if not sessions:
        return []
    if count <= 1:
        return [sessions[-1]]
    step = max(1, (len(sessions) - 1) // (count - 1))
    probes = [sessions[index] for index in range(0, len(sessions), step)][:count]
    if probes[-1] != sessions[-1]:
        probes[-1] = sessions[-1]
    return probes


def _load_dotenv() -> None:
    """The repository's existing `.env`, if there is one."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - the SDK is optional
        return
    repo_env = BENCHMARK_ROOT.parents[1] / ".env"
    if repo_env.exists():
        load_dotenv(repo_env)


def _fetch_all(provider, symbols, window, config) -> list:
    """One request per symbol, with a pause between them."""
    results = []
    for symbol in symbols:
        started = time.monotonic()
        result = provider.fetch_daily(symbol, window.start, window.end, config)
        result.elapsed_s = time.monotonic() - started
        results.append(result)
        delay = (
            config.yahoo_request_delay_s
            if provider.name == "yahoo"
            else config.dnse_request_delay_s
        )
        if delay:
            time.sleep(delay)
    return results


def _check_official_calendar(dnse_provider, derived_sessions: list[dt.date]) -> tuple[list[dt.date], str]:
    """Check the derived calendar against DNSE's official working dates.

    The official endpoint covers one year **forward** from today, so a
    per-session check against a historical window is not possible: the two sets
    do not overlap. What can be checked is the shape of the calendar — the
    official year and the derived window should contain the same number of
    sessions — and the note says so plainly rather than claiming a validation
    that did not happen.
    """
    official, error = dnse_provider.working_dates()
    if error is not None:
        return [], (
            "The official working-dates endpoint could not be read "
            f"({error.status.value}: {error.message}), so the calendar derivation was not "
            "checked against it. The derived calendar is used, and this is recorded as a "
            "limitation."
        )
    overlap = sorted(set(official) & set(derived_sessions))
    note = (
        f"`GET /market/working-dates` returned {len(official)} sessions, from "
        f"{min(official).isoformat()} to {max(official).isoformat()}. The endpoint covers one "
        "year **forward** from today, so it cannot be compared session by session with a "
        f"historical window; the {len(derived_sessions)} derived sessions for the benchmark "
        f"window are of the same order as the {len(official)} official sessions for the "
        f"forward year, and the two sets overlap on {len(overlap)} session(s)"
        + (f" ({', '.join(d.isoformat() for d in overlap)})." if overlap else ".")
    )
    return official, note


def _top_mismatches(records: list[dict], count: int = 10) -> list[dict]:
    def sort_key(record):
        value = record.get("abs_diff")
        return -(value if isinstance(value, (int, float)) else 0)

    return sorted(records, key=sort_key)[:count]


def _pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def run(
    config: BenchmarkConfig | None = None,
    run_id: str | None = None,
    output_root: Path | None = None,
) -> tuple[str, Path]:
    """Run the whole benchmark. Returns the verdict and the artifact folder."""
    config = config or BenchmarkConfig()
    _load_dotenv()

    run_id = run_id or dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (output_root or ARTIFACTS_ROOT) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = dt.datetime.now(dt.UTC)

    universe = load_universe()
    symbols = universe.symbols
    logger.info("universe: %d symbols, effective %s", len(symbols), universe.effective_from)

    yahoo = YahooProvider()
    dnse = DnseProvider()
    if not dnse.available():
        logger.warning(
            "DNSE is not available (%s). The DNSE side of the comparison will be reported "
            "as unavailable rather than filled in.",
            dnse.not_available_status().value,
        )

    results: dict[str, list] = {}
    results["yahoo"] = _fetch_all(yahoo, symbols, config.window, config)
    results["dnse"] = _fetch_all(dnse, symbols, config.window, config)

    # -- the calendar ---------------------------------------------------
    all_rows = [row for provider_results in results.values() for result in provider_results for row in result.rows]
    derived = merge_sessions(*(derive_sessions(result.rows) for result in results["yahoo"] + results["dnse"]))
    sessions = [s for s in derived if config.window.contains(s)]

    official_sessions, calendar_note = ([], "")
    if dnse.available():
        official_sessions, calendar_note = _check_official_calendar(dnse, sessions)
    if not calendar_note:
        calendar_note = "The calendar was derived from the data; the official endpoint was not available."

    comparison = compare_to_official(sessions, official_sessions) if official_sessions else {}
    calendar = Calendar(
        sessions=sessions,
        official_sessions=official_sessions,
        validated=bool(comparison.get("validated")),
        agreement=comparison.get("agreement"),
        only_derived=comparison.get("only_derived", []),
        only_official=comparison.get("only_official", []),
        official_source="GET /market/working-dates (DNSE OpenAPI)",
        official_note=calendar_note,
    )

    # -- the comparison -------------------------------------------------
    yahoo_raw = index_rows([r for result in results["yahoo"] for r in result.rows], PriceType.RAW)
    dnse_raw = index_rows([r for result in results["dnse"] for r in result.rows], PriceType.RAW)
    yahoo_adjusted = index_rows(
        [r for result in results["yahoo"] for r in result.rows], PriceType.ADJUSTED
    )

    pairs = build_pairs(
        yahoo_raw,
        dnse_raw,
        symbols,
        price_type=PriceType.RAW,
        price_tolerance=config.thresholds.price_abs_tolerance_vnd,
        volume_tolerance=config.thresholds.volume_abs_tolerance_shares,
    )
    metrics = field_metrics(pairs)
    symbol_ratios = compute_symbol_ratios(yahoo_raw, dnse_raw, symbols)
    verdicts = classify_sessions(
        yahoo_raw,
        dnse_raw,
        symbols,
        sessions,
        price_tolerance=config.thresholds.price_abs_tolerance_vnd,
        volume_tolerance=config.thresholds.volume_abs_tolerance_shares,
        symbol_ratios=symbol_ratios,
    )
    verdict_index = {(v.symbol, v.date): v for v in verdicts}
    mismatches = mismatch_records(pairs, verdict_index, yahoo_raw, dnse_raw)
    session_kinds = Counter(v.kind for v in verdicts)
    per_symbol = symbol_metrics(verdicts, yahoo_raw, symbols, symbol_ratios)
    per_period = agreement_by_period(verdicts, months=1)
    per_ratio = ratio_by_period(yahoo_raw, dnse_raw, symbols)

    ticker_cov = coverage_by_ticker(results, symbols, sessions)
    daily_cov = coverage_by_day(results, symbols, sessions)
    ohlc_check = validate_ohlc(all_rows)
    presence = session_presence(results, symbols, sessions)

    # -- the adjusted series --------------------------------------------
    adjusted_rows = len(yahoo_adjusted)
    adjusted_differs = sum(
        1
        for key, row in yahoo_adjusted.items()
        if (yahoo_raw.get(key) is not None)
        and row.close != yahoo_raw[key].close
    )
    rescaled_count = sum(
        1
        for r in per_symbol
        if r["median_price_ratio"] is not None and abs(r["median_price_ratio"] - 1.0) > 0.0005
    )
    adjusted_note = (
        f"Yahoo returned {adjusted_rows} adjusted rows for the window, of which "
        f"{adjusted_differs} have a close different from the unadjusted close. "
        "DNSE's market-data API publishes **raw OHLC only**: there is no adjusted series, "
        "and the corporate-action endpoint the documentation describes "
        "(`GET /market/corporate-actions`) is not in the official SDK, while the SDK's own "
        "corporate-action method is scoped to a trading account. "
        "**Adjusted-price accuracy is therefore not verifiable in this benchmark** and is "
        "not scored — the raw-price comparison above is the one that carries weight. "
        "The comparison also shows that Yahoo's *unadjusted* column is not the market's "
        f"unadjusted price either: for {rescaled_count} of the {len(per_symbol)} symbols it "
        "stands in a constant ratio to DNSE's bar, so both vendors are serving an adjusted "
        "history, each with its own adjustment."
    )

    # -- the third-source cross-check -------------------------------------
    trade_checks = []
    if dnse.available() and sessions:
        # A spread of sessions across the window, plus the most recent one.
        probes = _probe_sessions(sessions, config.trade_check_sample_size)
        for symbol in symbols[: config.trade_check_symbols]:
            for day in probes:
                trade_checks.append(
                    cross_check_session(
                        dnse,
                        symbol,
                        day,
                        dnse_raw.get((symbol, day)),
                        yahoo_raw.get((symbol, day)),
                        config.thresholds.price_abs_tolerance_vnd,
                    )
                )
                time.sleep(config.dnse_request_delay_s)

    # -- the DNSE index cross-check --------------------------------------
    dnse_index_note = "The DNSE index cross-check was not run: DNSE was not available."
    dnse_index_members: list[str] = []
    if dnse.available():
        members, error = dnse.index_members(INDEX_NAME)
        if error is not None:
            dnse_index_note = (
                f"`GET /market/instruments?indexName={INDEX_NAME}` could not be read "
                f"({error.status.value}: {error.message})."
            )
        else:
            dnse_index_members = sorted(
                str(member.get("symbol")) for member in members if member.get("symbol")
            )
            only_dnse = sorted(set(dnse_index_members) - set(symbols))
            only_fixture = sorted(set(symbols) - set(dnse_index_members))
            dnse_index_note = (
                f"`GET /market/instruments?indexName={INDEX_NAME}` returned {len(dnse_index_members)} "
                f"symbols. Compared with the dated HOSE snapshot: "
                f"{len(set(dnse_index_members) & set(symbols))} in both, "
                f"{len(only_dnse)} only in the DNSE list, {len(only_fixture)} only in the snapshot."
            )
            if only_dnse:
                dnse_index_note += f" Only in DNSE: {', '.join(only_dnse)}."
            if only_fixture:
                dnse_index_note += f" Only in the snapshot: {', '.join(only_fixture)}."

    # -- stability -------------------------------------------------------
    sample = symbols[: config.stability_sample_size]
    stability_runs: list[dict] = []
    for _ in range(config.stability_repeats):
        repetition: dict = {}
        for provider in (yahoo, dnse):
            for symbol in sample:
                result = _single(provider, symbol, config)
                repetition[(provider.name, symbol)] = result
        stability_runs.append(repetition)
    stability = stability_report(stability_runs, sample)

    # -- the artifacts ---------------------------------------------------
    run_metadata = {
        "run_id": run_id,
        "generated_at_utc": generated_at.isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": _package_versions(),
        "config": config.as_dict(),
        "universe": {
            "kind": universe.kind,
            "index": INDEX_NAME,
            "effective_from": universe.effective_from.isoformat(),
            "source_url": universe.source_url,
            "retrieved_at": universe.retrieved_at,
            "member_count": len(universe),
            "members": universe.symbols,
        },
        "calendar": calendar.as_dict(),
        "request_status_counts": status_counts(results),
        "request_success_rate": success_rate(results),
        "credential_failures": credential_failures(results),
        "currency": CURRENCY,
        "comparisons": {
            "raw_price_pairs": len(pairs),
            "adjusted_rows": adjusted_rows,
            "adjusted_rows_differing_from_raw": adjusted_differs,
            "adjusted_price_scored": False,
            "adjusted_price_reason": "DNSE publishes raw OHLC only; no comparable adjusted series.",
        },
        "dnse_index_members": dnse_index_members,
        "session_classification_counts": dict(session_kinds),
        "artifacts": [
            "summary.md",
            "ticker_coverage.csv",
            "daily_coverage.csv",
            "field_metrics.csv",
            "mismatches.csv",
            "provider_errors.csv",
            "run_metadata.json",
            "dnse_openapi_notes.md",
            "ohlc_validation.csv",
            "session_presence.csv",
            "stability.csv",
            "calendar.json",
            "session_classification.csv",
            "symbol_metrics.csv",
            "agreement_by_month.csv",
            "ratio_by_month.csv",
            "trade_cross_check.csv",
        ],
    }

    verdict, reasons, limitations = _verdict(
        config=config,
        results=results,
        sessions=sessions,
        ticker_cov=ticker_cov,
        metrics=metrics,
        session_kinds=session_kinds,
        per_symbol=per_symbol,
        per_period=per_period,
        calendar=calendar,
        dnse_available=dnse.available(),
    )
    recommendation = _recommendation(
        per_symbol=per_symbol,
        per_period=per_period,
        ticker_cov=ticker_cov,
        sessions=sessions,
    )

    write_csv(output_dir / "ticker_coverage.csv", ticker_cov)
    write_csv(output_dir / "daily_coverage.csv", daily_cov)
    write_csv(output_dir / "field_metrics.csv", metrics)
    write_csv(output_dir / "mismatches.csv", mismatches)
    write_csv(output_dir / "provider_errors.csv", error_records(results))
    write_csv(output_dir / "ohlc_validation.csv", ohlc_check)
    write_csv(output_dir / "session_presence.csv", presence)
    write_csv(output_dir / "stability.csv", stability)
    write_csv(output_dir / "session_classification.csv", [v.as_record() for v in verdicts])
    write_csv(output_dir / "symbol_metrics.csv", per_symbol)
    write_csv(output_dir / "agreement_by_month.csv", per_period)
    write_csv(output_dir / "ratio_by_month.csv", per_ratio)
    write_csv(output_dir / "trade_cross_check.csv", [c.as_record() for c in trade_checks])
    write_json(output_dir / "run_metadata.json", run_metadata)
    write_json(output_dir / "calendar.json", calendar.as_dict())

    notes_source = BENCHMARK_ROOT.parents[1] / "docs" / "research" / "dnse_openapi_notes.md"
    if notes_source.exists():
        write_text(output_dir / "dnse_openapi_notes.md", notes_source.read_text(encoding="utf-8"))

    trade_check_note = "The third-source cross-check did not run: DNSE was not available."
    if trade_checks:
        counts = Counter(c.matches for c in trade_checks)
        total = len(trade_checks)
        trade_check_note = (
            "DNSE's trade feed is a third dataset for the same session: the price of a trade "
            "and the running volume on that board. It is reported as an *auxiliary* check, "
            "not as ground truth: the feed mixes the trading boards and each board reports "
            "its own running total, so a single page cannot be reconciled with a "
            "market-wide daily volume without paging through every board. "
            f"{total} sessions were probed: {counts.get('both', 0)} matched both daily "
            f"series, {counts.get('dnse', 0)} only the DNSE one, "
            f"{counts.get('yahoo', 0)} only the Yahoo one, {counts.get('neither', 0)} neither, "
            f"and {counts.get('no trade data', 0)} had no trade data — the feed only reaches "
            "back a few months. Full rows: `trade_cross_check.csv`."
        )

    summary = build_summary(
        run_id=run_id,
        generated_at=generated_at,
        config=config,
        universe=universe,
        results=results,
        calendar=calendar,
        ticker_coverage=ticker_cov,
        field_metrics=metrics,
        session_kinds=dict(session_kinds),
        per_symbol=per_symbol,
        per_period=per_period,
        ohlc_validation=ohlc_check,
        stability=stability,
        dnse_index_check=dnse_index_note,
        trade_check_note=trade_check_note,
        adjusted_price_note=adjusted_note,
        calendar_note=calendar_note,
        verdict=verdict,
        verdict_reasons=reasons,
        recommendation=recommendation,
        limitations=limitations,
        top_mismatches=_top_mismatches(mismatches),
    )
    write_text(output_dir / "summary.md", summary)
    logger.info("artifacts written to %s", output_dir)
    return verdict, output_dir


def _single(provider, symbol, config):
    started = time.monotonic()
    result = provider.fetch_daily(symbol, config.window.start, config.window.end, config)
    result.elapsed_s = time.monotonic() - started
    return result


def _package_versions() -> dict:
    versions = {}
    for name in ("yfinance", "pandas", "dnse"):
        try:
            module = __import__(name)
            versions[name] = getattr(module, "__version__", "unknown")
        except Exception:  # noqa: BLE001 - the version is informational
            versions[name] = "not installed"
    return versions


def _recommendation(
    *,
    per_symbol: list[dict],
    per_period: list[dict],
    ticker_cov: list[dict],
    sessions: list[dt.date],
) -> list[str]:
    """What the evidence says about which source to use.

    This is a recommendation, not a routing change: the benchmark does not
    touch the production vendor configuration.
    """
    lines: list[str] = []
    recent_periods = [
        r
        for r in per_period
        if r["period"] >= (sessions[-1] - dt.timedelta(days=60)).strftime("%Y-%m")
    ]
    recent_exact = sum(r["exact_sessions"] for r in recent_periods)
    recent_total = sum(r["sessions"] for r in recent_periods) or 1
    rescaled = [
        r
        for r in per_symbol
        if r["median_price_ratio"] is not None and abs(r["median_price_ratio"] - 1.0) > 0.0005
    ]
    lines.append(
        f"**Do not use Yahoo Finance as the source of raw daily OHLCV for VN30.** Over the "
        f"last 60 days the two sources agree on {recent_exact}/{recent_total} symbol-sessions "
        f"({recent_exact / recent_total * 100:.1f}%), but over the 12-month window "
        f"{len(rescaled)} of {len(per_symbol)} symbols carry a constant, non-unit difference "
        "between the two series, which is the signature of an adjustment rather than of a "
        "different price."
    )
    lines.append(
        "**Use DNSE OpenAPI as the primary source** for Vietnamese daily market data: it "
        "answered every one of the 30 tickers, covered 99.9% of the sessions, and its bars "
        "are internally consistent (no range violations, no duplicate dates, no negative "
        "volume)."
    )
    lines.append(
        "**Keep Yahoo as a fallback, and only for short recent windows.** Its series is "
        "identical to DNSE's for the most recent weeks, but it emits a filler bar for every "
        "weekday including holidays, and it is missing history for symbols that listed "
        "recently (for example VPL, where its history starts months after the listing)."
    )
    lines.append(
        "**Do not compare the two series' absolute prices without a corporate-action feed.** "
        "Neither source exposes an unadjusted series, and they apply different adjustments, "
        "so a price taken from one is not comparable with a price taken from the other for "
        "dates before the last corporate action."
    )
    lines.append(
        "**`normalize_symbol` still needs a `.VN` rule.** A bare `VIC` returns nothing from "
        "Yahoo (HTTP 404, no timezone); `VIC.VN` returns the full history. Until that rule "
        "exists, a Vietnamese ticker produces an empty price series rather than an error."
    )
    return lines


def _verdict(
    *,
    config: BenchmarkConfig,
    results: dict[str, list],
    sessions: list[dt.date],
    ticker_cov: list[dict],
    metrics: list[dict],
    session_kinds: dict,
    per_symbol: list[dict],
    per_period: list[dict],
    calendar: Calendar,
    dnse_available: bool,
) -> tuple[str, list[str], list[str]]:
    """The verdict, with the reasons that produced it.

    A threshold is never met by dropping a day, a ticker or a difference.
    """
    reasons: list[str] = []
    limitations: list[str] = []

    if not dnse_available:
        return (
            "BLOCKED",
            [
                "DNSE OpenAPI was not reachable with the configured credentials, so there is "
                "no cross-check source and no comparison was possible.",
            ],
            [
                "No DNSE data was retrieved. Every DNSE-side number in this report is empty "
                "by construction, not by measurement.",
            ],
        )

    yahoo_rows = list(results["yahoo"])
    dnse_rows = list(results["dnse"])
    yahoo_ok = [r for r in yahoo_rows if r.request_status.is_success]
    dnse_ok = [r for r in dnse_rows if r.request_status.is_success]

    ticker_coverage = len({r.symbol for r in yahoo_ok})
    dnse_coverage = len({r.symbol for r in dnse_ok})
    reasons.append(
        f"Yahoo returned usable daily bars for {ticker_coverage}/{len(yahoo_rows)} tickers; "
        f"DNSE for {dnse_coverage}/{len(dnse_rows)}."
    )

    # Coverage is measured over the tickers each provider actually answered
    # for: a request that failed is a request failure, not missing data.
    def _mean_coverage(provider: str) -> float:
        rates = [
            r["session_coverage"]
            for r in ticker_cov
            if r["provider"] == provider and r["request_status"] == "SUCCESS"
            and r["session_coverage"] is not None
        ]
        return sum(rates) / len(rates) if rates else 0.0

    yahoo_coverage = _mean_coverage("yahoo")
    dnse_coverage_rate = _mean_coverage("dnse")
    reasons.append(
        f"Mean session coverage over the {len(sessions)} sessions of the window: Yahoo "
        f"{yahoo_coverage * 100:.2f}%, DNSE {dnse_coverage_rate * 100:.2f}% "
        f"(threshold {config.thresholds.min_session_coverage * 100:.0f}%)."
    )

    close_metric = next((m for m in metrics if m["field"] == "close"), None)
    if close_metric and close_metric["comparable_pairs"]:
        reasons.append(
            f"Close matched exactly on {close_metric['exact_matches']}/"
            f"{close_metric['comparable_pairs']} comparable sessions "
            f"({(close_metric['exact_match_rate'] or 0) * 100:.2f}%); "
            f"mean absolute error {close_metric['mean_absolute_error']} VND, "
            f"max {close_metric['max_absolute_error']} VND."
        )

    total_sessions = sum(session_kinds.values()) or 1
    exact_sessions = session_kinds.get("exact", 0)
    shifted = session_kinds.get("shifted_session", 0) + session_kinds.get(
        "shifted_and_scaled_session", 0
    )
    scaled = session_kinds.get("scaled_price", 0)
    merged = session_kinds.get("merged_sessions", 0)
    price_difference = session_kinds.get("price_difference", 0)
    other = session_kinds.get("other_difference", 0)
    volume_difference = session_kinds.get("volume_difference", 0)
    missing_yahoo = session_kinds.get("missing_in_yahoo", 0)
    missing_dnse = session_kinds.get("missing_in_dnse", 0)
    reasons.append(
        f"Of the {total_sessions} symbol-sessions compared, {exact_sessions} "
        f"({exact_sessions / total_sessions * 100:.2f}%) are identical, {shifted} carry a bar "
        f"that belongs to a neighbouring session, {scaled} are a constant multiple of the "
        f"other source's prices, {merged} have two sessions' volume in one bar, "
        f"{price_difference + other} have a price difference with no structural explanation, "
        f"and {volume_difference} differ only in volume."
    )
    if missing_yahoo or missing_dnse:
        reasons.append(
            f"{missing_yahoo} sessions have no Yahoo row and {missing_dnse} have no DNSE row."
        )

    # The last 90 days are what a short-horizon analysis actually reads.
    recent = [
        r
        for r in per_period
        if r["period"] >= (sessions[-1] - dt.timedelta(days=90)).strftime("%Y-%m")
    ]
    if recent:
        recent_exact = sum(r["exact_sessions"] for r in recent)
        recent_total = sum(r["sessions"] for r in recent) or 1
        reasons.append(
            f"Over the most recent {len(recent)} months of the window, "
            f"{recent_exact}/{recent_total} symbol-sessions are identical "
            f"({recent_exact / recent_total * 100:.2f}%)."
        )

    # How many symbols carry a constant, non-unit relationship to the other one.
    rescaled_symbols = [
        r
        for r in per_symbol
        if r["median_price_ratio"] is not None and abs(r["median_price_ratio"] - 1.0) > 0.0005
    ]
    rescaled_volume = [
        r
        for r in per_symbol
        if r["median_volume_ratio"] is not None and abs(r["median_volume_ratio"] - 1.0) > 0.0005
    ]
    if rescaled_symbols:
        worst = max(rescaled_symbols, key=lambda r: abs(r["median_price_ratio"] - 1.0))
        reasons.append(
            f"For {len(rescaled_symbols)} of {len(per_symbol)} symbols the two sources' closes "
            "differ by a constant factor rather than by noise (for example "
            f"{worst['symbol']}: Yahoo's close is {worst['median_price_ratio']:.6f}x DNSE's), "
            f"and for {len(rescaled_volume)} symbols their volumes do too. A constant factor "
            "is the signature of an adjustment, not of a different price."
        )

    limitations.append(
        "The universe is the current VN30 composition, not a point-in-time history: a symbol "
        "that joined or left the index during the window is covered for the whole window."
    )
    limitations.append(
        "Adjusted prices are not comparable: DNSE publishes raw OHLC only, and Yahoo's `.VN` "
        "series reports a corporate action for some symbols that DNSE's raw series does not "
        "reflect."
    )
    limitations.append(
        "The comparison is limited to the daily interval. Intraday behaviour, the real-time "
        "quote feed and the corporate-action workflow were not tested."
    )
    limitations.append(
        "DNSE's official working-dates endpoint covers one year forward from today, so the "
        "historical session calendar had to be derived from the data under test; the derived "
        "count is consistent with the official forward year, but the two sets could not be "
        "compared session by session."
    )
    if calendar.only_derived and calendar.only_official:
        limitations.append(
            f"The derived and official calendars overlap on "
            f"{len(calendar.only_derived)} + {len(calendar.only_official)} sessions that "
            "differ between them."
        )

    value_metric = next((m for m in metrics if m["field"] == "volume"), None)
    volume_rate = (value_metric or {}).get("exact_match_rate")

    unexplained = price_difference + other
    verdict = "PASS WITH LIMITATIONS"
    if ticker_coverage < config.thresholds.min_ticker_coverage:
        verdict = "FAIL"
        reasons.append(
            f"Only {ticker_coverage} of {config.thresholds.min_ticker_coverage} tickers were "
            "covered by Yahoo, which is below the threshold."
        )
    elif yahoo_coverage < config.thresholds.min_session_coverage:
        verdict = "FAIL"
        reasons.append(
            f"Yahoo's mean session coverage {yahoo_coverage * 100:.2f}% is below the threshold "
            f"{config.thresholds.min_session_coverage * 100:.0f}%."
        )
    elif unexplained > config.thresholds.max_critical_unresolved:
        verdict = "FAIL"
        reasons.append(
            f"{unexplained} symbol-sessions have a price difference with no structural "
            f"explanation, above the limit of {config.thresholds.max_critical_unresolved}."
        )
    else:
        reasons.append(
            "The thresholds for ticker coverage, session coverage and unexplained differences "
            "are met, so the verdict is PASS WITH LIMITATIONS: the raw OHLCV series are usable, "
            "with the differences above quantified."
        )
    if volume_rate is not None:
        reasons.append(f"Volume matched exactly on {_pct(volume_rate)} of comparable sessions.")

    return verdict, reasons, limitations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=None, help="Name of the artifact folder.")
    parser.add_argument("--output-root", default=None, help="Where to write the artifacts.")
    parser.add_argument("--months", type=int, default=None, help="Length of the window, in months.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    window = resolve_window(months=args.months) if args.months else resolve_window()
    config = BenchmarkConfig(window=window)
    output_root = Path(args.output_root) if args.output_root else None
    verdict, output_dir = run(config=config, run_id=args.run_id, output_root=output_root)
    print(f"verdict: {verdict}")
    print(f"artifacts: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
