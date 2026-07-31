"""
backtest/metrics.py
--------------------
Professional trading metrics calculation.

All metrics a quant fund would expect:
  Risk-adjusted returns: Sharpe, Sortino, Calmar, MAR
  Drawdown analysis: Max DD, Average DD, DD Duration
  Trade statistics: WR, PF, Expectancy, Avg RR, MFE, MAE
  Distribution: Monthly returns, Yearly returns, Regime performance
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any
import pandas as pd
import numpy as np


def json_safe(obj: object) -> object:
    """Recursively replaces bare inf/-inf/nan floats with JSON-standard
    string sentinels before serialization.

    profit_factor below is float('inf') by definition whenever a sample
    has zero losing trades (no denominator) — a real, correct value, not
    a bug. But json.dumps happily emits the bare token `Infinity` for it
    by default, which is NOT valid JSON: any strict JSON.parse() (every
    browser's fetch().json() included) throws on a report file containing
    one. Used by backtest/robustness.py and backtest/walk_forward.py,
    whose sweep/window payloads can carry this same profit_factor at
    arbitrary nesting depth; execution/api_shared_helpers.py's
    _forward_rule_progress() hit the identical failure mode for a single
    known field and sanitizes it inline the same way.
    """
    if isinstance(obj, float):
        if obj == float("inf"):
            return "Infinity"
        if obj == float("-inf"):
            return "-Infinity"
        if obj != obj:  # NaN != NaN is the standard way to detect it
            return "NaN"
        return obj
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj


@dataclass
class TradeRecord:
    """Single trade record with full context."""
    trade_id:     str
    symbol:       str
    direction:    str        # BUY / SELL
    entry_time:   pd.Timestamp
    exit_time:    pd.Timestamp | None
    entry_price:  float
    exit_price:   float | None
    stop_loss:    float
    take_profit:  float
    position_size: float
    pnl_usd:      float = 0.0
    pnl_pips:     float = 0.0
    rr_actual:    float = 0.0    # actual R:R achieved
    rr_planned:   float = 0.0    # planned R:R at entry
    mfe:          float = 0.0    # Maximum Favorable Excursion
    mae:          float = 0.0    # Maximum Adverse Excursion
    holding_bars: int = 0
    exit_reason:  str = ""       # TP / SL / TIMEOUT / MANUAL
    regime:       str = ""
    session:      str = ""
    cf_score:     float = 0.0    # confluence score at entry
    engine_votes: dict = field(default_factory=dict)
    is_win:       bool = False
    # Backtesting Lab Pro Phase D (2026-07-27) — per-indicator filter/
    # confirmation/score-weight involvement at entry, when an ad-hoc
    # indicator override was configured for this run (empty otherwise).
    indicator_filters: dict = field(default_factory=dict)
    # Feature Mining / Hypothesis Discovery Phase 1 (2026-07-30) —
    # flattened, analysis-ready decision-time context, populated in
    # backtest/runner.py's trade_to_record() from this trade's engine_votes
    # (already-nested raw dicts) plus the decision snapshot's mtf/
    # reversal_veto/mqs/atr_value/volatility/info_share/contradiction_reasons
    # objects (backtesting/backtest_engine.py). FLAT (not nested per engine,
    # unlike indicator_filters/context_filters, which are keyed by an
    # ad-hoc per-run-configured name set) because features are keyed by a
    # fixed, known-in-advance name set — backtest/feature_mining.py can
    # iterate feature names directly with no engine-specific unpacking. A
    # field is OMITTED (never fabricated) when its source gate was off for
    # this run. Empty for any TradeRecord built outside run_backtest()'s
    # loop, same "absent means not configured" convention as
    # indicator_filters.
    features: dict = field(default_factory=dict)


@dataclass
class BacktestMetrics:
    """Complete backtest metrics report."""
    # Basic
    total_trades:     int   = 0
    winning_trades:   int   = 0
    losing_trades:    int   = 0
    win_rate:         float = 0.0

    # P&L
    net_profit:       float = 0.0
    gross_profit:     float = 0.0
    gross_loss:       float = 0.0
    profit_factor:    float = 0.0
    expectancy:       float = 0.0     # avg $ per trade
    expectancy_r:     float = 0.0     # avg R per trade

    # Risk-adjusted
    sharpe_ratio:     float = 0.0
    sortino_ratio:    float = 0.0
    calmar_ratio:     float = 0.0
    mar_ratio:        float = 0.0     # == calmar_ratio here — see calculate_metrics()

    # Drawdown
    max_drawdown:     float = 0.0     # %
    max_drawdown_usd: float = 0.0
    avg_drawdown:     float = 0.0
    max_dd_duration:  int   = 0       # consecutive CLOSED TRADES underwater, not bars
                                       # (no per-bar series reaches this function)

    # Trade stats
    avg_rr:           float = 0.0
    std_rr:           float = 0.0     # sample std of R-multiples — the
                                       # mean_r/std_r/n input backtest/
                                       # multiple_testing.py's per-trial
                                       # significance test needs (AI
                                       # Research Lab Phase 1, 2026-07-27)
    avg_holding_bars: float = 0.0
    avg_win:          float = 0.0
    avg_loss:         float = 0.0
    largest_win:      float = 0.0
    largest_loss:     float = 0.0
    max_consecutive_wins:  int = 0
    max_consecutive_losses:int = 0

    # MFE / MAE
    avg_mfe:          float = 0.0
    avg_mae:          float = 0.0

    # Returns
    total_return_pct: float = 0.0
    annual_return:    float = 0.0
    monthly_returns:  dict  = field(default_factory=dict)
    yearly_returns:   dict  = field(default_factory=dict)

    # Exposure
    exposure_pct:     float = 0.0    # % time in market

    # AI Research Lab Phase 1 (2026-07-27) — standard risk/distribution
    # metrics beyond the original set, computed from data already
    # collected above (trade PnL, R-multiples, the equity/drawdown loop).
    sqn:              float = 0.0    # System Quality Number: mean(R)/std(R) * sqrt(n)
    recovery_factor:  float = 0.0    # net_profit / max_drawdown_usd
    ulcer_index:      float = 0.0    # sqrt(mean(drawdown_pct**2)) over per-trade drawdown
    kelly_criterion:  float = 0.0    # win_rate - (1-win_rate)/(avg_win/avg_loss), clamped [-1,1]
    var_95:           float = 0.0    # 5th percentile of trade pnl_usd (historical VaR)
    cvar_95:          float = 0.0    # mean pnl_usd of trades at/below var_95 (Expected Shortfall)
    skew:             float = 0.0    # sample skewness of trade pnl_usd
    kurtosis:         float = 0.0    # sample excess kurtosis of trade pnl_usd

    # By category
    by_direction:     dict  = field(default_factory=dict)
    by_session:       dict  = field(default_factory=dict)
    by_regime:        dict  = field(default_factory=dict)
    by_symbol:        dict  = field(default_factory=dict)
    by_engine:        dict  = field(default_factory=dict)
    # Edge Discovery (2026-07-31) — one flat pooled 3-way cross, keyed
    # "DIRECTION|REGIME|SESSION" (e.g. "BUY|RANGING|London"). by_direction/
    # by_session/by_regime above are three SEPARATE single-dimension
    # breakdowns — none tells you the direction of trades within a regime
    # bucket. All tree levels (1-way/2-way/3-way) can be derived from this
    # one field by grouping on key parts and summing (see
    # backtest/meta_analysis.py's pooling helpers).
    by_direction_regime_session: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    def summary(self) -> str:
        return (
            f"Trades: {self.total_trades} | "
            f"WR: {self.win_rate:.1f}% | "
            f"PF: {self.profit_factor:.2f} | "
            f"Sharpe: {self.sharpe_ratio:.2f} | "
            f"MaxDD: {self.max_drawdown:.1f}% | "
            f"Net: ${self.net_profit:,.0f}"
        )


def calculate_metrics(
    trades: list[TradeRecord],
    initial_capital: float = 10_000.0,
    risk_free_rate: float = 0.04,   # 4% annual
    bars_per_year: int = 8760,       # H1 bars
) -> BacktestMetrics:
    """
    Calculate comprehensive backtest metrics from trade list.

    Args:
        trades: list of TradeRecord
        initial_capital: starting capital
        risk_free_rate: annual risk-free rate for Sharpe
        bars_per_year: number of bars in a year (8760 for H1, 252 for daily)

    Returns:
        BacktestMetrics with all statistics
    """
    m = BacktestMetrics()

    if not trades:
        return m

    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        return m

    m.total_trades    = len(closed)
    m.winning_trades  = sum(1 for t in closed if t.is_win)
    m.losing_trades   = m.total_trades - m.winning_trades
    m.win_rate        = m.winning_trades / m.total_trades * 100

    # P&L
    pnls = [t.pnl_usd for t in closed]
    m.net_profit   = sum(pnls)
    m.gross_profit = sum(p for p in pnls if p > 0)
    m.gross_loss   = abs(sum(p for p in pnls if p < 0))
    m.profit_factor = (m.gross_profit / m.gross_loss) if m.gross_loss > 0 else float("inf")
    m.expectancy   = m.net_profit / m.total_trades
    m.avg_win      = m.gross_profit / m.winning_trades if m.winning_trades > 0 else 0
    m.avg_loss     = m.gross_loss / m.losing_trades if m.losing_trades > 0 else 0
    m.largest_win  = max(pnls)
    m.largest_loss = min(pnls)

    # Expectancy in R
    rrs = [t.rr_actual for t in closed if t.rr_actual != 0]
    m.avg_rr = sum(rrs) / len(rrs) if rrs else 0
    if len(rrs) >= 2:
        m.std_rr = float(np.std(np.array(rrs), ddof=1))
    win_rrs  = [t.rr_actual for t in closed if t.is_win and t.rr_actual > 0]
    loss_rrs = [abs(t.rr_actual) for t in closed if not t.is_win and t.rr_actual != 0]
    wr = m.win_rate / 100
    avg_win_r  = sum(win_rrs) / len(win_rrs) if win_rrs else 0
    avg_loss_r = sum(loss_rrs) / len(loss_rrs) if loss_rrs else 1
    m.expectancy_r = wr * avg_win_r - (1 - wr) * avg_loss_r

    # MFE / MAE
    m.avg_mfe = sum(t.mfe for t in closed) / len(closed)
    m.avg_mae = sum(t.mae for t in closed) / len(closed)

    # Holding time
    m.avg_holding_bars = sum(t.holding_bars for t in closed) / len(closed)

    # Equity curve
    equity = [initial_capital]
    peak   = initial_capital
    dds    = []
    dd_starts = []

    for t in closed:
        equity.append(equity[-1] + t.pnl_usd)
        if equity[-1] > peak:
            peak = equity[-1]
        dd = (peak - equity[-1]) / peak * 100
        dds.append(dd)

    m.max_drawdown     = max(dds) if dds else 0
    m.max_drawdown_usd = m.max_drawdown / 100 * initial_capital
    m.avg_drawdown     = sum(dds) / len(dds) if dds else 0
    m.total_return_pct = (equity[-1] - initial_capital) / initial_capital * 100

    # Ulcer Index: RMS of the per-trade drawdown series (equity[1:] vs.
    # running peak) — already computed above as `dds`.
    if dds:
        m.ulcer_index = math.sqrt(sum(d * d for d in dds) / len(dds))

    # max_dd_duration: longest run of consecutive closed trades where
    # equity sits below the running peak (honest "consecutive trades
    # underwater" definition — see the dataclass field's own comment).
    cur_underwater = max_underwater = 0
    peak_run = equity[0]
    for e in equity[1:]:
        if e > peak_run:
            peak_run = e
        if e < peak_run:
            cur_underwater += 1
            max_underwater = max(max_underwater, cur_underwater)
        else:
            cur_underwater = 0
    m.max_dd_duration = max_underwater

    # Recovery Factor: net profit relative to the worst drawdown suffered.
    if m.max_drawdown_usd > 0:
        m.recovery_factor = m.net_profit / m.max_drawdown_usd

    # exposure_pct: real elapsed time in market vs. the trading window's
    # real elapsed time, from entry_time/exit_time timestamps already on
    # each TradeRecord — decoupled from max_dd_duration's trade-count
    # definition on purpose (this one needs to be wall-clock true).
    timed = [t for t in closed if t.entry_time and t.exit_time]
    if timed:
        in_market = sum(
            (t.exit_time - t.entry_time).total_seconds() for t in timed
        )
        span_start = min(t.entry_time for t in timed)
        span_end = max(t.exit_time for t in timed)
        span_seconds = (span_end - span_start).total_seconds()
        if span_seconds > 0:
            m.exposure_pct = min(100.0, in_market / span_seconds * 100)

    # Consecutive wins/losses
    max_cw = max_cl = cur_cw = cur_cl = 0
    for t in closed:
        if t.is_win:
            cur_cw += 1; cur_cl = 0
            max_cw = max(max_cw, cur_cw)
        else:
            cur_cl += 1; cur_cw = 0
            max_cl = max(max_cl, cur_cl)
    m.max_consecutive_wins   = max_cw
    m.max_consecutive_losses = max_cl

    # Sharpe / Sortino
    if len(pnls) >= 2:
        pnl_arr = np.array(pnls)
        avg_pnl = np.mean(pnl_arr)
        std_pnl = np.std(pnl_arr, ddof=1)
        rf_per_trade = risk_free_rate / bars_per_year * m.avg_holding_bars

        if std_pnl > 0:
            m.sharpe_ratio = (avg_pnl - rf_per_trade * initial_capital) / std_pnl * math.sqrt(m.total_trades)

        neg_pnls = pnl_arr[pnl_arr < 0]
        downside_std = np.std(neg_pnls, ddof=1) if len(neg_pnls) >= 2 else std_pnl
        if downside_std > 0:
            m.sortino_ratio = (avg_pnl - rf_per_trade * initial_capital) / downside_std * math.sqrt(m.total_trades)

    # Calmar
    if m.max_drawdown > 0:
        m.calmar_ratio = m.total_return_pct / m.max_drawdown
    # MAR ratio is conventionally calmar computed over annualized return —
    # this function has no separate multi-year windowing to make it
    # distinct from calmar_ratio here, so it's set equal rather than
    # fabricated. See the dataclass field's own comment.
    m.mar_ratio = m.calmar_ratio

    # System Quality Number (Van Tharp): mean(R)/std(R) * sqrt(n), using
    # the R-multiples already gathered above for avg_rr.
    if len(rrs) >= 2:
        rr_arr = np.array(rrs)
        rr_std = np.std(rr_arr, ddof=1)
        if rr_std > 0:
            m.sqn = float(np.mean(rr_arr) / rr_std * math.sqrt(len(rrs)))

    # Kelly criterion, clamped to [-1, 1] — undefined/pathological inputs
    # (all-wins avg_loss==0, all-losses avg_win==0) must never divide by
    # zero or blow past a sane range.
    if m.avg_win > 0 and m.avg_loss > 0:
        b = m.avg_win / m.avg_loss
        raw_kelly = wr - (1 - wr) / b
        m.kelly_criterion = max(-1.0, min(1.0, raw_kelly))

    # VaR / CVaR (historical, 95%) over the trade PnL distribution.
    if len(pnls) >= 2:
        pnl_arr_sorted = np.sort(np.array(pnls))
        m.var_95 = float(np.percentile(pnl_arr_sorted, 5))
        tail = pnl_arr_sorted[pnl_arr_sorted <= m.var_95]
        m.cvar_95 = float(np.mean(tail)) if len(tail) > 0 else m.var_95

        # Skew / excess kurtosis of the trade PnL distribution.
        mean_pnl = np.mean(pnl_arr_sorted)
        std_pnl_pop = np.std(pnl_arr_sorted)  # population std for moment ratios
        if std_pnl_pop > 0:
            z = (pnl_arr_sorted - mean_pnl) / std_pnl_pop
            m.skew = float(np.mean(z ** 3))
            m.kurtosis = float(np.mean(z ** 4) - 3.0)

    # Monthly / Yearly returns
    sorted_trades = sorted(closed, key=lambda t: t.entry_time)
    monthly: dict[str, float] = {}
    yearly:  dict[int, float] = {}
    for t in sorted_trades:
        if t.entry_time:
            month_key = t.entry_time.strftime("%Y-%m")
            year_key  = t.entry_time.year
            monthly[month_key] = monthly.get(month_key, 0) + t.pnl_usd
            yearly[year_key]   = yearly.get(year_key, 0) + t.pnl_usd

    m.monthly_returns = monthly
    m.yearly_returns  = yearly

    # Annual return
    if sorted_trades and sorted_trades[0].entry_time and sorted_trades[-1].entry_time:
        days = (sorted_trades[-1].entry_time - sorted_trades[0].entry_time).days or 1
        m.annual_return = m.total_return_pct * 365 / days

    # By direction
    for direction in ("BUY", "SELL"):
        subset = [t for t in closed if t.direction == direction]
        if subset:
            wins = sum(1 for t in subset if t.is_win)
            pnl  = sum(t.pnl_usd for t in subset)
            gp   = sum(p for p in (t.pnl_usd for t in subset) if p > 0)
            gl   = abs(sum(p for p in (t.pnl_usd for t in subset) if p < 0))
            m.by_direction[direction] = {
                "trades": len(subset), "wins": wins,
                "win_rate": wins/len(subset)*100, "pnl": pnl,
                "gross_profit": gp, "gross_loss": gl,
            }

    # By session
    for t in closed:
        s = t.session or "Unknown"
        if s not in m.by_session:
            m.by_session[s] = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0}
        m.by_session[s]["trades"] += 1
        m.by_session[s]["pnl"]    += t.pnl_usd
        if t.is_win:
            m.by_session[s]["wins"] += 1
        if t.pnl_usd > 0:
            m.by_session[s]["gross_profit"] += t.pnl_usd
        elif t.pnl_usd < 0:
            m.by_session[s]["gross_loss"] += abs(t.pnl_usd)

    # By regime
    for t in closed:
        r = t.regime or "Unknown"
        if r not in m.by_regime:
            m.by_regime[r] = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0}
        m.by_regime[r]["trades"] += 1
        m.by_regime[r]["pnl"]    += t.pnl_usd
        if t.is_win:
            m.by_regime[r]["wins"] += 1
        if t.pnl_usd > 0:
            m.by_regime[r]["gross_profit"] += t.pnl_usd
        elif t.pnl_usd < 0:
            m.by_regime[r]["gross_loss"] += abs(t.pnl_usd)

    # By direction+regime+session (3-way pooled cross) — Edge Discovery
    # (2026-07-31). See the field's own docstring on BacktestMetrics for
    # why this is one flat compound-keyed dict rather than 7 separate
    # fields.
    for t in closed:
        key = f"{t.direction or 'Unknown'}|{t.regime or 'Unknown'}|{t.session or 'Unknown'}"
        b = m.by_direction_regime_session.setdefault(
            key, {"trades": 0, "wins": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0}
        )
        b["trades"] += 1
        b["pnl"]    += t.pnl_usd
        if t.is_win:
            b["wins"] += 1
        if t.pnl_usd > 0:
            b["gross_profit"] += t.pnl_usd
        elif t.pnl_usd < 0:
            b["gross_loss"] += abs(t.pnl_usd)

    # By engine — unlike direction/session/regime (exactly one category per
    # trade), multiple engines can vote on the same trade (TradeRecord.
    # engine_votes is a dict keyed by every engine that voted), so a trade
    # can contribute to more than one engine's bucket here.
    for t in closed:
        for engine_name in t.engine_votes:
            if engine_name not in m.by_engine:
                m.by_engine[engine_name] = {"trades": 0, "wins": 0, "pnl": 0.0}
            m.by_engine[engine_name]["trades"] += 1
            m.by_engine[engine_name]["pnl"]    += t.pnl_usd
            if t.is_win:
                m.by_engine[engine_name]["wins"] += 1

    # Win rates per category. by_engine deliberately gets no profit_factor
    # (an engine's votes span overlapping trades — different semantics
    # than the mutually-exclusive direction/session/regime buckets).
    for cat_dict in (m.by_session, m.by_regime, m.by_direction, m.by_engine, m.by_direction_regime_session):
        for v in cat_dict.values():
            if isinstance(v, dict) and v.get("trades", 0) > 0:
                v["win_rate"] = v.get("wins", 0) / v["trades"] * 100
                if "gross_profit" in v:
                    gp, gl = v["gross_profit"], v["gross_loss"]
                    v["profit_factor"] = (gp / gl) if gl > 0 else float("inf")

    return m
