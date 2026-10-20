# DNSE OpenAPI — verified documentation notes

Everything below was read on **2026-10-20** from the official DNSE Developer Portal
and the official `dnse-tech/openapi-sdk` repository. Nothing here is inferred from
blog posts or guesses: every endpoint, parameter, field and unit was read from the
official OpenAPI specification, the official SDK source, or from a live Production
response. Items that could not be verified are listed explicitly in
[§10 Not verified](#10-not-verified--still-open).

---

## 1. Pages and sources read

| # | Source | URL | Read |
|---|--------|-----|------|
| 1 | DNSE Developer Portal (root) | https://developers.dnse.com.vn/ | 2026-10-20 |
| 2 | Market Data — overview | https://developers.dnse.com.vn/docs/dnse/market-data/ | 2026-10-20 |
| 3 | Lịch sử OHLC (OHLC history) | https://developers.dnse.com.vn/docs/dnse/get-ohlc-history/ | 2026-10-20 |
| 4 | Ngày làm việc (Working dates) | https://developers.dnse.com.vn/docs/dnse/get-market-working-dates/ | 2026-10-20 |
| 5 | Chi tiết mã chứng khoán (Instruments) | https://developers.dnse.com.vn/docs/dnse/get-instruments/ | 2026-10-20 |
| 6 | Thông tin giao dịch chứng khoán (Security definition) | https://developers.dnse.com.vn/docs/dnse/get-symbol-secdef/ | 2026-10-20 |
| 7 | Giá đóng cửa (Close price) | https://developers.dnse.com.vn/docs/dnse/get-price-symbol-close/ | 2026-10-20 |
| 8 | Thông tin sự kiện quyền (Corporate actions) | https://developers.dnse.com.vn/docs/dnse/get-corporate-actions/ | 2026-10-20 |
| 9 | DNSE official Python SDK README | https://github.com/dnse-tech/openapi-sdk/blob/main/python/README.md | 2026-10-20 |
| 10 | SDK example `get_ohlc.py` | https://github.com/dnse-tech/openapi-sdk/blob/main/python/marketdata-api/get_ohlc.py | 2026-10-20 |
| 11 | SDK example `get_working_dates.py` | https://github.com/dnse-tech/openapi-sdk/blob/main/python/marketdata-api/get_working_dates.py | 2026-10-20 |
| 12 | SDK example `get_instruments.py` | https://github.com/dnse-tech/openapi-sdk/blob/main/python/marketdata-api/get_instruments.py | 2026-10-20 |
| 13 | SDK example `get_security_definition.py` | https://github.com/dnse-tech/openapi-sdk/blob/main/python/marketdata-api/get_security_definition.py | 2026-10-20 |
| 14 | OpenAPI spec (official repo, `spec/dnse-openapi-2026-09-15.yaml`) | https://github.com/dnse-tech/openapi-sdk/tree/main/spec | 2026-10-20 |
| 15 | OpenAPI spec (official repo, `spec/dnse-openapi-2026-07-23.yaml`) | https://github.com/dnse-tech/openapi-sdk/tree/main/spec | 2026-10-20 |
| 16 | SDK source `dnse/api/client.py` (v2.4.1) | https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/api/client.py | 2026-10-20 |
| 17 | SDK source `dnse/api/common.py` (v2.4.1) | https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/api/common.py | 2026-10-20 |

The developer portal renders each endpoint from a Docusaurus OpenAPI spec. The
spec is **also published in the official SDK repository** under `spec/`, which is
what was used to read parameter and response schemas in full (the portal's
rendered tables are client-side and the raw spec file is not served from the
portal's own origin).

### API version published by the current documentation

- `dnse/api/common.py` → `DEFAULT_API_VERSION = "2026-07-23"`.
- SDK README → `api_version="2026-07-23"` for both Production and Sandbox.
- Spec files published in the repo: `2026-07-23`, `2026-09-10`, `2026-09-15`.
- The `/price/ohlc` schema is **identical** in `2026-07-23` and `2026-09-15`
  (verified by diff — only the file encoding differs).

**Benchmark pins `api_version=2026-07-23`** (the version the shipped SDK signs
by default) and records it in `run_metadata.json`.

---

## 2. SDK package and install

- PyPI package: **`dnse-sdk-openapi`**. Latest published version at read time:
  **2.4.1** (uploaded 2026-10-05). Read from the PyPI release history and
  installed from PyPI; the GitHub tag `v2.4.1` points at the same commit
  (`aa55a5117de2534707bca6165b6738e1cc6fc6a4`).
- Import: `from dnse import DNSEClient`.
- **The published package declares no dependencies** (`pyproject.toml` has no
  `dependencies` list) but `dnse/__init__.py` imports the WebSocket client, which
  imports `msgpack`. `pip install dnse-sdk-openapi` alone therefore fails with
  `ModuleNotFoundError: No module named 'msgpack'`. `msgpack` must be installed
  explicitly. This benchmark pins both.
- Benchmark dependency declaration (repo convention: `pyproject.toml` is the
  single source of truth; `requirements.txt` is a one-line shim containing `.`):
  `pyproject.toml` → `[project.optional-dependencies] benchmark = ["dnse-sdk-openapi==2.4.1", "msgpack>=1.1.0"]`.
  It is an *optional* extra so the production install of TradingAgents is
  unchanged.

## 3. Environments, base URLs and credentials

| | Production | Sandbox |
|---|---|---|
| REST base URL | `https://openapi.dnse.com.vn` | `https://sb-openapi.dnse.com.vn` |
| WebSocket | `wss://ws-openapi.dnse.com.vn` | `wss://ws-sb-openapi.dnse.com.vn` |
| API version | `2026-07-23` | `2026-07-23` |
| Credentials | Production API Key + API Secret | Sandbox API Key + API Secret |

Production and Sandbox use **different** credentials; they must not be mixed.

Environment variables used by the official SDK: `DNSE_API_KEY`, `DNSE_API_SECRET`,
`DNSE_API_VERSION` (SDK default `2026-07-23`). The SDK examples additionally use
`DNSE_BASE_URL` (default `https://openapi.dnse.com.vn`).

**This benchmark reads `DNSE_API_KEY` / `DNSE_API_SECRET` from the environment
(`.env` is loaded by the repository's existing `python-dotenv` setup). No
credential is written to source, fixtures, logs, artifacts or reports.**

## 4. Authentication / request signing

Handled entirely by the official SDK — no custom signer was written.

`dnse/api/common.py::build_signature`:

- Signature string:
  `(request-target): {method.lower()} {path}\n{date_header_name.lower()}: {date_value}`
  plus `\nnonce: {nonce}` when a nonce is used.
- HMAC over that string with the **API Secret used as raw UTF-8 bytes**
  (`hmac.new(secret.encode("utf-8"), ...)`) — the secret is *not* base64-decoded.
  The benchmark passes the secret exactly as issued.
- Default algorithm `hmac-sha256` (also supports sha384/sha512; `sha1` is the
  fallback for an unknown algorithm name).
- Base64 digest, then URL-quoted (`parse.quote(encoded, safe="")`).
- Header value:
  `Signature keyId="{api_key}",algorithm="{algorithm}",headers="(request-target) {header_name_lower}",signature="{sig}"[,nonce="{nonce}"]`
- Date header value: `datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")`.
- Date header **name** comes from `os.getenv("DATE_HEADER", "Date")` — i.e. the
  SDK's default header name is `Date`, configurable via the `DATE_HEADER`
  environment variable.
- A random `nonce` (uuid4 hex) is added by default (`hmac_nonce_enabled=True`).

Request headers actually sent by the SDK (`dnse/api/client.py::_request`):

| Header | Value |
|---|---|
| `x-api-key` | API Key |
| `X-Signature` | signature string above |
| `Date` (or `DATE_HEADER`) | request timestamp (UTC, RFC-1123 style) |
| `version` | API version (`2026-07-23`) |
| `Content-Type` | `application/json` (only when a body is sent) |

> **Documented discrepancy (recorded, not resolved by guesswork).** The OpenAPI
> spec lists the date header as **`X-Aux-Date`** (`required: true`), while the
> shipped SDK sends **`Date`** by default and names the header
> `X-Aux-Date` only if the `DATE_HEADER` environment variable is set to that
> value. Both were read from official sources and disagree. The benchmark uses
> the SDK's own default (`Date`), because the SDK is the official client and its
> default is what the live Production API answered on 2026-10-20 (HTTP 200).
> The spec's `X-Aux-Date` spelling is recorded here as the documented variant.

Response headers exposed by the API (read from the spec's response objects):
`X-Ratelimit-Limit`, `X-Ratelimit-Remaining`, `X-Ratelimit-Reset`,
`X-Request-Id`, `X-Tyk-Api-Expires`, `Date`, `Vary`, `Content-Type`.

## 5. Market Data endpoints used by this benchmark

The SDK returns `(status, body)` for every call: `status` is the HTTP status
code, `body` is the **response text** (a JSON string, not a parsed object).

### 5.1 `get_ohlc` — historical candles

- SDK signature: `get_ohlc(bar_type, query=None, dry_run=False)`
- Endpoint: `GET /price/ohlc`
- The SDK copies `query` and adds `type=<bar_type>` to the query string.
- Official example (`marketdata-api/get_ohlc.py`):

```python
status, body = client.get_ohlc(
    bar_type="STOCK",
    query={"symbol": "HPG", "resolution": "1", "from": 1735689600, "to": 1735776000},
    dry_run=False,
)
```

Query parameters (from the spec):

| Name | Required | Type | Description / allowed values | Example |
|---|---|---|---|---|
| `symbol` | yes | string | Mã chứng khoán | `ACB` |
| `type` | no | string | `STOCK` (cổ phiếu) · `DERIVATIVE` (phái sinh) · `INDEX` (chỉ số) | `STOCK` |
| `resolution` | yes | string | Khung thời gian nến: `1, 3, 5, 15, 30, 1h, 1D, 1W` | `15` |
| `from` | yes | integer | Thời gian bắt đầu, **Unix timestamp (giây)** | `1773657310` |
| `to` | yes | integer | Thời gian kết thúc, **Unix timestamp (giây)** | `1773830110` |

- **Daily bars use `resolution="1D"`.** This is the only daily value in the
  documented enumeration (there is no `1d`).
- `from`/`to` are plain epoch seconds. The spec does not state whether the range
  is inclusive/exclusive at the boundary second; empirically the last candle
  returned for `to=<midnight of the next day>` is the session of the previous
  trading day, and a candle is returned for every session whose start falls in
  `[from, to]`. The benchmark therefore uses a `to` of `end_date + 1 day` at
  00:00:00 UTC and additionally filters the result to sessions `date <= end_date`.

Response schema (`200`), **columnar**:

```json
{
  "t": [1773715500, ...],   // int64  — candle time, Unix timestamp (seconds)
  "o": [23.8, ...],         // double — open
  "h": [23.9, ...],         // double — high
  "l": [23.7, ...],         // double — low
  "c": [23.75, ...],        // double — close
  "v": [2530900, ...],      // int64  — volume
  "nextTime": 0             // int64  — cursor, see below
}
```

All seven fields are `required`.

- `nextTime`: *"Mốc thời gian (Unix Timestamp, giây) của cây nến tiếp theo khi
  dữ liệu hiện tại chưa đủ để đáp ứng yêu cầu. Giá trị 0 nếu không còn dữ
  liệu."* — i.e. when the response does not cover the whole requested range,
  `nextTime` is the timestamp to continue from; `0` means "no more data".
  This is the only pagination mechanism for OHLC.
- Errors: `400` → `{status, code, message}` (e.g. code `OA-003`,
  *"Thông tin nhập không hợp lệ"*); `500` → `{status, code, message}` (e.g.
  `OA-009`, *"Lỗi hệ thống"*).

### 5.2 `get_working_dates` — trading calendar

- SDK signature: `get_working_dates(dry_run=False)`
- Endpoint: `GET /market/working-dates`
- Response `200`: `{"workingDates": ["2026-04-16", "2026-04-17", ...]}` — array of
  `date`-format strings.
- Documented scope: *"danh sách ngày làm việc (ngày giao dịch) của thị trường
  chứng khoán **trong vòng 1 năm tính từ ngày hiện tại**, đã loại trừ các ngày
  nghỉ lễ và cuối tuần."*

> **Verified live behaviour (2026-10-20):** the response contained **258**
> dates spanning **2026-10-20 → 2027-10-20**, i.e. the window runs **forward**
> from today, not backward. `min == today`, `max == today + 1 year`, and the
> array is sorted ascending.
>
> **Consequence for this benchmark:** `/market/working-dates` **cannot** serve
> as the historical session calendar for a back-test window. The benchmark
> therefore derives the historical calendar from the trading data itself and
> *validates the derivation method* against `/market/working-dates` over the
> forward year where the official calendar is available (see
> `docs/research/yahoo_dnse_benchmark_design.md`).

### 5.3 `get_instruments` — instrument metadata

- SDK signature:
  `get_instruments(symbol=None, market_id=None, security_group_id=None, index_name=None, limit=None, page=None, version=None, dry_run=False)`
- Endpoint: `GET /market/instruments`
- Query parameters:

| Name | Description | Allowed values | Example |
|---|---|---|---|
| `symbol` | Danh sách mã chứng khoán (comma separated) | — | `SSI,SHS,ACB,AAA,VGI` |
| `marketId` | Mã thị trường niêm yết | `STO` (HOSE) · `STX` (HNX) · `UPX` (UPCOM) · `DVX` (phái sinh) · `HCX` (trái phiếu DN) | `STO` |
| `securityGroupId` | Nhóm chứng khoán | `ST` (cổ phiếu) · `EF` (ETF) · `EW` (chứng quyền) · `FU` (futures) · `BS` (trái phiếu) | `ST` |
| `indexName` | Chỉ số thị trường | `VN30` (top 30 HOSE) · `VN100` (top 100 HOSE) · `HNX30` (top 30 HNX) | `VN30` |
| `limit` | Số bản ghi mỗi trang | — | `100` |
| `page` | Trang hiện tại (bắt đầu từ 1) | — | `1` |

- Response `200`: `{ "data": [ ... ], "total": <int>, "page": <int>, "limit": <int> }`
- Each element of `data`:

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Mã chứng khoán |
| `marketId` | string | `STO` HOSE · `STX` HNX · `UPX` UPCOM · `DVX` phái sinh · `HCX` trái phiếu |
| `securityGroupId` | string | `ST` cổ phiếu · `EF` ETF · `EW` chứng quyền · `FU` futures · `BS` trái phiếu |
| `symbolType` | string | Phân loại mã phái sinh theo đáo hạn (derivative only) |
| `listedDate` | string (date) | Ngày niêm yết |
| `shortName` | string | Tên viết tắt của tổ chức phát hành |
| `name` | string | Tên đầy đủ của tổ chức phát hành |
| `indexName` | string[] | Danh sách chỉ số mà mã thuộc về |

- `indexName=VN30` is a **server-side index filter**, so it is a machine-readable
  index membership list, distinct from "the symbol exists on DNSE".

### 5.4 `get_security_definition` — per-board security definition

- SDK signature: `get_security_definition(symbol, board_id=None, dry_run=False)`
- Endpoint: `GET /price/{symbol}/secdef`
- Optional query `boardId`: `G1` (lô chẵn) · `G4` (lô lẻ) · `T1` (thỏa thuận
  trong giờ) · `T3` (thỏa thuận sau giờ) · `T4` (thỏa thuận lô lẻ trong giờ) ·
  `T6` (thỏa thuận lô lẻ sau giờ).
- Response `200`: **an array** (one entry per board), each entry:

| Field | Type | Description |
|---|---|---|
| `marketId` | string | `STO` HOSE · `STX` HNX · `UPX` UPCOM · `DVX` phái sinh · `HCX` trái phiếu |
| `boardId` | string | mã bảng giao dịch (see above) |
| `id` | string | Mã định danh quốc tế (ISIN), e.g. `VN000000HPG4` |
| `symbol` | string | Mã chứng khoán |
| `marketType` | string | `STO`/`STX`/`UPX`/`FBX`/`FIO`/`HCX` |
| `securityGroup` | string | `ST` cổ phiếu · `EF` ETF · `EW` chứng quyền · `FU` futures · `BS` trái phiếu |
| `basicPrice` | double | Giá tham chiếu ngày giao dịch |
| `ceilingPrice` | double | Giá trần ngày giao dịch |
| `floorPrice` | double | Giá sàn ngày giao dịch |
| `tradingStatus` | string | `HALT` / `NO_HALT` |
| `adminStatus` | string | `CR`, `CTR`, `NRM`, `RES`, `WFR`, `WID`, `WOV` |
| `tradingStatusType` | string | `NRM`, `NWE`, `NWN`, `SLS`, `SNE` |
| `tradingStatusReason` | string | `NRM`, `SUS`, `DTL`, `TFR` |
| `lastTradingDate` | string\|null | Ngày giao dịch cuối cùng (nếu có) |
| `listedDate` | string (date-time) | Ngày niêm yết |
| `recordedAt` | string | Thời gian ghi nhận — `YYYY-MM-DD HH:mm:ss.SSS` **(GMT+7)** |

The `GMT+7` annotation on `recordedAt` is the only explicit timezone statement
found in the market-data schemas.

### 5.5 `get_close_price` — closing price of the session

- SDK signature: `get_close_price(symbol, board_id=None, dry_run=False)`
- Endpoint: `GET /price/{symbol}/close`
- Response `200`: an array of
  `{marketId, boardId, id, symbol, closePrice (double), recordedAt (string, GMT+7)}`.
- This is the **latest** session's close price, not a history. It is used here
  only as a cross-check of the last daily candle.

### 5.6 Corporate actions

- The developer portal documents **`GET /market/corporate-actions`**
  (*"Truy vấn danh sách sự kiện quyền sắp / đang diễn ra của các mã chứng
  khoán trên thị trường."*).
- **This endpoint is not present in the SDK.** The SDK's only corporate-action
  method is `get_corporate_action_history(account_no, symbol=None, ca_type=None,
  ca_status=None, page_index=None, page_size=None, dry_run=False)` →
  `GET /accounts/{accountNo}/corporate-action-history`, which is a **trading-API**
  endpoint: it is scoped to a DNSE trading account and therefore needs an
  account number (and, by extension, account access), and it is about *events
  affecting the holder*, not about a market-wide adjusted-price series.
- The published spec files in the SDK repository (`2026-07-23`, `2026-09-10`,
  `2026-09-15`) contain **no** `/market/corporate-actions` path.

**Consequence:** DNSE market data exposes **raw (unadjusted) OHLC only**. There
is no market-wide adjusted-price series and no market-wide corporate-action
feed that the benchmark can read with market-data credentials alone. See
[§10](#10-not-verified--still-open).

### 5.7 `get_trades` — the trade feed (used as a third source)

- SDK signature:
  `get_trades(symbol, board_id=None, from_date=None, to_date=None, limit=None, order=None, next_page_token=None, dry_run=False)`
- Endpoint: `GET /price/{symbol}/trades`
- Query parameters (from the SDK client): `boardId`, `from`, `to`, `limit`,
  `order`, `nextPageToken`.
- Response `200`: `{"trades": [...], "nextPageToken": "<base64 cursor>"}`.
- Each element of `trades` carries: `marketId`, `boardId`, `isin`, `symbol`,
  `matchPrice`, `matchQtty`, `side`, `avgPrice`, `totalVolumeTraded`,
  `grossTradeAmount`, `highestPrice`, `lowestPrice`, `openPrice`, `time`.

Verified live on 2026-10-20 (Production):

| Field | Observation |
|---|---|
| `matchPrice` | The trade's price, in **thousand dong** (the same unit as the daily bar). |
| `totalVolumeTraded` | The **running** volume on that board, in units of **ten shares**: for every session checked, the daily bar's volume is exactly `totalVolumeTraded × 10`. This factor is measured, not documented. |
| `time` | `YYYY-MM-DD HH:MM:SS.mmm`, in GMT+7. The last trade of a session is the closing auction, at 14:45. |
| `highestPrice` / `lowestPrice` / `openPrice` | **Not usable as the session's high, low and open.** On some sessions the reported "high" is below the reported "low" (for example CTG on 2026-10-19: high 30 000, low 30 650), so their meaning is not established. The benchmark does not use them. |
| Boards | The feed mixes the boards (`G1`, `G4`, `T1`, `T3`, `T4`, `T6`) and each board reports its own running total, so one page cannot be reconciled with a market-wide daily volume. |
| History depth | The feed returns **no trades** for sessions older than a few months (probed 2025-10-20 and 2026-02-13: empty; 2026-06-19 and 2026-10-19: populated). |

The benchmark uses this feed as an **auxiliary** third source only, and reports
it with that caveat rather than treating it as ground truth.

---

## 6. Pagination and range limits

| Endpoint | Mechanism | Verified limit |
|---|---|---|
| `/price/ohlc` | `nextTime` cursor; `0` = no more data | none documented; a request from 2000-01-01 to 2026-10-21 for `VIC` returned **3 639** daily candles (2012-03-20 → 2026-10-19) with `nextTime = 0` |
| `/market/instruments` | `page` + `limit` | `limit` example 100; `total`/`page`/`limit` returned in the body |
| `/price/{symbol}/trades`, `/quotes`, `/foreign-trading`, `/expected-price` | `limit`, `order`, `nextPageToken` | not used by this benchmark |
| `/market/working-dates` | none | fixed 1-year window (forward, see §5.2) |

Rate-limit headers are returned (`X-Ratelimit-Limit`, `X-Ratelimit-Remaining`,
`X-Ratelimit-Reset`). The spec's *example* values are `Limit: 1000` and
`Limit: 10000` on different endpoints, with `Remaining` one less — these are
illustrative examples in the spec, **not a published quota**. No explicit
requests-per-minute figure was found in the documentation.

## 7. Verified live responses (Production, 2026-10-20)

Read with the official SDK against `https://openapi.dnse.com.vn`,
`api_version=2026-07-23`.

### 7.1 `GET /market/working-dates` → `200`

```
{"workingDates": ["2026-10-20", "2026-10-21", ... "2027-10-18", "2027-10-19", "2027-10-20"]}
```
258 entries, min `2026-10-20`, max `2027-10-20`.

### 7.2 `GET /price/ohlc?symbol=VIC&type=STOCK&resolution=1D&from=<2000-01-01>&to=<2026-10-21>` → `200`

- 3 639 candles, `nextTime = 0`.
- Earliest candle `2012-03-20`, latest `2026-10-19`.
- Last five candles (`t` → ICT date, o/h/l/c as returned, v):

| ICT date | o | h | l | c | v |
|---|---|---|---|---|---|
| 2026-10-19 | 219 | 219 | 207 | 207 | 2 096 600 |
| 2026-10-16 | 218.3 | 219.5 | 214.5 | 215 | 2 419 600 |
| 2026-10-15 | 218.4 | 220 | 216.4 | 218 | 4 348 600 |
| 2026-10-14 | 222 | 222 | 217.3 | 218.4 | 3 116 100 |
| 2026-10-13 | 218 | 224 | 215.3 | 222 | 3 133 800 |

### 7.3 Units — established by cross-check against Yahoo Finance

The specification does **not** state the price unit or the volume unit. They
were established empirically by comparing the DNSE response with the Yahoo
Finance daily series for the same symbol and the same sessions:

| Session | DNSE (as returned) | Yahoo `VIC.VN` | Ratio |
|---|---|---|---|
| 2026-10-13 | o 218, h 224, l 215.3, c 222, v 3 133 800 | 218 000 / 224 000 / 215 300 / 222 000, v 3 133 800 | price **×1000**, volume **×1** |
| 2026-10-14 | 222 / 222 / 217.3 / 218.4, v 3 116 100 | 222 000 / 222 000 / 217 300 / 218 400, v 3 116 100 | ×1000, ×1 |
| 2026-10-15 | 218.4 / 220 / 216.4 / 218, v 4 348 600 | 218 400 / 220 000 / 216 400 / 218 000, v 4 348 600 | ×1000, ×1 |
| 2026-10-16 | 218.3 / 219.5 / 214.5 / 215, v 2 419 600 | 218 300 / 219 500 / 214 500 / 215 000, v 2 419 600 | ×1000, ×1 |
| 2026-10-19 | 219 / 219 / 207 / 207, v 2 096 600 | 219 000 / 219 000 / 207 000 / 207 000, v 2 096 600 | ×1000, ×1 |

**Established units:**

- **Price unit: thousand VND (nghìn đồng).** DNSE returns e.g. `218.4` for a
  stock that trades at `218 400 VND`. Normalisation to VND is **×1000**.
  Granularity is 0.1 thousand = 100 VND, consistent with the Vietnamese tick
  grid.
- **Volume unit: shares (cổ phiếu).** The integers returned match Yahoo's
  `Volume` exactly for the same session.
- **Currency: VND.** The API returns no currency field; VND is inferred from the
  price/volume match with Yahoo's VND-quoted `.VN` series and from the fact
  that DNSE only quotes Vietnamese listings.

**Floating-point caveat (measured):** multiplying a thousand-VND float by 1000
does not always land on an exact đồng. Example — DNSE `c = 130.7` becomes
`130699.99999999999` after `×1000`, and `c = 256.1` becomes
`256100.00000000003`. The benchmark therefore rounds the normalised price to the
nearest đồng (integer) and compares with an explicit absolute tolerance rather
than on raw floats.

## 8. Candle timestamp semantics

- `t` is a Unix timestamp in **seconds**. The spec says only *"Unix Timestamp
  (giây)"*; it does not state a timezone.
- Measured on `VIC` `1D` candles (3 639 rows): the time-of-day component is
  **not constant**:

| Period | UTC hour of `t` | ICT time of `t` |
|---|---|---|
| 2012-03-20 → 2021-07-14 | 00:00 | 07:00 |
| 2021-07-15 onwards (with sporadic one-day regressions back to 00:00 UTC) | 02:00 | 09:00 |
| a handful of individual sessions across 2021-07, 2022-12, 2023-04, 2023-09, 2023-10, 2023-11 … | 00:00 | 07:00 |

- Both 07:00 ICT and 09:00 ICT fall on the **same calendar day** in
  `Asia/Ho_Chi_Minh` (UTC+7, no DST), so the trading date is unambiguous: the
  ICT calendar date of `t` is the session date. The benchmark converts `t` to
  `Asia/Ho_Chi_Minh` and takes the date, and does **not** assume a fixed
  time-of-day.
- 09:00 ICT is the HOSE session open; 07:00 ICT is not. The switch from 07:00 to
  09:00 around 2021-07-15 is consistent with a change in the vendor's daily
  bar stamping, not with a market change.

## 9. Historical depth, and what a "complete" session set looks like

- `GET /price/ohlc` for `VIC` at `resolution=1D` returned **3 639** daily candles
  from **2012-03-20** with `nextTime = 0` for a request window that started in
  2000. History is therefore **not** limited to one year, and 2 years of daily
  history is comfortably available.
- The number of daily candles returned for a full year (2025-10-01 → 2026-10-19)
  was **261**, i.e. one row per actual trading session — DNSE does **not** emit
  filler rows for non-trading days, in contrast to Yahoo Finance (see
  `docs/research/yahoo_dnse_benchmark_design.md`).
- The last session DNSE had for `VIC` at the time of the run was **2026-10-19**;
  the session of 2026-10-20 (the run day) was **not** yet present, while Yahoo
  already carried a partial 2026-10-20 bar. The benchmark therefore treats the
  current session as incomplete and excludes it from the comparison window.

## 10. Not verified / still open

These are recorded rather than guessed, and each one bounds the conclusions of
the benchmark.

1. **`/market/corporate-actions` parameters and schema.** Documented on the
   portal, absent from the SDK and from all three published spec files. Its
   request parameters, response fields and whether it can be queried with
   market-data-only credentials are **unverified**. Consequence: the benchmark
   **cannot** verify Yahoo's adjusted-price semantics against a DNSE adjusted
   series, and reports that as "not comparable" rather than as a pass/fail.
2. **Explicit rate-limit quotas.** Only illustrative header examples were found
   (`X-Ratelimit-Limit: 1000` / `10000` in the spec examples). No published
   requests-per-minute figure. The benchmark therefore uses a conservative
   self-imposed rate and records the *observed* `X-Ratelimit-*` headers.
3. **`from`/`to` boundary inclusivity** for `/price/ohlc` is not stated in the
   spec. The benchmark filters the returned candles to `date <= end_date` so the
   boundary cannot bias the result either way.
4. **Explicit statement of the price/volume unit** is absent from the
   documentation. The units in §7.3 are established by measurement, not by a
   documented statement.
5. **`X-Aux-Date` vs `Date`** header naming discrepancy between the spec and the
   SDK (see §4). The benchmark uses the SDK default, which the live Production
   API accepted.
6. **Whether the market-data endpoints need a permission scope distinct from a
   trading scope.** The README states *"an application that only consumes market
   data does not need trading permissions"*, and the Production key used by this
   benchmark is able to read market data. No separate market-data scope name was
   found in the documentation.
7. **Sandbox data quality was not tested** and is not used as evidence: the
   benchmark runs against Production only.
8. **`GET /market/trading-session`** (`get_lastest_session`) and the
   WebSocket market-data subscriptions were read in the docs but are not used by
   this benchmark (they describe the *current* session, which is out of scope for
   a daily-history comparison).
9. **Whether the daily bar's prices are the market's traded prices.** The daily
   bar agrees with the trade feed's closing price and volume for the most
   recent sessions, but for some symbols and older sessions it does not, and
   the corporate-action history that would explain the difference is not
   readable with market-data credentials. See the benchmark report.
10. **The meaning of the trade feed's `openPrice`, `highestPrice` and
    `lowestPrice` fields.** They are not the session's open, high and low (on
    some sessions the reported "high" is below the "low"), and the
    documentation does not describe them. The benchmark does not use them.

---

## 11. Sources of the DNSE-side data used in the report

- Production API `https://openapi.dnse.com.vn`, API version `2026-07-23`.
- Official SDK `dnse-sdk-openapi==2.4.1`.
- No DNSE trading, order, account, balance, position or personal endpoint was
  called. Only the read-only market-data endpoints listed in §5 were used.
- Raw DNSE responses are **not** committed to the repository. A small,
  credential-free, reduced sample (one symbol, a handful of sessions) is kept
  under `tests/market_data_benchmark/fixtures/dnse/` for unit tests, and
  `tests/fixtures/dnse/` holds the documented response schemas.
