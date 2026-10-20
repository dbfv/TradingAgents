"""The measurements: coverage, internal validity, field-by-field accuracy.

Nothing here decides a verdict. It measures, and the report decides.

A difference is never attributed to a vendor automatically. The session-level
classifier works out *what kind* of difference it is before anyone says which
source is at fault. It uses the **volume** as the fingerprint, because volume
is the one field a corporate-action adjustment does not touch: a price may be
scaled, a volume may not.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

from .schema import CanonicalRow, PriceType, ProviderResult

PRICE_FIELDS = ("open", "high", "low", "close")
ALL_FIELDS = ("open", "high", "low", "close", "volume")

#: How a session differs between the two sources.
KIND_EXACT = "exact"
KIND_PRICE_DIFFERENCE = "price_difference"
KIND_SCALED_PRICE = "scaled_price"
KIND_SHIFTED = "shifted_session"
KIND_SHIFTED_SCALED = "shifted_and_scaled_session"
KIND_SHIFTED_DIFFERENT = "shifted_session_with_different_prices"
KIND_RESCALED = "rescaled_session"
KIND_MERGED = "merged_sessions"
KIND_VOLUME_DIFFERENCE = "volume_difference"
KIND_MISSING_YAHOO = "missing_in_yahoo"
KIND_MISSING_DNSE = "missing_in_dnse"
KIND_MISSING_BOTH = "missing_in_both"
KIND_OTHER = "other_difference"

#: A scale factor counts as a constant when the four prices agree on it to
#: this relative tolerance.
SCALE_FIT_TOLERANCE = 0.002
#: ... and it is only called a scale when it is far enough from 1 to matter.
SCALE_MIN_DEVIATION = 0.0005
#: Prices are compared in dong; one dong of slack absorbs the float residue of
#: multiplying a thousand-dong float by 1000.
DEFAULT_PRICE_TOLERANCE = 1.0
DEFAULT_VOLUME_TOLERANCE = 0


@dataclass
class FieldPair:
    """One field, one session, both sources' values."""

    symbol: str
    date: dt.date
    field: str
    yahoo: float | None
    dnse: float | None
    tolerance: float

    @property
    def abs_diff(self) -> float | None:
        if self.yahoo is None or self.dnse is None:
            return None
        return abs(self.yahoo - self.dnse)

    @property
    def matches(self) -> bool:
        if self.yahoo is None or self.dnse is None:
            return False
        return self.abs_diff is not None and self.abs_diff <= self.tolerance

    @property
    def rel_diff(self) -> float | None:
        if self.yahoo is None or self.dnse is None or self.dnse == 0:
            return None
        return abs(self.yahoo - self.dnse) / abs(self.dnse)


@dataclass
class SessionVerdict:
    """What the two sources say about one session of one symbol."""

    symbol: str
    date: dt.date
    kind: str
    #: The scale factor between the two price sets, when one explains them.
    scale: float | None = None
    #: The session the Yahoo bar appears to describe, when it is not this one.
    matched_session: dt.date | None = None
    detail: str = ""

    def as_record(self) -> dict:
        return {
            "symbol": self.symbol,
            "date": self.date.isoformat(),
            "kind": self.kind,
            "scale": round(self.scale, 8) if self.scale is not None else None,
            "matched_session": self.matched_session.isoformat() if self.matched_session else None,
            "detail": self.detail,
        }


# -- indexing --------------------------------------------------------------


def index_rows(rows: list[CanonicalRow], price_type: PriceType | None = None) -> dict:
    """`{(symbol, date): row}`."""
    index: dict = {}
    for row in rows:
        if price_type is not None and row.price_type is not price_type:
            continue
        index[(row.symbol, row.date)] = row
    return index


def _value(row: CanonicalRow | None, field: str) -> float | None:
    if row is None:
        return None
    value = getattr(row, field, None)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _prices(row: CanonicalRow | None) -> list[float] | None:
    if row is None:
        return None
    values = [_value(row, field) for field in PRICE_FIELDS]
    if any(v is None for v in values):
        return None
    return values  # type: ignore[return-value]


def _scale_between(yahoo: list[float], dnse: list[float]) -> tuple[float, float] | None:
    """The factor that turns one price set into the other, and how well it fits.

    Returns `(scale, max_residual)`, or None when no single factor explains
    the two sets.
    """
    ratios = [y / d for y, d in zip(yahoo, dnse, strict=True) if d not in (0,)]
    if len(ratios) != len(yahoo):
        return None
    scale = statistics.median(ratios)
    if scale <= 0:
        return None
    residuals = [abs(y - d * scale) / max(abs(y), 1e-9) for y, d in zip(yahoo, dnse, strict=True)]
    return scale, max(residuals)


# -- the session classifier ------------------------------------------------


@dataclass
class SymbolRatios:
    """How one source's numbers relate to the other's, for one symbol.

    Measured, not assumed: the median ratio of the two price sets and of the
    two volume sets over the sessions both sources answered for. A ratio of
    1 means the two agree; a constant ratio of, say, 1.027 means one of them
    has rescaled that symbol's history.
    """

    price_ratio: float | None = None
    volume_ratio: float | None = None
    price_ratio_spread: float | None = None
    volume_ratio_spread: float | None = None
    sessions: int = 0

    def matches(self, price_ratio: float | None, volume_ratio: float | None) -> bool:
        """Whether two observed ratios are the symbol's usual ones."""
        if self.price_ratio is None or price_ratio is None:
            return False
        if abs(price_ratio - self.price_ratio) > max(
            0.005 * abs(self.price_ratio), 0.0005
        ):
            return False
        if self.volume_ratio is None or volume_ratio is None:
            return True
        return abs(volume_ratio - self.volume_ratio) <= max(
            0.005 * abs(self.volume_ratio), 0.0005
        )


def compute_symbol_ratios(
    yahoo_rows: dict[tuple[str, dt.date], CanonicalRow],
    dnse_rows: dict[tuple[str, dt.date], CanonicalRow],
    symbols: list[str],
) -> dict[str, SymbolRatios]:
    """The per-symbol ratio between the two sources."""
    ratios: dict[str, SymbolRatios] = {}
    for symbol in symbols:
        price_ratios: list[float] = []
        volume_ratios: list[float] = []
        for (row_symbol, session), yahoo in yahoo_rows.items():
            if row_symbol != symbol:
                continue
            dnse = dnse_rows.get((row_symbol, session))
            if dnse is None:
                continue
            yahoo_close = _value(yahoo, "close")
            dnse_close = _value(dnse, "close")
            yahoo_volume = _value(yahoo, "volume")
            dnse_volume = _value(dnse, "volume")
            if yahoo_close and dnse_close:
                price_ratios.append(yahoo_close / dnse_close)
            if yahoo_volume and dnse_volume:
                volume_ratios.append(yahoo_volume / dnse_volume)
        ratios[symbol] = SymbolRatios(
            price_ratio=_median_or_none(price_ratios),
            volume_ratio=_median_or_none(volume_ratios),
            price_ratio_spread=_spread(price_ratios),
            volume_ratio_spread=_spread(volume_ratios),
            sessions=len(price_ratios),
        )
    return ratios


def _median_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.median(values)


def _spread(values: list[float]) -> float | None:
    """The interquartile range: how steady the ratio is."""
    if len(values) < 4:
        return None
    ordered = sorted(values)
    lower = statistics.median(ordered[: len(ordered) // 2])
    upper = statistics.median(ordered[(len(ordered) + 1) // 2 :])
    return upper - lower


def classify_session(
    symbol: str,
    session: dt.date,
    yahoo_row: CanonicalRow | None,
    dnse_rows: dict[tuple[str, dt.date], CanonicalRow],
    sessions: list[dt.date],
    price_tolerance: float = DEFAULT_PRICE_TOLERANCE,
    volume_tolerance: int = DEFAULT_VOLUME_TOLERANCE,
    symbol_ratios: SymbolRatios | None = None,
) -> SessionVerdict:
    """What kind of session this is, for the two sources.

    The volume is the fingerprint: a price may be scaled by a corporate action,
    a volume may not. So a Yahoo bar whose volume matches a *different*
    session's volume is that session's bar, whatever its prices say.
    """
    dnse_row = dnse_rows.get((symbol, session))
    if yahoo_row is None and dnse_row is None:
        return SessionVerdict(symbol, session, KIND_MISSING_BOTH)
    if yahoo_row is None:
        return SessionVerdict(symbol, session, KIND_MISSING_YAHOO)
    if dnse_row is None:
        return SessionVerdict(symbol, session, KIND_MISSING_DNSE)

    yahoo_prices = _prices(yahoo_row)
    dnse_prices = _prices(dnse_row)
    yahoo_volume = _value(yahoo_row, "volume")
    dnse_volume = _value(dnse_row, "volume")
    if yahoo_prices is None or dnse_prices is None or yahoo_volume is None or dnse_volume is None:
        return SessionVerdict(symbol, session, KIND_OTHER, detail="a value was missing")

    prices_equal = all(
        abs(y - d) <= price_tolerance for y, d in zip(yahoo_prices, dnse_prices, strict=True)
    )
    volume_equal = abs(yahoo_volume - dnse_volume) <= volume_tolerance

    if volume_equal:
        # The volume says the two bars are the same session.
        if prices_equal:
            return SessionVerdict(symbol, session, KIND_EXACT)
        # The same session, with the prices scaled by a constant factor.
        fitted = _scale_between(yahoo_prices, dnse_prices)
        if fitted is not None:
            scale, residual = fitted
            if residual <= SCALE_FIT_TOLERANCE and abs(scale - 1.0) > SCALE_MIN_DEVIATION:
                return SessionVerdict(
                    symbol,
                    session,
                    KIND_SCALED_PRICE,
                    scale=scale,
                    detail=f"the prices are {scale:.6f}x the other source's",
                )
        return SessionVerdict(symbol, session, KIND_PRICE_DIFFERENCE)

    # The volume does not match this session. It may still be this session,
    # with both series rescaled by a constant — which is what a vendor's
    # adjustment to a symbol's history looks like.
    if symbol_ratios is not None and symbol_ratios.price_ratio is not None:
        price_ratio = None
        if all(y and d for y, d in zip(yahoo_prices, dnse_prices, strict=True)):
            price_ratio = statistics.median(
                [y / d for y, d in zip(yahoo_prices, dnse_prices, strict=True) if d]
            )
        volume_ratio = None
        if yahoo_volume and dnse_volume:
            volume_ratio = yahoo_volume / dnse_volume
        if symbol_ratios.matches(price_ratio, volume_ratio):
            volume_text = (
                f"the volume {volume_ratio:.6f}x"
                if volume_ratio is not None
                else "the volume unchanged"
            )
            return SessionVerdict(
                symbol,
                session,
                KIND_RESCALED,
                scale=price_ratio,
                detail=(
                    f"the same session with the prices {price_ratio:.6f}x and {volume_text} "
                    "the other source's"
                ),
            )

    # The volume says the bar is a *different* session. Which one, the volume
    # decides: a corporate action may scale a price, but never a volume.
    position = _session_index(sessions, session)
    candidates = []
    if position is not None:
        if position > 0:
            candidates.append(("previous", sessions[position - 1]))
        if position < len(sessions) - 1:
            candidates.append(("next", sessions[position + 1]))

    for direction, candidate in candidates:
        other = dnse_rows.get((symbol, candidate))
        other_prices = _prices(other)
        other_volume = _value(other, "volume")
        if other_prices is None or other_volume is None:
            continue
        if abs(yahoo_volume - other_volume) > volume_tolerance:
            continue
        if all(abs(y - d) <= price_tolerance for y, d in zip(yahoo_prices, other_prices, strict=True)):
            return SessionVerdict(
                symbol,
                session,
                KIND_SHIFTED,
                matched_session=candidate,
                detail=f"the bar matches the {direction} session",
            )
        fitted = _scale_between(yahoo_prices, other_prices)
        if fitted is not None:
            scale, residual = fitted
            if residual <= SCALE_FIT_TOLERANCE and abs(scale - 1.0) > SCALE_MIN_DEVIATION:
                return SessionVerdict(
                    symbol,
                    session,
                    KIND_SHIFTED_SCALED,
                    scale=scale,
                    matched_session=candidate,
                    detail=(
                        f"the bar matches the {direction} session, "
                        f"with prices {scale:.6f}x the other source's"
                    ),
                )
        return SessionVerdict(
            symbol,
            session,
            KIND_SHIFTED_DIFFERENT,
            matched_session=candidate,
            detail=(
                f"the volume matches the {direction} session, but the prices differ "
                "by more than a constant factor"
            ),
        )

    # Two sessions merged into one bar: the volume is their sum.
    if position is not None:
        neighbours = []
        if position > 0:
            neighbours.append(dnse_rows.get((symbol, sessions[position - 1])))
        neighbours.append(dnse_row)
        if position < len(sessions) - 1:
            neighbours.append(dnse_rows.get((symbol, sessions[position + 1])))
        volumes = [_value(row, "volume") for row in neighbours if row is not None]
        for first in range(len(volumes)):
            for second in range(first + 1, len(volumes)):
                total = volumes[first] + volumes[second]
                if abs(yahoo_volume - total) <= volume_tolerance:
                    return SessionVerdict(
                        symbol,
                        session,
                        KIND_MERGED,
                        detail="the volume is the sum of two sessions' volumes",
                    )

    if prices_equal:
        return SessionVerdict(symbol, session, KIND_VOLUME_DIFFERENCE)
    return SessionVerdict(symbol, session, KIND_OTHER)


def _session_index(sessions: list[dt.date], session: dt.date) -> int | None:
    try:
        return sessions.index(session)
    except ValueError:
        return None


def classify_sessions(
    yahoo_rows: dict[tuple[str, dt.date], CanonicalRow],
    dnse_rows: dict[tuple[str, dt.date], CanonicalRow],
    symbols: list[str],
    sessions: list[dt.date],
    price_tolerance: float = DEFAULT_PRICE_TOLERANCE,
    volume_tolerance: int = DEFAULT_VOLUME_TOLERANCE,
    symbol_ratios: dict[str, SymbolRatios] | None = None,
) -> list[SessionVerdict]:
    """The verdict for every session of every symbol."""
    verdicts: list[SessionVerdict] = []
    for symbol in symbols:
        for session in sessions:
            verdicts.append(
                classify_session(
                    symbol,
                    session,
                    yahoo_rows.get((symbol, session)),
                    dnse_rows,
                    sessions,
                    price_tolerance,
                    volume_tolerance,
                    (symbol_ratios or {}).get(symbol),
                )
            )
    return verdicts


# -- coverage --------------------------------------------------------------


def coverage_by_ticker(
    results: dict[str, list[ProviderResult]],
    symbols: list[str],
    sessions: list[dt.date],
) -> list[dict]:
    """One row per ticker and provider: did the request work, and how much of
    the calendar does it cover."""
    records: list[dict] = []
    session_set = set(sessions)
    for provider, provider_results in results.items():
        for result in provider_results:
            present = {row.date for row in result.rows}
            covered = len(present & session_set)
            traded = {row.date for row in result.rows if _value(row, "volume")}
            records.append(
                {
                    "symbol": result.symbol,
                    "provider": provider,
                    "request_status": result.request_status.value,
                    "rows": result.row_count,
                    "first_date": result.first_date.isoformat() if result.first_date else None,
                    "last_date": result.last_date.isoformat() if result.last_date else None,
                    "expected_sessions": len(session_set),
                    "sessions_with_data": covered,
                    "sessions_with_trades": len(traded & session_set),
                    "session_coverage": round(covered / len(session_set), 6) if session_set else None,
                    "rows_outside_window": len(present - session_set),
                    "elapsed_s": round(result.elapsed_s, 3) if result.elapsed_s is not None else None,
                    "source_api_version": result.source_api_version,
                }
            )
    return records


def coverage_by_day(
    results: dict[str, list[ProviderResult]],
    symbols: list[str],
    sessions: list[dt.date],
) -> list[dict]:
    """Per ticker and provider: which sessions are missing.

    A missing row is only called missing when the ticker traded on the session
    before and the session after it; a ticker that was suspended, or that
    started or stopped trading, is reported separately rather than counted
    against the provider.
    """
    records: list[dict] = []
    for provider, provider_results in results.items():
        for result in provider_results:
            present = {row.date for row in result.rows}
            missing = [s for s in sessions if s not in present]
            anchored = neighbour_anchored_gaps(present, sessions)
            records.append(
                {
                    "symbol": result.symbol,
                    "provider": provider,
                    "expected_sessions": len(sessions),
                    "sessions_with_data": len(sessions) - len(missing),
                    "missing_sessions": len(missing),
                    "missing_anchored_sessions": len(anchored),
                    "missing_dates": ";".join(d.isoformat() for d in missing[:60]),
                    "request_status": result.request_status.value,
                }
            )
    return records


def neighbour_anchored_gaps(present: set[dt.date], sessions: list[dt.date]) -> list[dt.date]:
    """The sessions on which a ticker has no row, but should have had one.

    A ticker that was suspended, or that started or stopped trading during the
    window, legitimately has no row. A gap is only called a gap when the ticker
    traded on the session immediately before it and on the session immediately
    after it.
    """
    if len(sessions) < 3:
        return []
    gaps: list[dt.date] = []
    for index in range(1, len(sessions) - 1):
        session = sessions[index]
        if session in present:
            continue
        if sessions[index - 1] in present and sessions[index + 1] in present:
            gaps.append(session)
    return gaps


# -- internal consistency --------------------------------------------------


def validate_ohlc(rows: list[CanonicalRow]) -> list[dict]:
    """The internal-consistency checks, per provider, symbol and series.

    Each source is checked on its own: a bar that is internally inconsistent is
    a problem of that source, whatever the other one says. The raw and the
    adjusted series are checked separately, because a symbol legitimately has
    one row of each for the same day.
    """
    records: list[dict] = []
    by_key: dict[tuple[str, str, str], list[CanonicalRow]] = defaultdict(list)
    for row in rows:
        by_key[(row.provider, row.symbol, row.price_type.value)].append(row)

    for (provider, symbol, price_type), group in sorted(by_key.items()):
        dates = [row.date for row in group]
        duplicate_dates = sorted({d.isoformat() for d in dates if dates.count(d) > 1})
        bad_range = []
        negative = []
        not_finite = []
        for row in group:
            prices = (row.open, row.high, row.low, row.close)
            if any(p is None or p != p or math.isinf(p) for p in prices):
                not_finite.append(row.date.isoformat())
                continue
            if row.low > row.open or row.open > row.high or row.low > row.close or row.close > row.high:
                bad_range.append(row.date.isoformat())
            if row.volume is None or row.volume != row.volume:
                not_finite.append(row.date.isoformat())
            elif row.volume < 0:
                negative.append(row.date.isoformat())
        records.append(
            {
                "provider": provider,
                "symbol": symbol,
                "price_type": price_type,
                "rows": len(group),
                "first_date": min(dates).isoformat() if dates else None,
                "last_date": max(dates).isoformat() if dates else None,
                "duplicate_dates": len(duplicate_dates),
                "duplicate_date_sample": ";".join(duplicate_dates[:10]),
                "ohlc_range_violations": len(bad_range),
                "ohlc_range_sample": ";".join(bad_range[:10]),
                "negative_volume_rows": len(negative),
                "negative_volume_sample": ";".join(negative[:10]),
                "non_finite_rows": len(not_finite),
                "non_finite_sample": ";".join(not_finite[:10]),
            }
        )
    return records


# -- the comparison --------------------------------------------------------


def build_pairs(
    yahoo_rows: dict[tuple[str, dt.date], CanonicalRow],
    dnse_rows: dict[tuple[str, dt.date], CanonicalRow],
    symbols: list[str],
    price_type: PriceType = PriceType.RAW,
    price_tolerance: float = DEFAULT_PRICE_TOLERANCE,
    volume_tolerance: int = DEFAULT_VOLUME_TOLERANCE,
) -> list[FieldPair]:
    """One pair per field per session, on the sessions both sources have."""
    pairs: list[FieldPair] = []
    for symbol in symbols:
        dates = sorted(
            {d for (s, d) in yahoo_rows if s == symbol}
            & {d for (s, d) in dnse_rows if s == symbol}
        )
        for session in dates:
            yahoo = yahoo_rows[(symbol, session)]
            dnse = dnse_rows[(symbol, session)]
            for field in ALL_FIELDS:
                tolerance = price_tolerance if field in PRICE_FIELDS else volume_tolerance
                pairs.append(
                    FieldPair(
                        symbol=symbol,
                        date=session,
                        field=field,
                        yahoo=_value(yahoo, field),
                        dnse=_value(dnse, field),
                        tolerance=float(tolerance),
                    )
                )
    return pairs


def field_metrics(pairs: list[FieldPair]) -> list[dict]:
    """The per-field accuracy summary."""
    records: list[dict] = []
    by_field: dict[str, list[FieldPair]] = defaultdict(list)
    for pair in pairs:
        by_field[pair.field].append(pair)

    for field in ALL_FIELDS:
        group = by_field.get(field, [])
        diffs = [p.abs_diff for p in group if p.abs_diff is not None]
        exact = sum(1 for p in group if p.matches)
        relative = [p.rel_diff for p in group if p.rel_diff is not None]
        records.append(
            {
                "field": field,
                "comparable_pairs": len(group),
                "exact_matches": exact,
                "exact_match_rate": round(exact / len(group), 6) if group else None,
                "mean_absolute_error": _mean(diffs),
                "median_absolute_error": _median(diffs),
                "max_absolute_error": max(diffs) if diffs else None,
                "mean_absolute_percentage_error": _mean(relative),
                "median_absolute_percentage_error": _median(relative),
                "pairs_beyond_tolerance": sum(
                    1 for p in group if p.abs_diff is not None and not p.matches
                ),
                "pairs_without_a_reference": sum(1 for p in group if p.rel_diff is None),
            }
        )
    return records


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def symbol_metrics(
    verdicts: list[SessionVerdict],
    yahoo_rows: dict[tuple[str, dt.date], CanonicalRow],
    symbols: list[str],
    symbol_ratios: dict[str, SymbolRatios] | None = None,
) -> list[dict]:
    """Per symbol: how the two sources line up, and what the differences are.

    The corporate-action columns come from Yahoo's own `Dividends` and
    `Stock Splits` columns: they say whether Yahoo claims a corporate action
    for that symbol, which is what turns a rescaled price difference from a
    mystery into an explained one.
    """
    records: list[dict] = []
    for symbol in symbols:
        symbol_verdicts = [v for v in verdicts if v.symbol == symbol]
        kinds = Counter(v.kind for v in symbol_verdicts)
        total = len(symbol_verdicts) or 1
        dividends = 0.0
        splits = 0.0
        for (row_symbol, _date), row in yahoo_rows.items():
            if row_symbol != symbol:
                continue
            dividends += float(row.raw.get("Dividends") or 0.0)
            splits += float(row.raw.get("Stock Splits") or 0.0)
        scales = [v.scale for v in symbol_verdicts if v.scale is not None]
        ratio = (symbol_ratios or {}).get(symbol)
        records.append(
            {
                "symbol": symbol,
                "sessions": len(symbol_verdicts),
                "exact_sessions": kinds.get(KIND_EXACT, 0),
                "exact_rate": round(kinds.get(KIND_EXACT, 0) / total, 6),
                "rescaled_sessions": kinds.get(KIND_RESCALED, 0),
                "shifted_sessions": kinds.get(KIND_SHIFTED, 0) + kinds.get(KIND_SHIFTED_SCALED, 0),
                "scaled_sessions": kinds.get(KIND_SCALED_PRICE, 0),
                "merged_sessions": kinds.get(KIND_MERGED, 0),
                "volume_difference_sessions": kinds.get(KIND_VOLUME_DIFFERENCE, 0),
                "price_difference_sessions": kinds.get(KIND_PRICE_DIFFERENCE, 0),
                "other_difference_sessions": kinds.get(KIND_OTHER, 0),
                "missing_in_yahoo_sessions": kinds.get(KIND_MISSING_YAHOO, 0),
                "missing_in_dnse_sessions": kinds.get(KIND_MISSING_DNSE, 0),
                "dominant_kind": kinds.most_common(1)[0][0] if kinds else None,
                "median_scale": round(statistics.median(scales), 8) if scales else None,
                "median_price_ratio": round(ratio.price_ratio, 8) if ratio and ratio.price_ratio else None,
                "median_volume_ratio": round(ratio.volume_ratio, 8)
                if ratio and ratio.volume_ratio
                else None,
                "price_ratio_spread": round(ratio.price_ratio_spread, 8)
                if ratio and ratio.price_ratio_spread is not None
                else None,
                "volume_ratio_spread": round(ratio.volume_ratio_spread, 8)
                if ratio and ratio.volume_ratio_spread is not None
                else None,
                "yahoo_dividends_total": dividends,
                "yahoo_stock_splits_total": splits,
            }
        )
    return records


def ratio_by_period(
    yahoo_rows: dict[tuple[str, dt.date], CanonicalRow],
    dnse_rows: dict[tuple[str, dt.date], CanonicalRow],
    symbols: list[str],
) -> list[dict]:
    """The price and volume ratios, per symbol and per month.

    A defect that only affects the older part of the window is invisible in a
    single number over the whole window, so the ratios are also reported per
    month: that shows whether a symbol's two series converge.
    """
    buckets: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: {"price": [], "volume": []}
    )
    for (symbol, session), yahoo in yahoo_rows.items():
        dnse = dnse_rows.get((symbol, session))
        if dnse is None:
            continue
        yahoo_close = _value(yahoo, "close")
        dnse_close = _value(dnse, "close")
        yahoo_volume = _value(yahoo, "volume")
        dnse_volume = _value(dnse, "volume")
        key = (symbol, session.strftime("%Y-%m"))
        if yahoo_close and dnse_close:
            buckets[key]["price"].append(yahoo_close / dnse_close)
        if yahoo_volume and dnse_volume:
            buckets[key]["volume"].append(yahoo_volume / dnse_volume)

    records: list[dict] = []
    for (symbol, period) in sorted(buckets):
        bucket = buckets[(symbol, period)]
        records.append(
            {
                "symbol": symbol,
                "period": period,
                "sessions": len(bucket["price"]),
                "median_price_ratio": _round(_median_or_none(bucket["price"])),
                "median_volume_ratio": _round(_median_or_none(bucket["volume"])),
                "price_ratio_spread": _round(_spread(bucket["price"])),
                "volume_ratio_spread": _round(_spread(bucket["volume"])),
            }
        )
    return records


def _round(value: float | None) -> float | None:
    return round(value, 8) if value is not None else None


def agreement_by_period(
    verdicts: list[SessionVerdict], months: int = 1
) -> list[dict]:
    """How well the two sources agree, over time.

    A defect that affects only the older part of the window is invisible in a
    single number over the whole window, so the agreement is also reported per
    period.
    """
    buckets: dict[str, list[SessionVerdict]] = defaultdict(list)
    for verdict in verdicts:
        key = verdict.date.strftime("%Y-%m" if months == 1 else "%Y-Q" + str((verdict.date.month - 1) // 3 + 1))
        buckets[key].append(verdict)
    records: list[dict] = []
    for key in sorted(buckets):
        group = buckets[key]
        kinds = Counter(v.kind for v in group)
        total = len(group) or 1
        records.append(
            {
                "period": key,
                "sessions": len(group),
                "exact_sessions": kinds.get(KIND_EXACT, 0),
                "exact_rate": round(kinds.get(KIND_EXACT, 0) / total, 6),
                "shifted_sessions": kinds.get(KIND_SHIFTED, 0) + kinds.get(KIND_SHIFTED_SCALED, 0),
                "scaled_sessions": kinds.get(KIND_SCALED_PRICE, 0),
                "merged_sessions": kinds.get(KIND_MERGED, 0),
                "price_difference_sessions": kinds.get(KIND_PRICE_DIFFERENCE, 0),
                "volume_difference_sessions": kinds.get(KIND_VOLUME_DIFFERENCE, 0),
                "other_difference_sessions": kinds.get(KIND_OTHER, 0),
            }
        )
    return records


def mismatch_records(
    pairs: list[FieldPair],
    verdicts: dict[tuple[str, dt.date], SessionVerdict],
    yahoo_rows: dict[tuple[str, dt.date], CanonicalRow],
    dnse_rows: dict[tuple[str, dt.date], CanonicalRow],
    limit: int | None = None,
) -> list[dict]:
    """One record per differing pair, with the session's classification."""
    records: list[dict] = []
    for pair in pairs:
        if pair.matches:
            continue
        verdict = verdicts.get((pair.symbol, pair.date))
        records.append(
            {
                "symbol": pair.symbol,
                "date": pair.date.isoformat(),
                "field": pair.field,
                "yahoo_value": pair.yahoo,
                "dnse_value": pair.dnse,
                "abs_diff": pair.abs_diff,
                "rel_diff": round(pair.rel_diff, 10) if pair.rel_diff is not None else None,
                "difference_kind": verdict.kind if verdict else KIND_OTHER,
                "scale": round(verdict.scale, 8) if verdict and verdict.scale else None,
                "matched_session": verdict.matched_session.isoformat()
                if verdict and verdict.matched_session
                else None,
                "detail": verdict.detail if verdict else "",
                "yahoo_raw": _raw_of(yahoo_rows.get((pair.symbol, pair.date)), pair.field),
                "dnse_raw": _raw_of(dnse_rows.get((pair.symbol, pair.date)), pair.field),
            }
        )
        if limit is not None and len(records) >= limit:
            break
    return records


def _raw_of(row: CanonicalRow | None, field: str):
    if row is None:
        return None
    return row.raw.get(field.capitalize())


def session_presence(
    results: dict[str, list[ProviderResult]],
    symbols: list[str],
    sessions: list[dt.date],
) -> list[dict]:
    """Per session: which provider had a row for how many symbols."""
    records: list[dict] = []
    for session in sessions:
        record = {"date": session.isoformat()}
        for provider, provider_results in results.items():
            with_row = set()
            with_volume = set()
            for result in provider_results:
                for row in result.rows:
                    if row.date != session:
                        continue
                    with_row.add(row.symbol)
                    volume = row.volume
                    if volume is not None and volume == volume and volume > 0:
                        with_volume.add(row.symbol)
            record[f"{provider}_symbols_with_data"] = len(with_row)
            record[f"{provider}_symbols_with_volume"] = len(with_volume)
        records.append(record)
    return records


def stability_report(runs: list[dict], symbols: list[str]) -> list[dict]:
    """How repeatable the fetch is.

    `runs` is one dict per repetition, keyed by `(provider, symbol)`. The
    report says how often the requests succeeded and how many values changed
    between repetitions — a change is a data revision by the provider, not a
    failure.
    """
    records: list[dict] = []
    if not runs:
        return records

    provider_names: list[str] = []
    for (provider, _symbol) in runs[0]:
        if provider not in provider_names:
            provider_names.append(provider)

    for provider in provider_names:
        results = [run.get((provider, symbol)) for run in runs for symbol in symbols]
        results = [r for r in results if r is not None]
        if not results:
            continue
        statuses = [r.request_status.value for r in results]
        success = sum(1 for s in statuses if s == "SUCCESS")
        latencies = [r.elapsed_s for r in results if r.elapsed_s is not None]

        changed = 0
        compared = 0
        for symbol in symbols:
            series = []
            for run in runs:
                result = run.get((provider, symbol))
                if result is None:
                    series.append({})
                    continue
                series.append(
                    {
                        row.date: (row.open, row.high, row.low, row.close, row.volume)
                        for row in result.rows
                        if row.symbol == symbol
                    }
                )
            if len(series) < 2:
                continue
            for session in set(series[0]):
                values = [s.get(session) for s in series[1:]]
                if any(v is None for v in values):
                    continue
                compared += 1
                if any(v != series[0][session] for v in values):
                    changed += 1
        records.append(
            {
                "provider": provider,
                "repetitions": len(runs),
                "symbols": len(symbols),
                "successful_requests": success,
                "request_success_rate": round(success / len(results), 6) if results else None,
                "statuses": ";".join(statuses),
                "mean_latency_s": round(statistics.fmean(latencies), 3) if latencies else None,
                "max_latency_s": round(max(latencies), 3) if latencies else None,
                "values_compared": compared,
                "values_changed_between_repetitions": changed,
                "value_change_rate": round(changed / compared, 6) if compared else None,
            }
        )
    return records
