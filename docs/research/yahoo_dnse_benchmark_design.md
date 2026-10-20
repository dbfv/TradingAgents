# Yahoo Finance vs DNSE OpenAPI — benchmark design

Scope: a reproducible, read-only comparison of Yahoo Finance (`yfinance`) daily
OHLCV against DNSE OpenAPI daily OHLCV for the 30 VN30 constituents, using DNSE
as the cross-check source.

The DNSE side of the API is documented in
[`dnse_openapi_notes.md`](dnse_openapi_notes.md) — that file is the source of
truth for every DNSE endpoint, parameter, field and unit used here.

---

## 1. Current architecture of the Yahoo path in this repository

```
tradingagents/agents/tools.py                 # LLM-facing tools, one per data category
        │  get_stock_data(symbol, start_date, end_date)
        ▼
tradingagents/dataflows/router.py             # route_to_vendor(): vendor chain, error sentinels
        │  VENDOR_METHODS["get_stock_data"] = {"alpha_vantage": …, "yfinance": get_YFin_data_online}
        ▼
tradingagents/dataflows/vendors/yahoo/
        common.py     # yf_retry(): rate-limit backoff, "answered empty" detection,
                      #             raise_for_empty() -> VendorUnavailableError | NoMarketDataError
        market.py     # get_YFin_data_online() -> CSV *string*
        ohlcv.py      # load_ohlcv() -> pandas DataFrame (cached, gap-filled)
        news.py, fundamentals.py, snapshot.py
```

### 1.1 The schema downstream agents expect

`get_YFin_data_online(symbol, start_date, end_date) -> str`:

```
# Stock data for {label} from {start_date} to {end_date}
# Total records: {n}

{csv with the date index + Open, High, Low, Close, Volume [, Adj Close, Dividends, Stock Splits]}
```

- It returns a **CSV string**, not a DataFrame. `Alpha Vantage`'s
  `get_alpha_vantage_stock` returns the same shape, so the router level is
  shape-compatible.
- `end_date` is **inclusive** at the caller level; the vendor adds one day
  because `yfinance`'s `end=` is exclusive.
- The index is tz-stripped, OHLCV is rounded to 2 decimals, and a staleness
  guard rejects a newest row more than `MAX_OHLCV_STALE_DAYS` (10) days before
  `end_date`.
- `load_ohlcv(symbol, as_of_date)` is the DataFrame path (5-year lookback,
  cached under `data_cache_dir`, columns `Date, Open, High, Low, Close, Volume`,
  date normalised to local midnight).

### 1.2 Symbol normalisation — the reason this benchmark exists

`tradingagents/dataflows/symbols.py::normalize_symbol` maps a user/broker symbol
to a Yahoo symbol. It handles metals/energy/index CFDs, crypto (`BASE-USD`),
forex (`PAIR=X`), Hong Kong (4-digit padding) and Shanghai (`.SH` → `.SS`).
**Every other symbol is returned upper-cased unchanged.** There is no `.VN`
rule.

Measured consequence (2026-10-20, `yfinance==1.7.0`):

| symbol | Yahoo rows for 2026-09-01 → 2026-09-15 |
|---|---|
| `VIC` (bare) | 0 — *"possibly delisted; no timezone found"*, HTTP 404 `Quote not found for symbol: VIC` |
| `VIC.VN` | 10 sessions, tz-aware at `+07:00` |

So a bare Vietnamese ticker silently produces an empty price series, which is
exactly the failure mode `normalize_symbol`'s own docstring describes for other
markets. **This benchmark does not change `normalize_symbol`** — it measures
whether the `.VN` convention is sound before that change is considered.

### 1.3 Errors and rate limiting in the existing Yahoo path

`tradingagents/dataflows/vendors/yahoo/common.py`:

- `YFRateLimitError` → exponential backoff (`2^attempt × 2 s`, 3 retries) →
  `VendorUnavailableError` if it outlasts the retries.
- `YFPricesMissingError` / HTTP 404 → the symbol "answered empty" → `None` →
  `NoMarketDataError`.
- Any other exception → `VendorUnavailableError`.
- `raise_for_empty()` probes `https://query2.finance.yahoo.com` before claiming
  a symbol has no data, so an outage is not reported as a missing symbol.

**The existing taxonomy collapses too much for a benchmark**: everything becomes
either "no data" or "vendor unavailable". The benchmark needs to tell
`SYMBOL_NOT_FOUND` from `RATE_LIMITED` from `NETWORK_ERROR` from
`TIMEZONE_ERROR`, so it defines its own status enum (§4.2) and does **not**
reuse the vendor error classes. The production path is left untouched.

### 1.4 Test conventions in this repository

- `tests/` is **flat** (84 `test_*.py` files) with `tests/__init__.py` and a
  `tests/conftest.py`.
- `tests/conftest.py` blocks the network for every test by monkeypatching
  `socket.socket.connect` **and** `curl_cffi.requests.Session.request` (yfinance
  uses libcurl, which bypasses Python sockets). A test that must reach the
  network is marked `@pytest.mark.integration`.
- `pyproject.toml`: `addopts = "-ra --strict-markers -m \"not integration\""`,
  markers `unit` / `integration` / `smoke`, `testpaths = ["tests"]`.
- Ruff: line length 100, `select = ["E","W","F","I","B","UP","C4","SIM"]`,
  `target-version = "py311"`, `combine-as-imports = true`.
- Dependencies live in `pyproject.toml`; `requirements.txt` is a one-line shim
  containing `.`.
- `docs/` and `artifacts/` do not exist yet. `result/` is the existing
  gitignored output directory; `artifacts/` is added to `.gitignore` so that
  provider data is never committed.

---

## 2. The VN30 universe (reproducible, with a reference date)

The plan's list of 30 expected tickers is **stale**. It contains `DGC`, `PLX`
and `TPB`, none of which is in the current index.

The universe used here is the HOSE index review that took effect **2026-08-03**
(kỳ rà soát 7/2026), read from the HOSE *Công bố thông tin* (CBTT)
"Thông tin danh mục cổ phiếu thành phần chỉ số VN30":

| # | Symbol | # | Symbol | # | Symbol | # | Symbol | # | Symbol | # | Symbol |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ACB | 7 | GVR | 13 | MSN | 19 | STB | 25 | VIC | | |
| 2 | BID | 8 | HDB | 14 | MWG | 20 | TCB | 26 | VJC | | |
| 3 | BSR | 9 | HPG | 15 | SAB | 21 | TCX | 27 | VNM | | |
| 4 | CTG | 10 | LPB | 16 | SHB | 22 | VCB | 28 | VPB | | |
| 5 | FPT | 11 | MBB | 17 | SSB | 23 | VHM | 29 | VPL | | |
| 6 | GAS | 12 | MCH | 18 | SSI | 24 | VIB | 30 | VRE | | |

Changes relative to the plan's expected list: `DGC` → `BSR` (BSR entered in the
May 2026 review when DGC was placed under a late-audit warning), `PLX` and `TPB`
out and `MCH` and `TCX` in (July 2026 review, effective 2026-08-03).

The snapshot is stored in
`tests/market_data_benchmark/fixtures/vn30_universe.csv` with `symbol`,
`exchange`, `index`, `effective_from`, `source_url`, `retrieved_at` and
`yahoo_symbol`, so a later run reproduces the same universe. The benchmark uses
the **current** index composition, not a point-in-time history, and says so in
`run_metadata.json` (`universe_kind: "current_composition"`).

`DNSE /market/instruments?indexName=VN30` is queried as a **cross-check** of
the index membership and of the ticker metadata (exchange `STO` = HOSE, listing
date, issuer name). It is not used to *derive* membership from symbol existence.

---

## 3. Comparison window and the session calendar

**Window: 2025-10-20 → 2026-10-19** (12 months ending on the last *completed*
session before the run).

- 12 months was chosen over 24 because DNSE's `/market/working-dates` covers one
  year from today, and the benchmark needs a window that both providers can be
  measured against on the same calendar.
- The **last day is excluded** because the current session is incomplete: at the
  time of the run Yahoo already carried a partial `2026-10-20` bar (volume
  325 192 at the time of the smoke test) while DNSE had no `2026-10-20` candle
  at all. Including it would manufacture a false mismatch.

### 3.1 Why the calendar cannot come from `/market/working-dates`

Measured live on 2026-10-20: the endpoint returned **258** dates spanning
**2026-10-20 → 2027-10-20** — the window runs *forward* from today. It
therefore contains **no** historical session for a 12-month back-test window.

### 3.2 The calendar the benchmark uses, and how far it can be checked

`calendars.py` derives the market session set from the **union of the sessions
actually observed in the data**, where a session is a date on which a symbol
traded with volume > 0:

```
market_sessions = { d : ∃ symbol, ∃ provider, row(symbol, d) with volume > 0 }
```

- Using the **union** (not the intersection) means a date that one provider
  dropped is still a session — otherwise a Yahoo gap would silently shrink the
  denominator and hide the gap.
- Volume > 0 is the discriminator because **Yahoo emits a filler row for every
  weekday**, including holidays, with `O=H=L=C=previous close` and `Volume = 0`.
  Those rows are not sessions. 13 such rows were measured in the 12-month
  window (New Year, Tết, Hùng Kings, Labour Day, National Day, …).
- A ticker-level "expected session" is a market session on which that ticker
  should have traded. A ticker that was suspended legitimately has no row, so a
  per-ticker gap is only reported as a gap when the ticker traded on the
  sessions immediately before and after it (a "neighbour-anchored" gap). Gaps
  at the window edges are reported separately.

**How far the official calendar can check it.** `GET /market/working-dates`
covers one year **forward** from today (verified live: 258 dates from
2026-10-20 to 2027-10-20), so it cannot be compared session by session with a
historical window — the two sets do not overlap at all. What *can* be checked
is the shape of the calendar: the derived window holds 248 sessions and the
official forward year holds 258, which is the same order, and the run reports
both numbers. The report says this plainly rather than claiming a validation
that did not happen, and the limitation is listed.

A ticker-level "expected session" gap is only reported when the ticker traded
on the session before and the session after it.

### 3.3 Normalisation rules (both providers)

| Field | Rule |
|---|---|
| `symbol` | bare exchange symbol (`VIC`). `yahoo_symbol` (`VIC.VN`) is kept separately. |
| `date` | the session date in `Asia/Ho_Chi_Minh` (UTC+7). Yahoo's index is already tz-aware at +07:00; DNSE's `t` is converted from epoch seconds. |
| `price` | normalised to **VND**. Yahoo is already VND (×1). DNSE is **thousand VND → ×1000**, then rounded to the nearest đồng (integer) to remove the float residue (`130.7 × 1000 = 130699.99999999999`). |
| `volume` | **shares**, integer, both providers (verified equal on matched sessions). |
| `currency` | `VND` (DNSE returns no currency field; established by measurement — see the notes, §7.3). |
| `price_type` | `raw` (unadjusted OHLC) vs `adjusted` (dividend/split-adjusted). **Never merged.** |
| `exchange` | from the universe fixture (`HOSE`), cross-checked against DNSE `marketId` (`STO` = HOSE). |
| raw fields | every provider-native field is kept in `raw` so a mismatch can be traced back to the source value. |

### 3.4 Adjusted prices — what is actually comparable

| Provider | Raw OHLC | Adjusted OHLC | Adjusted close |
|---|---|---|---|
| Yahoo | yes (`auto_adjust=False`) | yes (`auto_adjust=True`) | `Adj Close` column |
| DNSE | yes | **no** — no adjusted series, and no market-wide corporate-action feed the benchmark can read with market-data credentials | no |

Measured: for `VIC.VN` over the full 12-month window, Yahoo's `Adj Close`
equals `Close` on **262 of 262 rows** and the `Dividends` column is **0 on every
row**. For other symbols (`SSI`) Yahoo *does* report a dividend and a stock
split, and its `Adj Close` differs from its `Close` on most rows.

Consequence, stated plainly: **adjusted-price accuracy cannot be scored in
this benchmark.** The adjusted comparison is reported as "not comparable", not
as a pass. The raw-price comparison is the one that carries weight — and even
that comparison shows that *both* vendors serve an adjusted history, each with
its own adjustment, so a price from one is not directly comparable with a price
from the other for dates before the last corporate action.

---

## 4. Code layout

```
tests/market_data_benchmark/
    __init__.py
    config.py                 # window, thresholds, paths
    schema.py                 # canonical row, status enum, provider result
    normalization.py          # symbol/date/unit normalisation
    calendars.py              # session derivation, neighbour-anchored gaps
    metrics.py                # coverage, field metrics, session classifier
    universe.py               # load / validate the VN30 fixture
    trade_check.py            # the third-source (trade feed) cross-check
    report.py                 # writes the artifacts
    run_benchmark.py          # CLI entry point
    providers/
        __init__.py
        base.py               # shared provider contract
        yahoo_provider.py
        dnse_provider.py
    fixtures/
        vn30_universe.csv
        dnse/                 # reduced, credential-free sample responses
    test_*.py                 # unit tests (mocked, no network)
    test_integration_providers.py   # real APIs, marked @pytest.mark.integration

docs/research/
    dnse_openapi_notes.md
    yahoo_dnse_benchmark_design.md

artifacts/market_data_benchmark/<run_id>/   # gitignored
```

### 4.1 Provider contract

Both providers implement the same read-only interface and return the same
shapes, so the comparison code never knows which vendor it is talking to:

```python
@dataclass
class ProviderResult:
    provider: str
    symbol: str
    request_status: RequestStatus
    rows: list[CanonicalRow]
    request_start: datetime | None
    request_end: datetime | None
    retrieved_at_utc: datetime
    source_api_version: str
    error: ProviderError | None
    elapsed_s: float | None
    raw: dict                     # provider-native response, for traceability
```

Rules that apply to both:

- Read-only market data only. No order, account, balance, position or personal
  endpoint is ever called.
- Bounded retry with exponential backoff, honouring the provider's rate limit;
  a *missing symbol* is never retried as if it were a network failure.
- Every request carries an explicit status (see §4.2) — errors are never
  flattened into a single "vendor unavailable" bucket.
- Source metadata (API version, request range, retrieval time) travels with
  every result.
- Credentials are read from the environment and never logged.

### 4.2 Request status enum

`SUCCESS`, `EMPTY_RESPONSE`, `SYMBOL_NOT_FOUND`, `RATE_LIMITED`,
`NETWORK_ERROR`, `TIMEZONE_ERROR`, `UNSUPPORTED_OR_UNKNOWN`,
`PERMISSION_ERROR`, `AUTH_ERROR`, `INVALID_PARAMETER`, `NOT_CONFIGURED`.

Mapping — Yahoo: `YFPricesMissingError`/404 → `SYMBOL_NOT_FOUND`;
`YFRateLimitError`/429 → `RATE_LIMITED`; `YFTzMissingError` →
`TIMEZONE_ERROR`; empty frame → `EMPTY_RESPONSE`; socket/connection errors →
`NETWORK_ERROR`; anything else → `UNSUPPORTED_OR_UNKNOWN`.

Mapping — DNSE: HTTP 401 → `AUTH_ERROR`; 403 → `PERMISSION_ERROR`;
404 → `SYMBOL_NOT_FOUND`; 429 → `RATE_LIMITED`; 400 → `INVALID_PARAMETER`;
5xx → `NETWORK_ERROR` (retried, bounded); a well-formed response with zero
candles → `EMPTY_RESPONSE`; missing SDK/credentials → `NOT_CONFIGURED`.

### 4.3 Rate limiting

- Yahoo: the repository's existing backoff shape (3 retries, `2^attempt ×
  2 s`), reimplemented locally — the benchmark must not mutate production
  state, and `yf_retry` raises the vendor error classes the benchmark does not
  use.
- DNSE: 3 retries with the same backoff on 429/5xx only, with a small fixed
  delay between symbols so 30 symbols do not arrive as a burst. The observed
  `X-Ratelimit-*` headers are recorded per request when the SDK exposes them.

### 4.4 How a difference is classified

The comparison is at the level of a **session**, not of a field, because a
field-level difference cannot say *what* went wrong. The classifier uses the
**volume as the fingerprint**: a corporate action may scale a price, but never
a volume, so a volume that matches a *different* session identifies that
session's bar.

| Kind | Meaning |
|---|---|
| `exact` | the same bar in both |
| `scaled_price` | the same session, prices multiplied by a constant |
| `rescaled_session` | the same session, prices **and** volume multiplied by constants |
| `shifted_session` | the volume identifies the bar as the neighbouring session's |
| `shifted_and_scaled_session` | the same, with the prices also scaled |
| `shifted_session_with_different_prices` | the volume identifies the neighbour, the prices differ by more than a constant |
| `merged_sessions` | two sessions' volume in one bar |
| `price_difference` | the same session, the same volume, prices that no constant explains |
| `volume_difference` | the same session, the same prices, a different volume |
| `other_difference` | none of the above |
| `missing_in_yahoo` / `missing_in_dnse` / `missing_in_both` | a row is missing |

The per-symbol **ratio** between the two series (Yahoo ÷ DNSE, for the close
and for the volume) is reported as well, because a constant ratio that is not 1
is the signature of an adjustment, and a ratio of exactly 1 is the signature of
agreement.

---

## 5. The checks the benchmark performs

| # | Check | Output |
|---|---|---|
| 5.1 | **Ticker coverage** — request status per provider, row counts, first/last date, session coverage ratio | `ticker_coverage.csv` |
| 5.2 | **Daily coverage** — per ticker and provider: expected sessions, present, missing, missing-in-both, provider-only | `daily_coverage.csv` |
| 5.3 | **Internal OHLC validity** (per provider, per series, separately) — `low ≤ open ≤ high`, `low ≤ close ≤ high`, non-negative price/volume, no NaN/Inf, no unexplained duplicate dates | `ohlc_validation.csv` |
| 5.4 | **Field-by-field price comparison** on rows that share ticker *and* session date — per field: pairs, exact matches, MAE, median AE, max AE, MAPE (only where the reference is non-zero), share beyond threshold | `field_metrics.csv` |
| 5.5 | **Session classification** — every symbol-session is classified (§4.4), and the per-symbol and per-month ratios are reported | `session_classification.csv`, `symbol_metrics.csv`, `agreement_by_month.csv`, `ratio_by_month.csv` |
| 5.6 | **Third-source cross-check** — DNSE's own trade feed for a sample of sessions, as an auxiliary check | `trade_cross_check.csv` |
| 5.7 | **Stability** — a controlled subset is fetched repeatedly; success rate, latency, and the share of values that changed between fetches | `stability.csv` |
| 5.8 | **Request outcome accounting** — success rate and error breakdown per provider | `provider_errors.csv` |

The thresholds in §10 of the plan live in `config.py` and are not hard-coded in
the comparison code.

### 5.1 What the run found

The measured outcome is in
`artifacts/market_data_benchmark/<run_id>/summary.md`. In summary:

- Both providers answer every one of the 30 tickers. DNSE covers 99.9% of the
  window's sessions, Yahoo 98.0%.
- For the most recent weeks the two series are **identical**, field by field.
- Over the 12-month window, for 20 of the 30 symbols the two closes differ by a
  **constant factor** (1.005 to 1.085), and for 13 symbols their volumes differ
  by a constant factor as well (1.07 to 2.0). A constant factor is the
  signature of an **adjustment**, not of a different price — and Yahoo's own
  `Dividends` and `Stock Splits` columns are non-zero for some of these
  symbols, while DNSE publishes no adjustment at all.
- Yahoo's `.VN` series also contains, over parts of the window, bars that
  belong to the **neighbouring session**, and **zero-volume filler rows** for
  non-trading days.
- Yahoo is **missing history** for symbols that listed recently: VPL's Yahoo
  series starts on 2026-03-17, while DNSE's starts on the first session of the
  window.
- Neither source is a clean "raw price" series: for the same session the two
  differ by a constant factor, and DNSE's own trade feed agrees with the
  *recent* daily bars on both the closing price and the traded volume, while
  for older sessions the daily bars do not reconcile with the trade feed.
  The corporate-action history that would explain it is not readable with
  market-data credentials.

The conclusion the evidence supports: **DNSE OpenAPI is the better primary
source for Vietnamese daily market data, Yahoo is a usable fallback for short
recent windows, and the two are not interchangeable for a multi-year history
without a corporate-action feed.**

---

## 6. Thresholds (configurable, in `config.py`)

| Metric | Target | Note |
|---|---|---|
| Ticker coverage | 30/30 | members of the dated VN30 fixture |
| Daily session coverage | ≥ 99% | over valid sessions; non-session days excluded |
| Raw OHLC match | reported per field | absolute thresholds only after units/rounding are aligned |
| Volume match | reported per field | units verified first |
| API request success | ≥ 99% in the controlled repeat | credential/permission errors counted separately |
| Critical unresolved discrepancies | 0 before choosing a primary source | unexplained differences must be explained or marked unresolved |
| Adjusted price | scored only if semantics are equivalent | **not equivalent here — not scored** |

No threshold is met by dropping days, interpolating prices or excluding
failing tickers.

## 7. Safety

- Only read-only market-data endpoints are called. No order, cancel, modify,
  account, balance, position or personal endpoint. No trading token or OTP is
  requested or used.
- Credentials come from `DNSE_API_KEY` / `DNSE_API_SECRET` in the environment
  and are never written to source, fixtures, logs, artifacts or reports.
- Raw provider payloads are not committed: `artifacts/` is gitignored and only
  a reduced, credential-free sample is kept under `fixtures/dnse/`.
- No production routing or vendor configuration is changed by this benchmark.

## 8. What the benchmark changed afterwards

The benchmark itself changed nothing. Its findings then informed a separate
change: a **DNSE vendor** that serves the daily bars of a Vietnamese listing.

```
tradingagents/dataflows/vendors/dnse/
    __init__.py
    common.py     # the client, the retry, the error mapping, the symbol rules
    market.py     # get_DNSE_data_online: the CSV the market analyst reads
    ohlcv.py      # load_dnse_ohlcv: the frame the indicators are computed from

tradingagents/dataflows/ohlcv.py       # which vendor serves a symbol's prices
scripts/verify_dnse_vendor.py          # proves which vendor answered
tests/test_dnse_vendor.py              # unit tests (mocked)
tests/test_dnse_vendor_integration.py  # against the real API
```

**How a symbol is routed.** The routing layer picks a vendor per *tool*, but a
symbol's market decides which vendor can serve it at all: Yahoo has no
Vietnamese listing under a bare ticker, and the DNSE OpenAPI has no US
listing. So the chain is resolved per symbol, from configuration:

```python
"market_vendors": {
    ".VN": "dnse,yfinance",   # Vietnamese listings: DNSE first, Yahoo after
},
"fallback_vendors": {
    "get_stock_data": "dnse",  # tried only after every configured vendor has
                               # answered that it has no such symbol
},
```

- The market rule is **explicit configuration**, not a hidden re-route, and the
  configured chain still follows it, so a symbol the rule does not cover keeps
  exactly the chain it had.
- The fallback vendors run **only** after every configured vendor has answered
  "I have no such symbol". That is the case a bare Vietnamese ticker lands in:
  Yahoo answers a bare `VIC` with a 404, and DNSE lists it.
- Without `DNSE_API_KEY` / `DNSE_API_SECRET` the DNSE vendor raises
  `VendorNotConfiguredError`, which the router already treats as "this vendor
  cannot answer" — the request falls through to Yahoo.

**Every stage of the price path goes through the same source.** The routed
tool, the indicator frame, the verified market snapshot and the closes the
memory log settles all read the dispatcher, so the prices the agent is told to
trust and the prices it is shown cannot come from two different vendors. The
DNSE CSV also carries a `# Source:` line, so the provenance is visible in the
data the analyst reads.

**Checking it.** `scripts/verify_dnse_vendor.py` instruments the two HTTP
transports (the SDK's urllib3 pool and yfinance's curl_cffi session) and
reports, for each stage, which vendor answered and how many requests each
host received:

```console
$ python scripts/verify_dnse_vendor.py --symbol VIC.VN
stage                              served by  hint              dnse reqs  yahoo reqs
--------------------------------------------------------------------------------------
get_stock_data (routed tool)       dnse       dnse                      1           0
OHLCV frame (indicators)           dnse       dnse                      1           0
verified market snapshot           dnse       consistent                1           0
closes (memory-log settlement)     dnse       read                      1           0

VERDICT: the DNSE OpenAPI served every stage.
```

## 9. Verdict

One of `PASS`, `PASS WITH LIMITATIONS`, `FAIL`, `INCONCLUSIVE`, `BLOCKED`, with
quantified evidence and sample sizes for every claim, in
`artifacts/market_data_benchmark/<run_id>/summary.md`.
