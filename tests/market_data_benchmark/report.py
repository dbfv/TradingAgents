"""Writing the artifacts.

Every conclusion in `summary.md` has to point at a number that is in one of the
CSVs, and every number has to come from a file that says where it came from.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

from .config import BenchmarkConfig
from .schema import RequestStatus


def write_csv(path: Path, records: list[dict], fieldnames: list[str] | None = None) -> Path:
    """Write records as a CSV, with a header even when there are none."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(records[0].keys()) if records else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)
    return path


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def error_records(results: dict[str, list]) -> list[dict]:
    """One row per failed request, with the cause kept apart."""
    records: list[dict] = []
    for provider, provider_results in results.items():
        for result in provider_results:
            if result.error is None:
                continue
            record = result.error.as_record()
            record["provider"] = provider
            record["symbol"] = result.symbol
            records.append(record)
    return records


def status_counts(results: dict[str, list]) -> dict[str, dict[str, int]]:
    """How each provider's requests ended, by status."""
    counts: dict[str, dict[str, int]] = {}
    for provider, provider_results in results.items():
        bucket = counts.setdefault(provider, {})
        for result in provider_results:
            bucket[result.request_status.value] = bucket.get(result.request_status.value, 0) + 1
    return counts


def success_rate(results: dict[str, list]) -> float | None:
    """The share of successful requests across both providers."""
    total = 0
    successful = 0
    for provider_results in results.values():
        for result in provider_results:
            total += 1
            if result.request_status.is_success:
                successful += 1
    return (successful / total) if total else None


def credential_failures(results: dict[str, list]) -> int:
    """Requests that failed on the credential rather than on the market."""
    count = 0
    for provider_results in results.values():
        for result in provider_results:
            if result.error is not None and result.error.status.is_credential_problem:
                count += 1
    return count


def build_summary(
    *,
    run_id: str,
    generated_at: dt.datetime,
    config: BenchmarkConfig,
    universe,
    results: dict[str, list],
    calendar,
    ticker_coverage: list[dict],
    field_metrics: list[dict],
    session_kinds: dict,
    per_symbol: list[dict],
    per_period: list[dict],
    ohlc_validation: list[dict],
    stability: list[dict],
    dnse_index_check: dict,
    trade_check_note: str,
    adjusted_price_note: str,
    calendar_note: str,
    verdict: str,
    verdict_reasons: list[str],
    recommendation: list[str],
    limitations: list[str],
    top_mismatches: list[dict],
) -> str:
    """The report. Every claim below is a number that is in another file."""
    symbols = universe.symbols
    lines: list[str] = []
    lines.append(f"# Yahoo Finance vs DNSE OpenAPI — VN30 benchmark ({run_id})")
    lines.append("")
    lines.append(f"Generated {generated_at.isoformat()}. Verdict: **{verdict}**.")
    lines.append("")

    lines.append("## What was compared")
    lines.append("")
    lines.append(
        f"- Universe: {len(symbols)} VN30 constituents, HOSE index review effective "
        f"{universe.effective_from.isoformat()} (current composition, not a "
        f"point-in-time history)."
    )
    lines.append(
        f"- Window: {config.window.start.isoformat()} → {config.window.end.isoformat()} "
        f"(inclusive session dates; the run day is excluded because its session is incomplete)."
    )
    lines.append(f"- Sessions in the window: {len(calendar.sessions)}.")
    lines.append("- Sources: `yfinance` (Yahoo Finance) and the official DNSE OpenAPI SDK.")
    for provider, provider_results in results.items():
        versions = {r.source_api_version for r in provider_results if r.source_api_version}
        if versions:
            lines.append(f"  - `{provider}` API version: {', '.join(sorted(versions))}.")
    lines.append("")

    lines.append("## 1. How many of the 30 tickers downloaded?")
    lines.append("")
    for provider, provider_results in results.items():
        successes = [r for r in provider_results if r.request_status.is_success]
        empty = [r for r in provider_results if r.request_status is RequestStatus.EMPTY_RESPONSE]
        missing = [
            r
            for r in provider_results
            if r.request_status in (RequestStatus.SYMBOL_NOT_FOUND, RequestStatus.UNSUPPORTED_OR_UNKNOWN)
        ]
        credential = [r for r in provider_results if r.request_status.is_credential_problem]
        lines.append(
            f"- **{provider}**: {len(successes)}/{len(provider_results)} successful, "
            f"{len(empty)} empty, {len(missing)} not found, {len(credential)} credential/permission problems."
        )
        for result in provider_results:
            if not result.request_status.is_success:
                lines.append(
                    f"  - `{result.symbol}` → `{result.request_status.value}`"
                    + (f" ({result.error.error_type}: {result.error.message})" if result.error else "")
                )
    lines.append("")

    lines.append("## 2. Session coverage")
    lines.append("")
    lines.append("| provider | symbols answered | mean session coverage | min | max |")
    lines.append("|---|---|---|---|---|")
    for provider in results:
        rows = [r for r in ticker_coverage if r["provider"] == provider]
        answered = [r for r in rows if r["request_status"] == "SUCCESS"]
        rates = [r["session_coverage"] for r in answered if r["session_coverage"] is not None]
        if rates:
            lines.append(
                f"| {provider} | {len(answered)}/{len(rows)} | "
                f"{sum(rates) / len(rates):.4f} | {min(rates):.4f} | {max(rates):.4f} |"
            )
        else:
            lines.append(f"| {provider} | {len(answered)}/{len(rows)} | n/a | n/a | n/a |")
    lines.append("")
    lines.append("Per-ticker numbers: `ticker_coverage.csv`, `daily_coverage.csv`.")
    lines.append("")

    lines.append("## 3. How large are the price differences?")
    lines.append("")
    lines.append("| field | comparable pairs | exact | MAE | median AE | max AE | MAPE |")
    lines.append("|---|---|---|---|---|---|---|")
    for record in field_metrics:
        lines.append(
            f"| {record['field']} | {record['comparable_pairs']} | "
            f"{record['exact_matches']} ({_pct(record['exact_match_rate'])}) | "
            f"{_num(record['mean_absolute_error'])} | {_num(record['median_absolute_error'])} | "
            f"{_num(record['max_absolute_error'])} | {_pct(record['mean_absolute_percentage_error'])} |"
        )
    lines.append("")
    lines.append("MAPE is `n/a` where the reference value is zero, so it is never computed on a zero denominator.")
    lines.append("")

    lines.append("## 4. What kind of difference is it?")
    lines.append("")
    lines.append("A session is classified by what the two sources' bars say about it.")
    lines.append("")
    lines.append("| kind | symbol-sessions |")
    lines.append("|---|---|")
    for kind, count in sorted(session_kinds.items(), key=lambda item: -item[1]):
        lines.append(f"| `{kind}` | {count} |")
    lines.append("")
    lines.append(
        "`exact` — the same bar in both. `scaled_price` — the same session, with the prices "
        "multiplied by a constant. `rescaled_session` — the same session, with the prices "
        "*and* the volume multiplied by constants. `shifted_session` — the volume identifies "
        "the bar as belonging to the neighbouring session. `shifted_and_scaled_session` — the "
        "same, with the prices also multiplied by a constant. "
        "`shifted_session_with_different_prices` — the volume identifies the neighbour, but "
        "the prices differ by more than a constant. `merged_sessions` — two sessions' volume "
        "in one bar. `price_difference` — the volume matches but the prices do not, with no "
        "constant factor between them. `volume_difference` — the prices match but the volume "
        "does not. `other_difference` — none of the above. `missing_in_yahoo` / "
        "`missing_in_dnse` / `missing_in_both` — one side has no row for that session."
    )
    lines.append("")
    lines.append("Per symbol: `symbol_metrics.csv`. Per month: `agreement_by_month.csv`.")
    lines.append("")
    lines.append(
        "| symbol | sessions | exact | rate | rescaled | shifted | scaled | unexplained | "
        "price ratio | volume ratio |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for record in per_symbol:
        unexplained = (record["price_difference_sessions"] or 0) + (
            record["other_difference_sessions"] or 0
        )
        lines.append(
            f"| {record['symbol']} | {record['sessions']} | {record['exact_sessions']} | "
            f"{_pct(record['exact_rate'])} | {record['rescaled_sessions']} | "
            f"{record['shifted_sessions']} | {record['scaled_sessions']} | {unexplained} | "
            f"{_num(record['median_price_ratio'])} | {_num(record['median_volume_ratio'])} |"
        )
    lines.append("")
    lines.append(
        "The two ratio columns are the medians of Yahoo's values divided by DNSE's, over the "
        "sessions both answered for. A ratio of 1 means the two agree; a constant ratio "
        "different from 1 means one of the two has rescaled that symbol's history."
    )
    lines.append("")
    if top_mismatches:
        lines.append("The ten largest differences by absolute size:")
        lines.append("")
        lines.append("| symbol | date | field | yahoo | dnse | abs diff | kind |")
        lines.append("|---|---|---|---|---|---|---|")
        for record in top_mismatches:
            lines.append(
                f"| {record['symbol']} | {record['date']} | {record['field']} | "
                f"{_num(record['yahoo_value'])} | {_num(record['dnse_value'])} | "
                f"{_num(record['abs_diff'])} | `{record['difference_kind']}` |"
            )
        lines.append("")
    lines.append("The full list: `mismatches.csv`.")
    lines.append("")

    lines.append("## 5. Internal consistency of each source")
    lines.append("")
    lines.append("| provider | series | rows checked | OHLC range violations | negative volume | duplicate dates |")
    lines.append("|---|---|---|---|---|---|")
    aggregated: dict[tuple[str, str], list[dict]] = {}
    for record in ohlc_validation:
        aggregated.setdefault((record["provider"], record["price_type"]), []).append(record)
    for (provider, price_type), rows in sorted(aggregated.items()):
        lines.append(
            f"| {provider} | {price_type} | {sum(r['rows'] for r in rows)} | "
            f"{sum(r['ohlc_range_violations'] for r in rows)} | "
            f"{sum(r['negative_volume_rows'] for r in rows)} | "
            f"{sum(r['duplicate_dates'] for r in rows)} |"
        )
    lines.append("")
    lines.append("Each source is checked on its own. Details: `ohlc_validation.csv`.")
    lines.append("")

    lines.append("## 6. Stability")
    lines.append("")
    lines.append("| provider | repetitions | success rate | mean latency | values changed |")
    lines.append("|---|---|---|---|---|")
    for record in stability:
        lines.append(
            f"| {record['provider']} | {record['repetitions']} | "
            f"{_pct(record['request_success_rate'])} | {_num(record['mean_latency_s'])} s | "
            f"{record['values_changed_between_repetitions']}/{record['values_compared']} |"
        )
    lines.append("")

    lines.append("## 7. Adjusted price")
    lines.append("")
    lines.append(adjusted_price_note)
    lines.append("")

    lines.append("## 8. Which series describes what actually traded?")
    lines.append("")
    lines.append(trade_check_note)
    lines.append("")

    lines.append("## 9. The universe, cross-checked against DNSE")
    lines.append("")
    lines.append(dnse_index_check)
    lines.append("")

    lines.append("## 10. The calendar")
    lines.append("")
    lines.append(calendar_note)
    lines.append("")

    lines.append("## Why this verdict")
    lines.append("")
    for reason in verdict_reasons:
        lines.append(f"- {reason}")
    lines.append("")

    lines.append("## What this means for TradingAgents")
    lines.append("")
    for line in recommendation:
        lines.append(f"- {line}")
    lines.append("")

    lines.append("## Limitations")
    lines.append("")
    for limitation in limitations:
        lines.append(f"- {limitation}")
    lines.append("")

    lines.append("## Files")
    lines.append("")
    for name in (
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
    ):
        lines.append(f"- `{name}`")
    lines.append("")
    return "\n".join(lines)


def _pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _num(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return f"{value:,.6g}"
    return str(value)
