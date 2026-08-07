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
mvn clean package -DskipTests
```

**Read the source before running it against your account** — this is
the point where you decide whether you trust this project.

**Known build issue, confirmed live 2026-08-07**: the project's own
`pom.xml` ships with THREE competing SLF4J bindings on the classpath at
once (`slf4j-reload4j` — pulled in transitively by the real JForex SDK
jar, keep this one as a dependency; `slf4j-simple` — directly, redundantly
declared in `pom.xml` itself; `logback-classic` — pulled in by Spring
Boot's own default logging starter, and the one Spring Boot's
`LogbackLoggingSystem` actually requires to be active). SLF4J picks
non-deterministically among them, and if it doesn't pick Logback, the
app crashes on startup with `IllegalArgumentException: LoggerFactory is
not a Logback LoggerContext but Logback is on the classpath`. Fix, before
`mvn clean package`:
1. Delete the directly-declared `slf4j-simple` `<dependency>` block from
   `pom.xml` entirely.
2. Add an `<exclusions>` block to the `DDS2-jClient-JForex` `<dependency>`
   excluding `org.slf4j:slf4j-reload4j` **as a binding only** — the JForex
   SDK jar dependency itself must stay; only its transitive
   `slf4j-reload4j` binding needs excluding.

Confirm neither jar is present after building:
```bash
unzip -l target/dukascopy-api-websocket-1.0.war | grep -E "slf4j-simple|slf4j-reload4j"
```
Should print nothing. `logback-classic` and `reload4j` (the underlying
log4j-compatible library `slf4j-reload4j` still depends on, unrelated to
the *binding* conflict above) are expected to remain — only the
`slf4j-simple`/`slf4j-reload4j` **bindings** are the problem.

## 3. Configure credentials — NEVER in IATIS's own `.env`, NEVER in chat

The bridge takes its own Dukascopy login (username/password, demo or
real server) via a Spring Boot `application.properties` file — a
config file local to the bridge process, separate from IATIS's `.env`.
**Never paste these credentials into a chat, an issue, or a commit** —
if a JForex login/password ever appears anywhere outside this file,
rotate it the same day (same rule CLAUDE.md applies to every other
secret in this repo).

**The real property keys, confirmed live 2026-08-07 by reading the
project's own shipped `src/main/resources/application.properties` and
`DukasService.java` source directly** (an earlier version of this doc
guessed a flat `dukascopy.username`/`dukascopy.password`/`dukascopy.
server`/`rest.port` shape from a README summary — that was wrong and
produced a `java.net.MalformedURLException: url=null` crash; the real
keys are kebab-case and nested under `dukascopy.credential-*`):

```properties
# application.properties (bridge process only — not part of the IATIS repo)
server.port=7080
dukascopy.ws-server-port=7081
dukascopy.credential-jnlp=http://platform.dukascopy.com/demo/jforex.jnlp
dukascopy.credential-username=<your JForex login>
dukascopy.credential-password=<your JForex password>
dukascopy.subscription-instruments=EUR/USD,GBP/USD,USD/JPY,XAU/USD,XAG/USD,BTC/USD,ETH/USD
dukascopy.lifecycle-wait=60000
dukascopy.connection-wait=60000
```

For a real (non-demo) account, use the real-server JNLP URL Dukascopy
gives you instead of the demo one above. If a future version of the
project changes these keys again, pull the ground truth the same way —
`cat src/main/resources/application.properties` and `grep -n
credential-jnlp src/main/java/**/DukasService.java` in your own clone —
rather than trusting this doc or the upstream README blindly.

Place this file outside the IATIS repo (e.g. `/opt/iatis/dukas-api-secrets/`),
readable only by the service user (`chmod 600`).

## 4. Run the bridge, bound to localhost only

```bash
# Docker (only if your VPS is amd64 — the published
# ismailfer/dukascopy-api:latest image is amd64-only, confirmed via
# Docker Hub; on an ARM VPS (e.g. Oracle Cloud's ARM shapes) this will
# fail to run at all — skip straight to build-from-source below):
docker run --name dukascopy-api -d \
  -p 127.0.0.1:7080:7080 -p 127.0.0.1:7081:7081 \
  -v /opt/iatis/dukas-api-secrets/application.properties:/app/application.properties:ro \
  ismailfer/dukascopy-api:latest

# or, if built from source (works on any architecture — compiled
# bytecode, not a native binary). The real jar name confirmed live
# 2026-08-07 is dukascopy-api-websocket-1.0.war (a Spring Boot
# executable WAR, still run the normal `java -jar` way), NOT
# dukas-api.jar — build first with `mvn clean package -DskipTests`,
# then:
java -jar target/dukascopy-api-websocket-1.0.war --spring.config.location=/opt/iatis/dukas-api-secrets/application.properties
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

## 6. Keep it alive with systemd

A plain `java -jar ...` run in a terminal (or `nohup`'d) dies the moment
that shell session ends, gets killed, or the VPS reboots — fine for the
first smoke test, not for anything you actually depend on. Convert it to
a systemd unit, matching the MT5 bridge's own precedent
(`docs/MT5_BRIDGE_SETUP.md`):

```ini
# /etc/systemd/system/iatis-dukas-bridge.service
[Unit]
Description=IATIS Dukascopy JForex data bridge (dukas-api)
After=network.target

[Service]
Type=simple
User=iatis
WorkingDirectory=/opt/iatis/dukas-api
ExecStart=/usr/bin/java -jar target/dukascopy-api-websocket-1.0.war --spring.config.location=/opt/iatis/dukas-api-secrets/application.properties
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
```

Adjust `ExecStart`'s `java` path if `which java` on your VPS points
somewhere other than `/usr/bin/java`. First, kill any manually-started
`java -jar ...`/`nohup` process for this bridge (`pkill -u iatis -f
dukascopy-api-websocket` or find its PID via `jobs`/`ps aux | grep
dukascopy`) so systemd owns the process cleanly, not a duplicate:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now iatis-dukas-bridge
sudo systemctl status iatis-dukas-bridge
```

Re-run the section 5 verification curl to confirm the systemd-managed
process is the one actually serving requests. `journalctl -u
iatis-dukas-bridge -f` is the equivalent of tailing the old
`/tmp/dukas-bridge.log` file going forward.

## 7. Point IATIS at the bridge — data confirmation BEFORE any opt-in

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

## 8. Order execution (Phase 2b) — mandatory manual verification BEFORE enabling

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
