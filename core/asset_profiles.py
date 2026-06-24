"""
core/asset_profiles.py
------------------------
Per-asset configuration: volatility character, session behavior,
pip/tick value, ATR expectations, and appropriate timeframes.

Why this matters: EURUSD and XAUUSD behave completely differently —
treating them identically (same ATR multipliers, same swing window,
same min_score) produces wrong results for both. This module provides
the per-asset context the engines and risk layer need.

Phase 2: profiles are hand-crafted from market knowledge.
Phase 3: profiles will be auto-calibrated from historical ATR data
         once the backtesting layer exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AssetProfile:
    symbol: str                    # internal symbol (e.g. EURUSD)
    td_symbol: str                 # Twelve Data format (e.g. EUR/USD)
    asset_class: str               # FOREX | METALS | ENERGY | INDICES | CRYPTO
    description: str

    # Volatility character (used to scale ATR multipliers and SL/TP)
    volatility_tier: str           # LOW | MEDIUM | HIGH | EXTREME
    typical_daily_range_pips: float  # approximate, for context

    # Swing detection: larger/faster assets need wider windows
    swing_window: int = 3          # bars on each side for swing detection
    min_swing_bars: int = 100      # minimum bars needed for reliable swing count

    # Risk sizing: pip/point value per standard lot in USD
    # Used when account_currency = USD
    pip_value_per_lot: float = 10.0   # USD per pip for 1 standard lot

    # Sessions where this asset is most liquid
    primary_sessions: list[str] = field(default_factory=lambda: ["London", "NewYork"])

    # Free plan Twelve Data native intervals (H4/D1 require paid plan)
    native_intervals: list[str] = field(default_factory=lambda: ["M15", "H1"])
    resample_intervals: list[str] = field(default_factory=lambda: ["H4", "D1"])

    # Score threshold adjustment relative to config baseline
    # e.g. CRYPTO = -5 (more permissive due to stronger trends)
    score_threshold_adj: float = 0.0


# ---------------------------------------------------------------------------
# FOREX Majors
# ---------------------------------------------------------------------------

PROFILES: dict[str, AssetProfile] = {

    "EURUSD": AssetProfile(
        symbol="EURUSD", td_symbol="EUR/USD",
        asset_class="FOREX", description="Euro / US Dollar",
        volatility_tier="LOW", typical_daily_range_pips=80,
        pip_value_per_lot=10.0,
        primary_sessions=["London", "NewYork", "Overlap"],
    ),

    "GBPUSD": AssetProfile(
        symbol="GBPUSD", td_symbol="GBP/USD",
        asset_class="FOREX", description="British Pound / US Dollar",
        volatility_tier="MEDIUM", typical_daily_range_pips=120,
        pip_value_per_lot=10.0,
        primary_sessions=["London", "Overlap"],
    ),

    "USDJPY": AssetProfile(
        symbol="USDJPY", td_symbol="USD/JPY",
        asset_class="FOREX", description="US Dollar / Japanese Yen",
        volatility_tier="MEDIUM", typical_daily_range_pips=80,
        pip_value_per_lot=9.0,   # approx, depends on USD/JPY rate
        primary_sessions=["Asia", "NewYork"],
    ),

    "USDCHF": AssetProfile(
        symbol="USDCHF", td_symbol="USD/CHF",
        asset_class="FOREX", description="US Dollar / Swiss Franc",
        volatility_tier="LOW", typical_daily_range_pips=70,
        pip_value_per_lot=10.0,
        primary_sessions=["London", "NewYork"],
    ),

    "AUDUSD": AssetProfile(
        symbol="AUDUSD", td_symbol="AUD/USD",
        asset_class="FOREX", description="Australian Dollar / US Dollar",
        volatility_tier="LOW", typical_daily_range_pips=70,
        pip_value_per_lot=10.0,
        primary_sessions=["Asia", "London"],
    ),

    "USDCAD": AssetProfile(
        symbol="USDCAD", td_symbol="USD/CAD",
        asset_class="FOREX", description="US Dollar / Canadian Dollar",
        volatility_tier="LOW", typical_daily_range_pips=70,
        pip_value_per_lot=10.0,
        primary_sessions=["NewYork"],
    ),

    "NZDUSD": AssetProfile(
        symbol="NZDUSD", td_symbol="NZD/USD",
        asset_class="FOREX", description="New Zealand Dollar / US Dollar",
        volatility_tier="LOW", typical_daily_range_pips=60,
        pip_value_per_lot=10.0,
        primary_sessions=["Asia", "London"],
    ),

    "EURJPY": AssetProfile(
        symbol="EURJPY", td_symbol="EUR/JPY",
        asset_class="FOREX", description="Euro / Japanese Yen",
        volatility_tier="MEDIUM", typical_daily_range_pips=100,
        pip_value_per_lot=9.0,
        primary_sessions=["Asia", "London", "Overlap"],
    ),

    "GBPJPY": AssetProfile(
        symbol="GBPJPY", td_symbol="GBP/JPY",
        asset_class="FOREX", description="British Pound / Japanese Yen",
        volatility_tier="HIGH", typical_daily_range_pips=150,
        pip_value_per_lot=9.0,
        swing_window=4,   # wider window for higher volatility
        primary_sessions=["Asia", "London", "Overlap"],
    ),

    "AUDJPY": AssetProfile(
        symbol="AUDJPY", td_symbol="AUD/JPY",
        asset_class="FOREX", description="Australian Dollar / Japanese Yen",
        volatility_tier="MEDIUM", typical_daily_range_pips=90,
        pip_value_per_lot=9.0,
        primary_sessions=["Asia", "London"],
    ),

    "EURGBP": AssetProfile(
        symbol="EURGBP", td_symbol="EUR/GBP",
        asset_class="FOREX", description="Euro / British Pound",
        volatility_tier="LOW", typical_daily_range_pips=50,
        pip_value_per_lot=10.0,
        primary_sessions=["London"],
    ),

    "EURCHF": AssetProfile(
        symbol="EURCHF", td_symbol="EUR/CHF",
        asset_class="FOREX", description="Euro / Swiss Franc",
        volatility_tier="LOW", typical_daily_range_pips=45,
        pip_value_per_lot=10.0,
        primary_sessions=["London"],
    ),

    # ---------------------------------------------------------------------------
    # Metals
    # ---------------------------------------------------------------------------

    "XAUUSD": AssetProfile(
        symbol="XAUUSD", td_symbol="XAU/USD",
        asset_class="METALS", description="Gold / US Dollar",
        volatility_tier="HIGH", typical_daily_range_pips=200,   # in pips (0.01)
        pip_value_per_lot=10.0,
        swing_window=4,
        min_swing_bars=80,
        primary_sessions=["London", "NewYork", "Overlap"],
        score_threshold_adj=-3.0,   # slightly more permissive — gold trends strongly
    ),

    "XAGUSD": AssetProfile(
        symbol="XAGUSD", td_symbol="XAG/USD",
        asset_class="METALS", description="Silver / US Dollar",
        volatility_tier="HIGH", typical_daily_range_pips=300,
        pip_value_per_lot=50.0,   # silver has higher pip value
        swing_window=4,
        min_swing_bars=80,
        primary_sessions=["London", "NewYork"],
        score_threshold_adj=-3.0,
    ),

    # ---------------------------------------------------------------------------
    # Energy
    # ---------------------------------------------------------------------------

    "USOIL": AssetProfile(
        symbol="USOIL", td_symbol="WTI/USD",
        asset_class="ENERGY", description="US Crude Oil (WTI)",
        volatility_tier="HIGH", typical_daily_range_pips=150,
        pip_value_per_lot=10.0,
        swing_window=4,
        min_swing_bars=80,
        primary_sessions=["NewYork"],
        score_threshold_adj=-5.0,
    ),

    # ---------------------------------------------------------------------------
    # Indices
    # ---------------------------------------------------------------------------

    "US30": AssetProfile(
        symbol="US30", td_symbol="DJI",
        asset_class="INDICES", description="Dow Jones Industrial Average",
        volatility_tier="HIGH", typical_daily_range_pips=300,
        pip_value_per_lot=1.0,
        swing_window=4,
        min_swing_bars=80,
        primary_sessions=["NewYork"],
        score_threshold_adj=-5.0,
    ),

    "NAS100": AssetProfile(
        symbol="NAS100", td_symbol="NDX",
        asset_class="INDICES", description="NASDAQ 100",
        volatility_tier="HIGH", typical_daily_range_pips=400,
        pip_value_per_lot=1.0,
        swing_window=4,
        min_swing_bars=80,
        primary_sessions=["NewYork"],
        score_threshold_adj=-5.0,
    ),

    "SPX500": AssetProfile(
        symbol="SPX500", td_symbol="SPX",
        asset_class="INDICES", description="S&P 500",
        volatility_tier="MEDIUM", typical_daily_range_pips=250,
        pip_value_per_lot=1.0,
        swing_window=3,
        min_swing_bars=80,
        primary_sessions=["NewYork"],
        score_threshold_adj=-3.0,
    ),

    # ---------------------------------------------------------------------------
    # Crypto
    # ---------------------------------------------------------------------------

    "BTCUSD": AssetProfile(
        symbol="BTCUSD", td_symbol="BTC/USD",
        asset_class="CRYPTO", description="Bitcoin / US Dollar",
        volatility_tier="EXTREME", typical_daily_range_pips=2000,
        pip_value_per_lot=1.0,
        swing_window=5,   # crypto needs wider windows due to noise
        min_swing_bars=60,
        primary_sessions=["London", "NewYork"],  # crypto trades 24/7 but these are peak
        score_threshold_adj=-8.0,   # much more permissive — crypto trends hard
    ),

    "ETHUSD": AssetProfile(
        symbol="ETHUSD", td_symbol="ETH/USD",
        asset_class="CRYPTO", description="Ethereum / US Dollar",
        volatility_tier="EXTREME", typical_daily_range_pips=1500,
        pip_value_per_lot=1.0,
        swing_window=5,
        min_swing_bars=60,
        primary_sessions=["London", "NewYork"],
        score_threshold_adj=-8.0,
    ),
}


def get_profile(symbol: str) -> AssetProfile:
    """Return the asset profile for a symbol. Raises KeyError if unknown."""
    clean = symbol.replace("/", "").upper()
    if clean not in PROFILES:
        raise KeyError(
            f"No asset profile for '{symbol}'. "
            f"Known symbols: {sorted(PROFILES.keys())}"
        )
    return PROFILES[clean]


def get_td_symbol(symbol: str) -> str:
    """Convert internal symbol to Twelve Data format via profile."""
    return get_profile(symbol).td_symbol


def all_symbols_by_class() -> dict[str, list[str]]:
    """Return all symbols grouped by asset class."""
    result: dict[str, list[str]] = {}
    for sym, profile in PROFILES.items():
        result.setdefault(profile.asset_class, []).append(sym)
    return result
