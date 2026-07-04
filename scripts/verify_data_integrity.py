#!/usr/bin/env python3
"""
scripts/verify_data_integrity.py
--------------------------------
Audits every historical CSV in the data directory and answers, with
evidence: "Is this data real? Is it complete? Does it cover the
expected timeframes and known market events?"

Checks (each reported individually with its rationale):

STRUCTURAL (CORRUPT if failed)
  S1  Required OHLCV columns present, index is tz-aware UTC datetime
  S2  No null values, no non-positive prices
  S3  No duplicate timestamps; strictly ascending index
  S4  OHLC coherence: high >= max(open, close), low <= min(open, close)

CONTINUITY / TIMEFRAME
  C1  Inferred bar interval matches H1 (the storage timeframe; H4/D1
      are built by resampling — only H1 needs to exist on disk)
  C2  Gap analysis with an asset-class-aware market-hours model:
      forex/metals/energy close Fri ~21:00 UTC -> Sun ~21:00 UTC;
      indices additionally close outside cash sessions; crypto is 24/7.
      Unexpected gaps are listed (count, largest).
  C3  Date-range coverage vs the filename's advertised span
      ({SYM}_H1_{N}y.csv -> expect ~N years of data)

REAL-VS-SYNTHETIC HEURISTICS (SUSPECT if failed)
  R1  Weekend bars: non-crypto instruments must NOT have Saturday bars
      or Sunday bars before ~20:00 UTC. Synthetic generators produce a
      uniform hourly grid; real FX feeds cannot.
  R2  Session volatility profile: real FX/metals show higher mean
      absolute H1 return during London+NY (07-20 UTC) than during the
      Asian session (00-06 UTC). A ratio near 1.0 indicates a generator
      with no session structure.
  R3  Fat tails: real H1 returns are leptokurtic (excess kurtosis
      typically >> 1). Gaussian synthetic data gives about 0.
  R4  Identical-bar runs: long runs of byte-identical OHLC rows
      indicate fill-forward corruption or a dead feed segment.

EVENT COVERAGE (informational, USD instruments)
  E1  NFP response: US Nonfarm Payrolls is released on the first Friday
      of the month at 13:30 UTC (12:30 UTC during US DST). If the data
      is real, mean |return| in the release hour across first Fridays
      should exceed the same hour on other Fridays. This verifies the
      dataset actually contains the market's reaction to scheduled
      high-impact events, without hardcoding any event outcomes.

Verdicts per file: OK | SUSPECT_SYNTHETIC | CORRUPT.
Exit code 1 if any file is CORRUPT (CI-friendly).

Usage:
    python3 scripts/verify_data_integrity.py --data-dir data
    python3 scripts/verify_data_integrity.py --data-dir data --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

# Asset-class map (kept local so the verifier has no config dependency).
ASSET_CLASS: dict[str, str] = {
    **{s: "forex" for s in (
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
        "NZDUSD", "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF",
    )},
    "XAUUSD": "metal", "XAGUSD": "metal", "USOIL": "energy",
    "US30": "index", "NAS100": "index", "SPX500": "index",
    "BTCUSD": "crypto", "ETHUSD": "crypto",
}

# Data-source fidelity notes (facts about the Yahoo tickers used by
# scripts/download_all_symbols.py — they affect backtest interpretation).
SOURCE_CAVEATS: dict[str, str] = {
    "XAUUSD": "GC=F gold FUTURES, not spot XAU/USD — basis differs",
    "XAGUSD": "SI=F silver FUTURES, not spot",
    "USOIL": "CL=F futures — contract rolls create price gaps that are "
             "real for the future but not for spot CFD pricing",
    "NAS100": "^IXIC Nasdaq COMPOSITE, not NDX/US Tech 100 — different index",
    "US30": "^DJI cash index — no overnight session",
    "SPX500": "^GSPC cash index — no overnight session",
}


@dataclass
class FileReport:
    file: str
    symbol: str
    asset_class: str
    verdict: str = "OK"
    bars: int = 0
    start: str = ""
    end: str = ""
    findings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def fail(self, level: str, msg: str) -> None:
        self.findings.append(f"[{level}] {msg}")
        order = {"OK": 0, "SUSPECT_SYNTHETIC": 1, "CORRUPT": 2}
        if order.get(level, 0) > order[self.verdict]:
            self.verdict = level

    def note(self, msg: str) -> None:
        self.findings.append(f"[INFO] {msg}")


def _symbol_from_name(path: Path) -> str:
    m = re.match(r"([A-Z0-9]+)_H1_", path.name)
    return m.group(1) if m else path.stem


def _expected_weekend_closed(ts: pd.Timestamp) -> bool:
    """True if a non-crypto market is expected CLOSED at this UTC time.

    Model: closed from Friday ~22:00 UTC to Sunday ~21:00 UTC (covers
    both 21:00/22:00 DST variants; callers add tolerance on duration).
    """
    wd, hour = ts.weekday(), ts.hour
    if wd == 5:                       # Saturday
        return True
    if wd == 4 and hour >= 22:        # late Friday
        return True
    if wd == 6 and hour < 21:         # Sunday before reopen
        return True
    return False


def verify_file(path: Path) -> FileReport:
    symbol = _symbol_from_name(path)
    ac = ASSET_CLASS.get(symbol, "unknown")
    rep = FileReport(file=path.name, symbol=symbol, asset_class=ac)

    if symbol in SOURCE_CAVEATS:
        rep.note(f"source caveat: {SOURCE_CAVEATS[symbol]}")

    # -- S1: structure ------------------------------------------------
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    except Exception as exc:
        rep.fail("CORRUPT", f"unreadable CSV: {exc}")
        return rep

    required = {"open", "high", "low", "close"}
    df.columns = [str(c).lower() for c in df.columns]
    if not required.issubset(set(df.columns)):
        rep.fail("CORRUPT",
                 f"missing columns: {sorted(required - set(df.columns))}")
        return rep
    if "volume" not in df.columns:
        rep.note("no volume column (acceptable for FX)")

    if not isinstance(df.index, pd.DatetimeIndex):
        rep.fail("CORRUPT", "index is not a datetime index")
        return rep
    if df.index.tz is None:
        rep.fail("SUSPECT_SYNTHETIC",
                 "index is timezone-naive — production loader expects UTC; "
                 "session/event checks assume UTC")
        df.index = df.index.tz_localize("UTC")

    rep.bars = len(df)
    rep.start, rep.end = str(df.index[0]), str(df.index[-1])
    if len(df) < 500:
        rep.fail("CORRUPT", f"only {len(df)} bars — below pipeline warmup")
        return rep

    # -- S2: nulls / prices -------------------------------------------
    nulls = int(df[["open", "high", "low", "close"]].isna().sum().sum())
    if nulls:
        rep.fail("CORRUPT", f"{nulls} null OHLC values")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        rep.fail("CORRUPT", "non-positive prices present")

    # -- S3: index integrity ------------------------------------------
    dupes = int(df.index.duplicated().sum())
    if dupes:
        rep.fail("CORRUPT", f"{dupes} duplicate timestamps")
    if not df.index.is_monotonic_increasing:
        rep.fail("CORRUPT", "timestamps not strictly ascending")

    # -- S4: OHLC coherence --------------------------------------------
    bad_high = int((df["high"] < df[["open", "close"]].max(axis=1)).sum())
    bad_low = int((df["low"] > df[["open", "close"]].min(axis=1)).sum())
    if bad_high or bad_low:
        rep.fail("CORRUPT",
                 f"OHLC incoherent: {bad_high} bars high<max(o,c), "
                 f"{bad_low} bars low>min(o,c)")

    if rep.verdict == "CORRUPT":
        return rep

    # -- C1: interval ---------------------------------------------------
    deltas = df.index.to_series().diff().dropna()
    mode_delta = deltas.mode().iloc[0]
    rep.metrics["bar_interval"] = str(mode_delta)
    if mode_delta != pd.Timedelta(hours=1):
        rep.fail("SUSPECT_SYNTHETIC",
                 f"dominant interval {mode_delta} != 1h — file claims H1")
    rep.note("timeframes: H1 on disk; H4/D1 are built by resampling in "
             "core/timeframe_sync (by design — no separate files needed)")

    # -- C2: gaps vs market-hours model ---------------------------------
    gaps = deltas[deltas > pd.Timedelta(hours=1)]
    unexpected: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timedelta]] = []
    for ts, d in gaps.items():
        prev = ts - d
        if ac == "crypto":
            unexpected.append((prev, ts, d))
        elif ac in ("metal", "energy"):
            # Sourced from CME futures (GC=F/SI=F/CL=F): Globex trades
            # Sun ~22:00 UTC – Fri ~21:00 UTC with a DAILY ~60-minute
            # maintenance break (21:00–23:00 UTC window across DST).
            # Expected gaps: the daily break (gap ≤ 3h starting
            # 20:00–23:00 UTC), the weekend closure, and single-day US
            # exchange holidays (≤ 4 days including adjacent weekend).
            gap_start_hour = (prev + pd.Timedelta(hours=1)).hour
            daily_break = (d <= pd.Timedelta(hours=3)
                           and 20 <= gap_start_hour <= 23)
            weekend = (_expected_weekend_closed(prev + pd.Timedelta(hours=1))
                       and d <= pd.Timedelta(hours=53))
            holiday = d <= pd.Timedelta(days=4)  # e.g. Memorial Day + wknd
            if not (daily_break or weekend or holiday):
                unexpected.append((prev, ts, d))
        elif ac == "forex":
            # weekend closure: gap starting late Fri, ending ~Sun 21-22 UTC
            if not (_expected_weekend_closed(prev + pd.Timedelta(hours=1))
                    and d <= pd.Timedelta(hours=53)):
                unexpected.append((prev, ts, d))
        else:  # index cash sessions: overnight/weekend gaps are expected
            if d > pd.Timedelta(days=4):
                unexpected.append((prev, ts, d))
    rep.metrics["gap_count_total"] = int(len(gaps))
    rep.metrics["gap_count_unexpected"] = len(unexpected)
    if unexpected:
        biggest = max(unexpected, key=lambda x: x[2])
        weeks = max((df.index[-1] - df.index[0]).days / 7.0, 1.0)
        per_week = len(unexpected) / weeks
        msg = (f"{len(unexpected)} unexpected gap(s); largest "
               f"{biggest[2]} ending {biggest[1]}")
        if per_week > 1.0:
            rep.fail("SUSPECT_SYNTHETIC", msg + " — too many for holidays")
        else:
            rep.note(msg + " (within holiday tolerance)")

    # completeness: actual bars vs theoretical tradeable hours
    span_hours = (df.index[-1] - df.index[0]).total_seconds() / 3600
    expected = span_hours if ac == "crypto" else span_hours * (120 / 168)
    completeness = 100.0 * len(df) / max(expected, 1)
    rep.metrics["completeness_pct"] = round(completeness, 1)
    if completeness < 85:
        rep.fail("SUSPECT_SYNTHETIC",
                 f"only {completeness:.0f}% of expected bars present")

    # -- C3: advertised span ---------------------------------------------
    m = re.search(r"_(\d+)y", path.name)
    if m:
        years = int(m.group(1))
        actual_years = span_hours / (24 * 365.25)
        rep.metrics["span_years"] = round(actual_years, 2)
        if actual_years < years * 0.9:
            rep.fail("SUSPECT_SYNTHETIC",
                     f"filename claims {years}y, data spans "
                     f"{actual_years:.1f}y")

    # -- R1: weekend bars (decisive real-vs-synthetic test) ---------------
    if ac not in ("crypto", "unknown"):
        saturday = int((df.index.weekday == 5).sum())
        sunday_early = int(((df.index.weekday == 6)
                            & (df.index.hour < 20)).sum())
        if saturday or sunday_early:
            rep.fail("SUSPECT_SYNTHETIC",
                     f"{saturday} Saturday bars + {sunday_early} early-"
                     f"Sunday bars — {ac} markets are closed then; real "
                     f"feeds cannot contain these")

    # -- R2: session volatility structure ---------------------------------
    ret = df["close"].pct_change().abs()
    if ac in ("forex", "metal"):
        asian = ret[(df.index.hour >= 0) & (df.index.hour <= 6)].mean()
        ldn_ny = ret[(df.index.hour >= 7) & (df.index.hour <= 20)].mean()
        if asian and asian > 0:
            ratio = float(ldn_ny / asian)
            rep.metrics["session_vol_ratio_ldnny_vs_asia"] = round(ratio, 2)
            if ratio < 1.15 and ac == "forex":
                rep.fail("SUSPECT_SYNTHETIC",
                         f"London+NY/Asian volatility ratio {ratio:.2f} — "
                         f"real forex shows session structure (>1.15)")
            elif ac == "metal":
                # Metals trade actively in Asia (physical demand); a low
                # ratio is NOT synthetic evidence. Informational only.
                rep.note(f"session vol ratio {ratio:.2f} (metals trade "
                         f"actively in Asia — not a synthetic indicator)")

    # -- R3: fat tails ------------------------------------------------------
    r = df["close"].pct_change().dropna()
    if len(r) > 500:
        exk = float(pd.Series(r).kurtosis())  # excess kurtosis
        rep.metrics["excess_kurtosis"] = round(exk, 2)
        if exk < 1.0:
            rep.fail("SUSPECT_SYNTHETIC",
                     f"excess kurtosis {exk:.2f} — real H1 returns are "
                     f"fat-tailed (>1); near 0 indicates Gaussian generator")

    # -- R4: identical-bar runs ---------------------------------------------
    same = (df[["open", "high", "low", "close"]].diff().abs().sum(axis=1)
            == 0)
    if same.any():
        run = int((same.groupby((~same).cumsum()).cumsum()).max())
        if run >= 24:
            rep.fail("SUSPECT_SYNTHETIC",
                     f"run of {run} identical bars — fill-forward "
                     f"corruption or dead feed segment")

    # -- E1: NFP event response (USD instruments) ----------------------------
    if "USD" in symbol and ac in ("forex", "metal"):
        fridays_mask = df.index.weekday == 4
        first_fri_mask = fridays_mask & (df.index.day <= 7)
        release_hour_mask = pd.Index(df.index.hour).isin((12, 13))
        nfp_idx = df.index[first_fri_mask & release_hour_mask]
        other_idx = df.index[fridays_mask & (df.index.day > 7)
                             & release_hour_mask]
        if len(nfp_idx) >= 8 and len(other_idx) >= 8:
            nfp_move = float(ret.reindex(nfp_idx).mean())
            base_move = float(ret.reindex(other_idx).mean())
            if base_move > 0:
                lift = nfp_move / base_move
                rep.metrics["nfp_hour_vol_lift"] = round(lift, 2)
                if ac == "metal" and lift >= 1.0:
                    rep.note(f"NFP release-hour volatility {lift:.2f}x "
                             f"normal Fridays — positive response present "
                             f"(metals respond less uniformly than FX; "
                             f"informational)")
                elif lift >= 1.3:
                    rep.note(
                        f"NFP response present: release-hour volatility "
                        f"{lift:.2f}x normal Fridays (n={len(nfp_idx)} "
                        f"first-Friday bars) — data contains real event "
                        f"reactions")
                else:
                    rep.fail("SUSPECT_SYNTHETIC",
                             f"no NFP volatility response (lift {lift:.2f}x)"
                             f" — real USD data reacts to first-Friday NFP")

    return rep


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    files = sorted(args.data_dir.glob("*_H1_*.csv"))
    if not files:
        print(f"No *_H1_*.csv files under {args.data_dir} — nothing to "
              f"verify. Run scripts/download_all_symbols.py first.")
        sys.exit(1)

    reports = [verify_file(p) for p in files]

    print(f"\n{'file':<30}{'class':<8}{'bars':>8}{'complete':>10}"
          f"{'verdict':>20}")
    print("-" * 78)
    for r in reports:
        comp = r.metrics.get("completeness_pct")
        print(f"{r.file:<30}{r.asset_class:<8}{r.bars:>8}"
              f"{(f'{comp:.0f}%' if comp is not None else 'n/a'):>10}"
              f"{r.verdict:>20}")
    print()
    for r in reports:
        if r.findings:
            print(f"-- {r.file} ({r.verdict})")
            for f in r.findings:
                print(f"   {f}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            [vars(r) for r in reports], indent=2, default=str))
        print(f"\nJSON report: {args.json}")

    corrupt = [r for r in reports if r.verdict == "CORRUPT"]
    suspect = [r for r in reports if r.verdict == "SUSPECT_SYNTHETIC"]
    print(f"\nSummary: {len(reports)} files | OK="
          f"{len(reports) - len(corrupt) - len(suspect)} "
          f"SUSPECT={len(suspect)} CORRUPT={len(corrupt)}")
    sys.exit(1 if corrupt else 0)


if __name__ == "__main__":
    main()
