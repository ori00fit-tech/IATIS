# Dukascopy JForex Bridge Setup (dukas-api, Linux VPS)

Operator runbook for installing the Dukascopy JForex data-provider
bridge on the real VPS. **None of this is verifiable from a sandboxed
dev session** — this sandbox's network policy blocks outbound requests
to Dukascopy's servers, and there is no real (even demo) JForex account
credential here to test with. Follow it as a checklist.

## Why this exists, and what it is

Dukascopy's official programmatic access is the **JForex SDK**: a Java
library (`com.dukascopy.api.IClient`) that connects directly to
Dukascopy's servers and can run headless — no Windows/Wine hack needed
here, unlike MT5 (see `docs/MT5_BRIDGE_SETUP.md`), since the SDK itself
supports a plain JVM on Linux.

Rather than write and maintain a bespoke Java bridge from scratch, this
integration talks to a real, existing open-source project —
[`ismailfer/dukascopy-api-websocket`](https://github.com/ismailfer/dukascopy-api-websocket)
("dukas-api") — which already wraps the JForex SDK as a small local
REST + WebSocket service, with a published Docker image. IATIS's
`core/data_providers.py::_fetch_dukascopy_jforex` talks to its
`GET /api/v1/history` endpoint exactly the way it talks to the MT5
bridge's `/rates` endpoint — one more pull-based HTTP provider.

**This is UNOFFICIAL, THIRD-PARTY code — a stronger caveat than the MT5
Wine bridge's fragility warning.** It is not written or reviewed by
Dukascopy, and this session has not audited its source in depth (only
its documented API contract, verified against its README). **Read the
project's own source yourself before pointing even demo credentials at
it.** It is deliberately **not** wired into any live trading decision by
default (see `core/data_providers.py`'s `DEFAULT_CHAINS` comment) — you
opt it in explicitly, per asset class, once you trust it.

**Scope of this integration: DATA ONLY.** The bridge's own
`POST`/`PUT`/`DELETE /api/v1/position` endpoints could place real orders,
but nothing in IATIS calls them — `execution/trade_executor.py` is
untouched by this integration. Wiring real order execution through this
new, unaudited path is a separate, much higher-stakes decision that
needs its own dedicated safety design (dry-run gating, extensive
testing) — not something to build alongside a data-provider addition.

## 1. Install a JDK

```bash
sudo apt update
sudo apt install -y openjdk-17-jre-headless   # JDK 11+ required by dukas-api's README
java -version   # confirm a real JVM is present
```

## 2. Get dukas-api

Two options — pick one:

```bash
# Option A: the published Docker image (simplest)
docker pull ismailfer/dukascopy-api:latest

# Option B: build from source (lets you actually read the code first,
# which you should do regardless of which option you run it with)
git clone https://github.com/ismailfer/dukascopy-api-websocket.git /opt/iatis/dukas-api
cd /opt/iatis/dukas-api
mvn clean package
```

**Read the source before running it against your account** — this is
the point where you decide whether you trust this project.

## 3. Configure credentials — NEVER in IATIS's own `.env`, NEVER in chat

The bridge takes its own Dukascopy login (username/password, demo or
real server) via a Spring Boot `application.properties` file — a
config file local to the bridge process, separate from IATIS's `.env`.
**Never paste these credentials into a chat, an issue, or a commit** —
if a JForex login/password ever appears anywhere outside this file,
rotate it the same day (same rule CLAUDE.md applies to every other
secret in this repo).

```properties
# application.properties (bridge process only — not part of the IATIS repo)
dukascopy.username=<your JForex login>
dukascopy.password=<your JForex password>
dukascopy.server=demo
rest.port=7080
websocket.port=7081
```

Place this file outside the IATIS repo (e.g. `/opt/iatis/dukas-api-secrets/`),
readable only by the service user (`chmod 600`).

## 4. Run the bridge, bound to localhost only

```bash
# Docker:
docker run --name dukascopy-api -d \
  -p 127.0.0.1:7080:7080 -p 127.0.0.1:7081:7081 \
  -v /opt/iatis/dukas-api-secrets/application.properties:/app/application.properties:ro \
  ismailfer/dukascopy-api:latest

# or, if built from source:
java -jar target/dukas-api.jar --spring.config.location=/opt/iatis/dukas-api-secrets/application.properties
```

**Never publish these ports beyond `127.0.0.1`.** Unlike the MT5
bridge (which has a shared-secret header as defense-in-depth), this
project's README does not document any bridge-side authentication — the
loopback binding above (`127.0.0.1:PORT:PORT`, not `0.0.0.0`) is the
only thing standing between this process and the open internet. Treat
that binding as load-bearing, not optional.

## 5. Verify the bridge is really connected

**The real contract, confirmed 2026-08-07 against the operator's own live
bridge and `HistDataController.java` source — do NOT trust the
`dukas-api` README here, it documents a `count` param that does not
exist.** The real endpoint needs a slash-separated `instID` and a
required `from` (epoch-milliseconds); `from=0` makes the bridge default
to "last 5 days":

```bash
curl -s 'http://127.0.0.1:7080/api/v1/history?instID=EUR%2FUSD&timeFrame=15MIN&from=0&to=0' | python3 -m json.tool
```

Expect real, sane OHLC candles (`open`/`high`/`low`/`close`/`volume`/
`spread`/`timestamp`, with `timestamp` in **milliseconds**) with recent,
sequential timestamps — not an empty array or an error. If you get a
`500`, retry once (a transient 500 right after the bridge finishes its
JForex session handshake has been observed to clear on retry). If this
fails after a retry, check the bridge's own logs before touching
anything on the IATIS side.

## 6. Point IATIS at the bridge — data confirmation BEFORE any opt-in

In the main repo's `.env`:

```
DUKASCOPY_JFOREX_BRIDGE_URL=http://127.0.0.1:7080
```

Then, from the IATIS venv:

```bash
python3 -c "
from core.data_providers import _fetch_dukascopy_jforex
df = _fetch_dukascopy_jforex('EUR/USD', 'H1', 10)
print(df)
print(df.index[0].year)  # sanity: should be the real current year
"
```

**Only after this looks correct for real data**, consider adding
`"dukascopy_jforex"` to one `config.yaml` `data.provider_chains`
entry — start with a non-primary slot (e.g. appended after `ctrader`,
not first), and watch `core/data_confidence.py`'s cross-provider checks
(`GET /data-confidence`, already live) for a few cycles. A `MATERIAL`
divergence between `dukascopy_jforex` and `ctrader` on the same symbol
is the first real signal something's wrong with the symbol/timeframe
mapping — catch it there, before it ever reaches a live decision.

## 7. Order execution (Phase 2b) — mandatory manual verification BEFORE enabling

`execution/dukascopy_jforex_client.py` places real orders through the
bridge's `POST`/`PUT /api/v1/position` endpoints, gated by
`config.yaml`'s `execution.dukascopy_jforex_enabled` (**false by
default**) plus the standard `dry_run`/`broker: dukascopy_jforex`/
`allow_live_trading` gates already used for cTrader/OANDA.

**Two things in this client are best-documented assumptions, not
verified facts** (see the module's own docstring for the full
reasoning):

1. **Position sizing is a fixed quantity**
   (`execution.dukascopy_jforex_fixed_quantity`, `0.0` = refuse to
   trade) — the bridge documents no account-balance endpoint, so there
   is no live number to compute cTrader/OANDA-style `risk_pct × balance`
   sizing from. Set this to something deliberately tiny for your first
   test.
2. **Stop-loss/take-profit are converted to pips** using a standard FX
   pip-size table (`execution/dukascopy_jforex_client.py`'s
   `_PIP_SIZE`) — the bridge's `PUT /api/v1/position` documents
   `stopLossPips`/`takeProfitPips`, but no per-instrument pip-size
   convention is documented anywhere found. **`quantity`'s exact unit
   (lots vs. base-currency units) is also undocumented.**

**Before ever setting `dukascopy_jforex_enabled: true` and letting the
scheduler loop run unattended**, place exactly ONE real order manually
and inspect it:

```bash
python3 -c "
from execution.dukascopy_jforex_client import DukascopyJForexClient, DukascopyJForexOrder
client = DukascopyJForexClient()
order = DukascopyJForexOrder(
    symbol='EURUSD', direction='BUY', quantity=0.01,   # start tiny
    stop_loss=1.0700, take_profit=1.1000,               # pick real, sane demo-account levels
    client_order_id='IATIS_EURUSD_MANUAL_TEST',
)
result = client.place_market_order(order)
print(result)
"
```

Then, in the real JForex terminal UI (or the bridge's own logs):

- Confirm the position size is what you actually intended (`quantity`'s
  unit assumption).
- Confirm the stop-loss and take-profit are placed at approximately the
  right distance from entry (the pip-size assumption) — not 10x too
  close, not 10x too far.
- Only once both look correct, consider enabling
  `dukascopy_jforex_enabled` for real scheduler-loop use.

If either assumption is wrong, fix the specific piece
(`_PIP_SIZE[symbol]` or the `quantity` semantics in
`DukascopyJForexOrder`) before trusting this integration with anything
beyond that one manual test order.

## Known fragility, stated plainly

- This is unaudited, third-party code with no vendor support contract —
  weigh that against every other provider in this codebase, all of
  which are either official SDKs/protocols (cTrader, MT5's own Python
  package) or well-established commercial APIs.
- No documented bridge-side authentication beyond the operator's own
  loopback binding — do not expose these ports.
- If the bridge loses its JForex session, `/api/v1/history` calls will
  start failing — by design, this surfaces as a normal `DataFetchError`
  on the IATIS side and the provider chain falls through to whatever
  comes after `dukascopy_jforex` in that asset class's chain. It will
  NOT silently return stale/wrong data.
- `timeFrame` support is `1SEC | 10SEC | 1MIN | 5MIN | 10MIN | 15MIN |
  1HOUR | DAILY` (per the project's own README) — no native 30-minute or
  4-hour candle. IATIS's H4 requests will need to come from resampling
  elsewhere in the pipeline, same as any other provider without a
  native H4.
