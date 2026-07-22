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
    # Signal persistence — neutral cooldown (slot lifetime)
    "neutral_cooldown_active": 6,       # ACTIVE signals: neutrals before full reset
    "neutral_cooldown_confirmed": 8,    # CONFIRMED signals: neutrals before full reset
    "neutral_cooldown_max": 3,          # Standard (non-sticky): neutrals before reset
    "sticky_counter_cycles": 2,         # Counter-direction cycles before sticky downgrade
    # Signal age decay (minutes)
    "signal_age_decay_start_min": 15,
    "signal_age_decay_half_min": 60,
    "signal_age_decay_floor": 0.50,
    # 0DTE Mode — filters strategies to gamma/flow/dealer only for option signals
    "odte_mode": False,
    # Closing pin — block new entries in last 30 minutes before close
    # 0DTE traders may want this ON (quality) or OFF (last-minute scalps)
    "block_entries_in_closing_pin": True,
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
