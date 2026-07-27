# MT5 Bridge Setup (Wine-hosted, Linux VPS)

Operator runbook for installing the MT5 data-provider bridge on the real
VPS. **None of this is verifiable from a sandboxed dev session** — it
needs a real Linux server with root/sudo access, real MT5 broker
credentials, and enough time for Wine + a Windows Python + the MT5
terminal to actually install and log in. Follow it as a checklist.

## Why this exists

MetaTrader5's official Python package only works on Windows — it's a
thin wrapper over a DLL that talks to a real, running MT5 terminal on
the *same* machine. There is no official cross-platform network API
(unlike cTrader's real Protobuf-over-TCP protocol, already integrated in
`execution/ctrader_client.py`). This setup runs a real Windows Python +
MT5 terminal *under Wine*, on the same Linux VPS IATIS already runs on,
fronted by a small localhost-only HTTP bridge
(`scripts/mt5_bridge/mt5_bridge_server.py`) that the normal Linux venv
talks to.

**This is an unofficial, community-pattern setup — not vendor-supported.**
Wine crashes or MT5-terminal updates can silently kill the bridge with no
vendor support path. Treat it as strictly less reliable than the cTrader
feed until you've watched it run stably for a while. It is deliberately
**not** wired into any live trading decision by default (see
`core/data_providers.py`'s `DEFAULT_CHAINS` comment) — you opt it in
explicitly, per asset class, once you trust it.

## 1. Install Wine + a Windows Python

```bash
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install -y wine wine32 wine64 winetricks

# A dedicated Wine prefix for this bridge, kept separate from any other
# Wine usage on the box:
export WINEPREFIX=/opt/iatis/mt5_wine
export WINEARCH=win64
wineboot --init

# Install a Windows Python build inside that prefix (winetricks has a
# python3 verb on many distros; if not, download the official Windows
# .exe installer from python.org and run it under `wine`):
winetricks python3   # or: wine python-3.x.x-amd64.exe /quiet InstallAllUsers=1 PrependPath=1
```

Verify: `wine python.exe --version` should print a real Windows Python
version.

## 2. Install the MT5 terminal + Python package under Wine

```bash
# Download your broker's MT5 terminal installer (a real .exe from your
# broker's website — not a generic MetaQuotes download, since login
# servers are broker-specific), then:
wine mt5setup.exe

# Once installed, launch the terminal at least once under Wine and log
# into your real account (interactively — MT5's own login UI):
wine "C:\Program Files\<broker>\MetaTrader 5\terminal64.exe"

# Then, still inside the WINEPREFIX from step 1:
wine python.exe -m pip install MetaTrader5
```

Leave the terminal logged in and running — the bridge connects to this
same running terminal process, it does not launch or log into MT5 itself.

## 3. Configure and run the bridge

```bash
cd /opt/iatis  # wherever this repo is checked out on the VPS

export MT5_BRIDGE_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(24))")
# Only needed if your broker's symbol names differ from IATIS's internal
# ones (very common — e.g. "EURUSD.a", "EURUSDm"). Optional; unmapped
# symbols pass through unchanged.
export MT5_SYMBOL_MAP='{"EURUSD": "EURUSD.a", "XAUUSD": "XAUUSD.a"}'

WINEPREFIX=/opt/iatis/mt5_wine wine python.exe scripts/mt5_bridge/mt5_bridge_server.py
```

It should print `MT5 initialized. Serving on http://127.0.0.1:18812
(token set)`. Leave this running (a plain terminal run is fine for the
first smoke test — use systemd for anything longer, see below).

**Never change `MT5_BRIDGE_HOST` away from `127.0.0.1`.** This bridge has
no rate limiting or hardening beyond the shared-secret header — it must
stay unreachable off-box.

## 4. Verify the bridge is really connected

From a separate terminal on the same VPS:

```bash
curl -s http://127.0.0.1:18812/health -H "X-Bridge-Token: $MT5_BRIDGE_TOKEN" | python3 -m json.tool
```

Expect `"connected": true` with real `terminal`/`account` objects — not
just an HTTP 200 (the port can be open with the terminal still
disconnected).

```bash
curl -s "http://127.0.0.1:18812/rates?symbol=EURUSD&timeframe=H1&count=10" \
  -H "X-Bridge-Token: $MT5_BRIDGE_TOKEN" | python3 -m json.tool
```

Expect 10 real, sane OHLC rows with recent, sequential timestamps.

## 5. Keep it alive with systemd

```ini
# /etc/systemd/system/iatis-mt5-bridge.service
[Unit]
Description=IATIS MT5 data bridge (Wine)
After=network.target

[Service]
Type=simple
User=iatis
Environment=WINEPREFIX=/opt/iatis/mt5_wine
Environment=MT5_BRIDGE_TOKEN=<same token as above>
Environment=MT5_SYMBOL_MAP={"EURUSD": "EURUSD.a"}
WorkingDirectory=/opt/iatis
ExecStart=/usr/bin/wine python.exe scripts/mt5_bridge/mt5_bridge_server.py
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now iatis-mt5-bridge
sudo systemctl status iatis-mt5-bridge
```

`Restart=on-failure` matters more here than for most services — a Wine
crash is a real, expected failure mode for this setup, not a hypothetical
one. Watch `journalctl -u iatis-mt5-bridge -f` for the first day or two.

## 6. Point IATIS at the bridge — data confirmation BEFORE any live opt-in

In the main repo's `.env` (the normal Linux venv, not the Wine one):

```
MT5_BRIDGE_URL=http://127.0.0.1:18812
MT5_BRIDGE_TOKEN=<same token as above>
```

Then, from the normal IATIS venv:

```bash
python3 -c "
from core.data_providers import _fetch_mt5
df = _fetch_mt5('EUR/USD', 'H1', 10)
print(df)
print(df.index[0].year)  # sanity: should be the real current year, not 1970
"
```

Run a real historical download for one symbol:

```bash
python3 -m scripts.download_mt5_history --probe EURUSD
```

**Only after both of the above look correct for real data**, consider
adding `"mt5"` to one `config.yaml` `data.provider_chains` entry — start
with a non-primary slot (e.g. appended after `ctrader`, not first), and
watch `core/data_confidence.py`'s cross-provider checks (`GET
/data-confidence`, already live) for a few cycles. A `MATERIAL`
divergence between `mt5` and `ctrader` on the same symbol is the first
real signal something's wrong with the symbol mapping or timeframe
handling — catch it there, before it ever reaches a live decision.

## Known fragility, stated plainly

- Wine has no vendor support contract with MetaQuotes or your broker.
  A Windows/MT5-terminal update can change UI flows Wine can't render,
  or break silently.
- If the MT5 terminal disconnects (broker maintenance, network blip,
  Wine hiccup) the bridge's `/rates` calls will start failing — by
  design, this surfaces as a normal `DataFetchError` on the Linux side
  and the provider chain falls through to whatever comes after `mt5` in
  that asset class's chain. It will NOT silently return stale/wrong data.
- If you ever see the terminal logged out, re-run step 2's login step
  interactively under Wine — there is no automated re-login built here.
