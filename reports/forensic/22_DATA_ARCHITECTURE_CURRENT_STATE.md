# 22 — Data Architecture: Current State (Forensic Report)

**Requested by**: operator, ahead of building a Trusted Data Center /
`TrustedDataRegistry` wiring Mission Center to `storage/market_bars.py`'s
D1 warehouse instead of raw provider/CSV reads. Per the operator's own
instruction, this report is written *before* any code changes.

## Current state — what exists, per component

| Component | Real / exists | Role today |
|---|---|---|
| `storage/market_bars.py` | ✅ | D1-backed `market_bars` (raw bars) + `dataset_manifest` (per symbol+timeframe status/coverage/checksum) tables. `compute_manifest()` reuses `core.data_validator.validate_ohlcv` + `backtest.price_benchmark.completeness_score`. `is_ready()` reads back `status=="READY"`. **Its own docstring already states it is storage-layer-only and not wired into backtest/Mission Center — this was a planned, deliberate follow-up, not an oversight.** |
| `backtest/runner.py::find_symbol_csv`/`load_symbol_data` | ✅ | The **actual** loader every backtest/mission/walk-forward/robustness/validator path uses. 100% local filesystem (`data/{SYMBOL}_{TF}_*.csv`/`.parquet`), zero D1/`storage.market_bars` import. |
| `core/timeframe_sync.py` | ✅ | Downsample-only (coarser-from-finer). Explicitly guards against upsampling ("would fabricate bars that never existed"). H4/D1 are **always** derived from H1 inside the engine at run time — `find_symbol_csv` never even looks for an `H4`/`D1` file. |
| `backtest/price_benchmark.py::classify_gaps`/`completeness_score` | ✅ | Real, asset-class-aware (forex weekend / equity market-hours / futures-session) gap classification. Already the shared implementation `compute_manifest()` reuses. |
| `research/manifest.py::dataset_fingerprint` + `mission_runner.py::_compute_fingerprint` | ✅ | Computed **once per symbol per mission run** (not per trial), combining a SHA256 of the source CSV file + git state. Attached to every trial recorded for that symbol. This already gives Mission Center most of the "immutable dataset per mission" property the operator is asking for (items 20–22) — just fingerprinting a local CSV, not a D1 dataset. |
| `utils/provenance.py` | ✅ | A **different** mechanism — live-decision-pipeline provenance (git commit + config hash + per-timeframe DataFrame content hash), attached to `main.py` reports. Not used by, or related to, Mission Center/backtest. |
| `core/data_manager.py` | ✅ | Legacy provider-failover + local-cache layer, used only by `scripts/download_all_data.py`. Not on any backtest/mission code path. |
| "native vs derived" flag | ⚠️ partial | Exists only as a comment in `scripts/push_bars_to_d1.py` ("so a manifest reader can tell native from derived at a glance"). **Not an actual column anywhere.** |
| Pre-flight "is this dataset usable" check before launching a Mission | ❌ | `execution/routes/missions.py`/`experiments.py` validate a symbol only against the configured symbol *universe* (a name whitelist), never against file existence or D1 readiness. The first place a missing dataset surfaces is a `FileNotFoundError` raised deep inside the subprocess. |

## Root cause

`storage/market_bars.py` was built and shipped as a complete, tested,
D1-backed warehouse with real quality metrics — but the wiring step that
would make `backtest/runner.py` (and therefore every mission, walk-forward,
robustness, and validation run) actually read from it was explicitly
deferred, per the module's own docstring, to "a deliberate, separate
follow-up phase." That phase never happened. The result: **two parallel,
disconnected concepts of "the data"** — a real, quality-scored D1 warehouse
that nothing reads, and a local-CSV-glob loader with no readiness concept
at all that everything actually uses.

**Practical constraint this creates for any fix**: today, exactly **one**
symbol (EURUSD) has ever been pushed into the D1 warehouse via
`scripts/push_bars_to_d1.py` (per the `market_bars --status` output from
this session — 4/4 READY for EURUSD only). Every other symbol currently
used by Missions exists only as a local CSV. Live-observed Dukascopy
throughput this session (5752s + 12093s for one symbol's M15+H1 over a
0.1-year window, throttled by 429s) means backfilling the full symbol
universe into D1 is a real, multi-hour-to-multi-day undertaking, not
something to assume complete before Phase 1 ships.

## Current flow

```
Mission Center (POST /research/missions)
  -> validate symbol against _configured_symbol_universe() [name whitelist only]
  -> build argv, submit subprocess (mission_runner.py)
       -> _run_symbol(): load_symbol_data(symbol, data_dir, start, end)
            -> find_symbol_csv(): glob data/{SYMBOL}_{TF}_*.csv|.parquet
            -> FileNotFoundError if missing (first time anyone finds out)
       -> df loaded ONCE, reused across all trials for that symbol
       -> _compute_fingerprint(): SHA256 of the CSV file + git state,
          recorded once per symbol, attached to every trial row
  -> storage/market_bars.py's dataset_manifest/is_ready(): never consulted
```

## Target flow (Phase 1 scope — see companion plan)

```
Mission Center
  -> TrustedDataRegistry.get_dataset(symbol, timeframe)
       -> checks storage.market_bars.is_ready(symbol, timeframe)
       -> READY  -> load from D1 (storage.market_bars.load_bars), attach
                    real manifest metadata (provider, coverage, checksum,
                    native/derived) to the mission's provenance record
       -> not READY -> [Phase 1 policy — see companion plan's AskUserQuestion:
                    the warehouse only covers 1 symbol today, so a literal
                    hard DATASET_NOT_READY block would break every other
                    symbol immediately]
```

## Files to modify / add (companion plan has the authoritative list)

- New: `storage/data_registry.py` (`TrustedDataRegistry`, thin wrapper over
  `storage/market_bars.py`).
- New: `dataset_manifest.native`/`derived_from` columns (migration).
- Modify: `backtest/runner.py::load_symbol_data` (registry-aware loading
  path), `execution/routes/missions.py`/`experiments.py` (pre-flight
  readiness surface, not necessarily a hard block — policy TBD).
- Not touched this phase (per triage, see companion plan): Data Center
  UI/tab, CLI tooling (`--repair`, `--snapshot`), cross-provider
  quarantine, immutable-snapshot hashing beyond what `_compute_fingerprint`
  already does.

## Test plan

Covered in the companion Phase 1 plan section — hard-block tests (registry
never silently substitutes timeframe/upsamples), a READY-dataset loads via
the registry, a not-READY dataset's exact behavior per the confirmed
policy, native-flag correctness, and a regression test pinning
`load_symbol_data`'s pre-existing local-CSV behavior for every symbol not
yet in the warehouse.
