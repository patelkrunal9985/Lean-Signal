"""
Time-of-Day Layer -- 0DTE time-window aware strategy weighting and trade blocking.

Defines 5 intraday windows and adjusts:
  1. Strategy weights per time window (some strategies dominate at open, others at close)
  2. Entry/exit stop tightness (tighten in power hour when gamma explodes)
  3. Trade blocking rules (no new directional entries in last 30 min)

SPX/SPY/QQQ cash-settled options expire at 4:00 PM ET. SPXW has 4:15 PM ET close.
This module handles both.
"""
from __future__ import annotations
from datetime import time
from typing import Optional

from utils.logger import get_logger

logger = get_logger("engine.time_of_day")

# -- Time window definitions (minutes since midnight ET) --
# 9:30 = 570, 10:10 = 610, etc.
TIME_WINDOWS = {
    "opening_drive":    (570, 610),   # 9:30 - 10:10
    "morning_session":  (610, 690),   # 10:10 - 11:30
    "midday_lull":       (690, 840),   # 11:30 - 14:00
    "power_hour":        (840, 930),   # 14:00 - 15:30
    "closing_pin":       (930, 960),   # 15:30 - 16:00
}

# -- Strategy weight multipliers per time window --
# 1.0 = no change, >1.0 = boosted, <1.0 = dampened, 0.0 = blocked
STRATEGY_TIME_WEIGHTS: dict[str, dict[str, float]] = {
    "opening_drive": {
        "opening_drive": 2.5,
        "option_volume_flow": 1.8,
        "unusual_whale_flow": 1.5,
        "large_option_flow": 1.5,
        "call_put_wall_breakout": 1.3,
        "gamma_flip_levels": 0.5,
        "theta_decay": 0.2,
        "max_pain": 0.3,
        "zero_dte_gamma": 0.3,
    },
    "morning_session": {
        "gamma_exposure": 1.6,
        "gamma_flip_levels": 1.4,
        "call_put_wall_breakout": 1.3,
        "delta_positioning": 1.2,
        "delta_hedging_imbalance": 1.2,
        "iv_skew": 1.2,
        "theta_decay": 0.5,
    },
    "midday_lull": {
        # Everything dampened during chop -- only high-conviction signals trade
        "default": 0.55,
        "max_pain": 0.8,       # pin mechanics still valid
        "oi_concentration": 0.8,
    },
    "power_hour": {
        "zero_dte_gamma": 2.5,
        "gamma_flip_acceleration": 2.0,
        "max_pain": 1.8,
        "gamma_flip_levels": 1.5,
        "theta_decay": 0.05,    # NEVER sell premium in power hour
        "iv_rv_spread": 0.3,
        "expected_vs_actual": 0.3,
    },
    "closing_pin": {
        "max_pain": 2.5,
        "zero_dte_gamma": 1.5,
        "gamma_flip_acceleration": 1.5,
        "gamma_flip_levels": 1.2,
        # Everything else blocked
        "default": 0.0,
    },
}

# -- Stop tightness multiplier per time window (applied to SL distance) --
# <1.0 = tighter stops (gamma acceleration), >1.0 = wider stops (volatility at open)
STOP_TIGHTNESS: dict[str, float] = {
    "opening_drive": 1.2,      # wider stops during gap fill (noisy)
    "morning_session": 1.0,    # normal
    "midday_lull": 0.85,       # slightly tighter (low vol chop)
    "power_hour": 0.65,        # much tighter (gamma explosion)
    "closing_pin": 0.50,       # tightest (settlement mechanics)
}

# -- Minimum confidence required per time window --
MIN_CONFIDENCE: dict[str, float] = {
    "opening_drive": 0.15,
    "morning_session": 0.12,
    "midday_lull": 0.22,       # higher bar during chop
    "power_hour": 0.15,
    "closing_pin": 0.30,       # very high bar near close
}

# -- Block new entries in closing_pin? --
BLOCK_NEW_ENTRIES_IN_CLOSING = True

# -- Minimum minutes before close to allow entry --
MIN_MINUTES_BEFORE_CLOSE = 15  # block entries after 3:45 PM ET

# -- Underlying tickers that use 4:15 PM ET close (SPXW weekly) --
LATE_CLOSE_TICKERS = frozenset({"SPX"})


def _current_et_minutes() -> int:
    """Return minutes since midnight ET, or 9999 if weekend."""
    from kronos.utils.time_utils import now_ny
    t = now_ny()
    if t.weekday() >= 5:
        return 9999
    return t.hour * 60 + t.minute


def get_time_window() -> str:
    """Return the current intraday time window name."""
    now_min = _current_et_minutes()
    if now_min >= 9999:
        return "closed"
    for name, (start, end) in TIME_WINDOWS.items():
        if start <= now_min < end:
            return name
    # Before 9:30 or after 16:00
    if now_min < 570:
        return "pre_market"
    return "after_hours"


def get_strategy_time_weight(strategy_name: str) -> float:
    """Get the time-based weight multiplier for a strategy."""
    window = get_time_window()
    weights = STRATEGY_TIME_WEIGHTS.get(window, {})
    if strategy_name in weights:
        return weights[strategy_name]
    return weights.get("default", 1.0)


def get_stop_tightness() -> float:
    """Get the stop tightness multiplier for the current time window."""
    window = get_time_window()
    return STOP_TIGHTNESS.get(window, 1.0)


def get_min_confidence() -> float:
    """Get the minimum confidence for the current time window."""
    window = get_time_window()
    return MIN_CONFIDENCE.get(window, 0.15)


def can_enter_new_trade(ticker: str, dte: int = 0) -> tuple[bool, str]:
    """Check if new entries are allowed at this time.

    Returns (allowed, reason).
    For 0DTE options: blocks new entries in closing_pin or within
    MIN_MINUTES_BEFORE_CLOSE of expiry.
    """
    window = get_time_window()

    # Always block in closing_pin for 0DTE
    if dte == 0 and BLOCK_NEW_ENTRIES_IN_CLOSING:
        if window == "closing_pin":
            return False, "blocked_closing_pin_0dte"

    # Block within N minutes of close for all instruments
    now_min = _current_et_minutes()
    if now_min < 570:
        return False, "pre_market"

    close_min = 960  # 16:00 ET default
    if ticker in LATE_CLOSE_TICKERS:
        close_min = 975  # 16:15 ET for SPXW

    if now_min >= close_min - MIN_MINUTES_BEFORE_CLOSE:
        return False, f"within_{MIN_MINUTES_BEFORE_CLOSE}min_of_close"

    # Midday lull: allow but mark as reduced conviction
    if window == "midday_lull":
        return True, "midday_reduced_conviction"

    return True, "allowed"


def get_market_session_context(ohlcv: list, current_price: float) -> dict:
    """Compute intraday market structure context.

    Returns dict with:
      - vwap: session VWAP (approximated from daily OHLCV bar)
      - vwap_position: 'above' or 'below'
      - opening_range_high/low: first 5-min range (approximated)
      - day_pct_range: current day's range as % of open
    """
    if not ohlcv or current_price <= 0:
        return {}

    today = ohlcv[-1] if isinstance(ohlcv[-1], dict) else None
    if not today:
        return {}

    open_price = float(today.get("open", 0) or 0)
    high = float(today.get("high", 0) or 0)
    low = float(today.get("low", 0) or 0)
    close = float(today.get("close", current_price) or current_price)

    # Approximate VWAP from daily bar: (H+L+C)/3 weighted toward close
    typical = (high + low + close) / 3 if high > 0 else current_price
    # Better VWAP approximation: volume-weighted typical price
    # Since we don't have intraday data, use typical price as proxy
    vwap = typical if typical > 0 else current_price
    vwap_position = "above" if current_price > vwap else "below"
    vwap_distance_pct = abs(current_price - vwap) / vwap if vwap > 0 else 0

    # Opening range approximation: first 5 minutes
    # Without tick data, estimate from open-to-close range
    if open_price > 0:
        or_high = max(open_price, high * 0.99 + open_price * 0.01) if high > 0 else open_price * 1.005
        or_low = min(open_price, low * 0.99 + open_price * 0.01) if low > 0 else open_price * 0.995
    else:
        or_high = current_price * 1.005
        or_low = current_price * 0.995

    # Day range as % of open
    day_range_pct = ((high - low) / open_price) if open_price > 0 and high > low else 0

    return {
        "vwap": round(vwap, 2),
        "vwap_position": vwap_position,
        "vwap_distance_pct": round(vwap_distance_pct, 4),
        "opening_range_high": round(or_high, 2),
        "opening_range_low": round(or_low, 2),
        "in_opening_range": or_low <= current_price <= or_high,
        "day_range_pct": round(day_range_pct, 4),
    }


def vwap_alignment_ok(session_ctx: dict, direction: str) -> tuple[bool, str]:
    """Check if trade direction aligns with VWAP and opening range.

    Rules:
      - Long above VWAP = aligned (trend continuation)
      - Long below VWAP = needs extra conviction but allowed
      - Short below VWAP = aligned
      - Short above VWAP = needs extra conviction but allowed
      - Block only when price is far from VWAP (>2%) against the trend
    """
    if not session_ctx:
        return True, "no_session_context"

    vwap_pos = session_ctx.get("vwap_position", "")
    vwap_dist = session_ctx.get("vwap_distance_pct", 0)
    in_or = session_ctx.get("in_opening_range", True)

    # Block extreme counter-VWAP moves (>2% away)
    if direction == "long" and vwap_pos == "below" and vwap_dist > 0.02 and not in_or:
        return False, "far_below_vwap_blocks_long"
    if direction == "short" and vwap_pos == "above" and vwap_dist > 0.02 and not in_or:
        return False, "far_above_vwap_blocks_short"

    if direction == "long":
        if vwap_pos == "above":
            return True, "vwap_aligned_long"
        elif in_or:
            return True, "in_opening_range_neutral"
        else:
            return True, "vwap_counter_long_allowed"
    else:  # short
        if vwap_pos == "below":
            return True, "vwap_aligned_short"
        elif in_or:
            return True, "in_opening_range_neutral"
        else:
            return True, "vwap_counter_short_allowed"
