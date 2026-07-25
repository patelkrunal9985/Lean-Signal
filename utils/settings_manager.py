"""Settings Manager — Persists user-configurable engine settings to a JSON file.

Thread-safe: all read/write operations acquire a lock so the engine can read
settings mid-cycle without races with the dashboard saving settings.

Settings file location: data/settings.json (auto-created on first save)
"""

from __future__ import annotations
import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_SETTINGS_FILE = Path(__file__).parent.parent / "data" / "settings.json"

# ── Default values ──────────────────────────────────────────────
_DEFAULTS: dict[str, Any] = {
    # Signal age decay (minutes)
    "signal_age_decay_start_min": 15,
    "signal_age_decay_half_min": 60,
    "signal_age_decay_floor": 0.50,
    # 0DTE Mode — filters strategies to gamma/flow/dealer only for option signals
    "odte_mode": True,
    # Closing pin — block new entries in last 30 minutes before close
    "block_entries_in_closing_pin": True,
    "flip_score_threshold": 0.60,   # flip significance cutoff
    # ── Conviction-based signal persistence (Thesis Validation) ──
    "conviction_decay": 0.97,       # Leak per cycle (lower = faster decay)
    "min_conviction": 0.25,         # Min conviction to enter a thesis
    "thesis_horizon": 30,           # Cycles before thesis is "stale"
    "pnl_profit_mid_atr": 1.5,      # ATR profit → conviction floor 0.15 (mid-tier)
    "pnl_profit_floor_atr": 2.0,    # ATR profit → conviction floor 0.20
    "pnl_profit_confirm_atr": 3.0,  # ATR profit → force confirmed (0.80)
    "pnl_force_exit_atr": 2.5,      # ATR drawdown → force exit
    "regime_invalidation_factor": 0.60,  # Regime change conviction penalty
    # ── Velocity Detection (ATR/cycle thresholds) ──
    "velocity_spike_atr": 0.8,      # ATR/cycle → instant conviction crash (0.30x)
    "velocity_penalty_atr": 0.4,    # ATR/cycle → heavy penalty (0.55x)
    "velocity_building_max": 0.70,  # Max conviction cap when velocity supports new thesis
    # ── Signal Budget (P1.2) ──
    "signal_budget_max": 6,         # Max concurrent active signals before blocking new entries
    # ── Per-instrument overrides (P2.3) ──
    "future_signal_budget_max": 8,  # Futures get higher budget
    "option_signal_budget_max": 4,  # Options get lower budget
    "stock_signal_budget_max": 6,   # Stocks default
    "future_velocity_building_max": 0.75,
    "option_velocity_building_max": 0.60,
    "stock_velocity_building_max": 0.70,
    # Verdict scoring thresholds (level at which each verdict fires)
    "verdict_exit_threshold": -1,   # level ≤ this → EXIT
    "verdict_reduce_threshold": 0,  # level ≤ this → REDUCE
    "verdict_hold_threshold": 1,    # level ≤ this → HOLD
    "verdict_buy_threshold": 2,     # level ≤ this → BUY/SELL
    "verdict_strong_threshold": 3,  # level ≥ this → STRONG
}

# ── ODTE Strategy Whitelist ─────────────────────────────────────
# When odte_mode is ON and instrument_type is "option", ONLY these V3
# strategy names will be computed. All others (theta_decay, iv_rv_spread,
# earnings_vol_arbitrage, etc.) are filtered out because they are designed
# for 5-30 DTE and add noise to 0DTE consensus.
#
ODTE_STRATEGY_WHITELIST: set[str] = {
    # ── Gamma & Dealer Positioning (core 0DTE) ──
    "gamma_exposure",
    "zero_dte_gamma",
    "vanna_charm_flow",
    "delta_gamma_imbalance",
    "delta_hedging_imbalance",
    "delta_positioning",
    "gamma_flip_levels",
    "gamma_flip_acceleration",
    "expiry_day_gamma",
    # ── Large Flow & Whale Activity ──
    "large_option_flow",
    "unusual_whale_flow",
    "option_volume_flow",
    "vwap_option_flow",
    "strike_volume_surge",
    "call_put_wall_breakout",
    # ── Sentiment & OI ──
    "put_call_divergence",
    "oi_concentration",
    "oi_change_rate",
    "prior_hl_magnetism",
    "breadth_confirmation",
    # ── IV / Skew / Vol Structure ──
    "iv_skew",
    "vol_smile_curvature",
    "skew_term_structure",
    "vix_spx_convexity",
    "sector_etf_option_rotation",
    "opening_drive",
    "max_pain",
}

# Strategies explicitly EXCLUDED from ODTE.
# NOTE: This is documentation-only. The active filter uses the WHITELIST above.
# These strategies are fine for 5-30 DTE but add noise for same-day expiry.
ODTE_STRATEGY_BLACKLIST: set[str] = {
    "theta_decay",              # Designed for 30+ DTE theta harvesting
    "iv_rv_spread",             # Medium-term vol arbitrage
    "expected_vs_actual",       # Multi-DTE expected move analysis
    "earnings_vol_arbitrage",   # Single-stock earnings, different context
    "iv_rank_percentile",       # Adds noise for 0DTE
    "credit_spread_detector",   # Multi-leg multi-DTE
    "iron_condor_detector",     # Multi-leg multi-DTE
}

# ── In-memory cache ─────────────────────────────────────────────
_cache: dict[str, Any] = dict(_DEFAULTS)


def _load_from_disk() -> dict[str, Any]:
    """Read settings from disk, returning {} if file missing/corrupt."""
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        with open(_SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_to_disk(data: dict[str, Any]):
    """Atomically write settings to disk."""
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _SETTINGS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(_SETTINGS_FILE)


def init():
    """Load settings from disk, merging with defaults. Call once at startup."""
    global _cache
    with _lock:
        disk = _load_from_disk()
        merged = dict(_DEFAULTS)
        merged.update(disk)
        _cache = merged


def get(key: str, default: Any = None) -> Any:
    """Get a setting value. Falls back to default, then to the hardcoded default."""
    with _lock:
        return _cache.get(key, _DEFAULTS.get(key, default))


def get_all() -> dict[str, Any]:
    """Get all current settings (merged defaults + overrides)."""
    with _lock:
        return dict(_cache)


def set_many(overrides: dict[str, Any]) -> dict[str, Any]:
    """Apply a batch of setting overrides, persist to disk, return full state.

    Only keys that exist in _DEFAULTS are accepted (unknown keys are silently
    ignored to prevent injection of arbitrary config).
    """
    with _lock:
        for key, value in overrides.items():
            if key in _DEFAULTS:
                # Coerce to the same type as the default
                default_val = _DEFAULTS[key]
                if isinstance(default_val, int):
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        continue
                elif isinstance(default_val, float):
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        continue
                _cache[key] = value
        _save_to_disk(_cache)
        return dict(_cache)


def reset():
    """Reset all settings to defaults and persist."""
    with _lock:
        _cache.clear()
        _cache.update(_DEFAULTS)
        _save_to_disk(_cache)


# Auto-init on import
init()
