"""
Session & Day-Type Classifier — Dalton opening types, Balance/Breakout state.

Derives from IBKR-only data (1m OHLCV, tick engine cumulative delta, Globex range).

Produces:
  - Dalton's 4 opening types (Open Auction-Drive, Open Test-Drive,
    Open Rejection, Open Rotation)
  - Balance/Breakout state machine with live invalidation
  - Overnight inventory (Globex net direction, gap fill status)
  - Opening auction process analysis
  - Session context enrichments for time_of_day.py
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from utils.logger import get_logger

logger = get_logger("engine.session_classifier")

# Opening range periods (minutes from RTH open at 9:30 ET)
_OR_START = 0    # 9:30
_OR_END = 30     # 10:00 (first 30 min for open classification)
_IB_START = 0    # 9:30
_IB_END = 60     # 10:30 (60-min initial balance)

# Dalton opening type thresholds
_DRIVE_MIN_RANGE_PCT = 0.002   # 0.2% move in first 30m = driven
_TEST_RETRACE_PCT = 0.50       # retrace > 50% = test, not drive
_REJECTION_STOP_PCT = 0.001    # stop below OR low on open drive up


def classify_opening_type(ohlcv_1m: list) -> dict:
    """Classify the NY opening type per Dalton's four categories.

    Types:
      1. Open Auction-Drive:   Price drives in one direction from open,
                               little retracement, initiative buying/selling.
      2. Open Test-Drive:      Initial drive, then retracement to test the
                               other side, then continuation of original drive.
      3. Open Rejection:       Price gaps or drives one way, then reverses
                               sharply, stopping beyond the open.
      4. Open Rotation:        No clear direction, overlapping range, balanced
                               auction — price searches for fair value.

    Args:
        ohlcv_1m: List of 1-min bars for the current day.

    Returns:
        Dict with opening type, direction, range, and key levels.
    """
    if not ohlcv_1m or len(ohlcv_1m) < 10:
        return {"type": "unknown", "direction": "neutral"}

    # Get NY RTH opening bars (first 30 min)
    open_bars = []
    for c in ohlcv_1m:
        mins = _minutes_from_ts(c.get("timestamp", ""))
        if mins is not None and 570 <= mins < 600:
            open_bars.append(c)
    if len(open_bars) < 5:
        return {"type": "unknown", "direction": "neutral"}

    or_high = max(c["high"] for c in open_bars)
    or_low = min(c["low"] for c in open_bars)
    or_range = or_high - or_low
    or_open = open_bars[0]["open"]
    or_close = open_bars[-1]["close"]
    or_mid = (or_high + or_low) / 2

    # Get full RTH bars up to now for extension check
    all_rth = []
    for c in ohlcv_1m:
        mins = _minutes_from_ts(c.get("timestamp", ""))
        if mins is not None and 570 <= mins < 960:
            all_rth.append(c)
    if len(all_rth) < 10:
        return {"type": "unknown", "direction": "neutral"}

    current_price = all_rth[-1]["close"]
    session_high = max(c["high"] for c in all_rth)
    session_low = min(c["low"] for c in all_rth)

    # Direction in opening range
    or_direction = "up" if or_close > or_open else "down" if or_close < or_open else "flat"

    # Range test: did price test both sides of OR?
    or_window = open_bars[:20] if len(open_bars) >= 20 else open_bars
    above_high = any(c["high"] > or_high for c in or_window)
    below_low = any(c["low"] < or_low for c in or_window)
    tested_both = above_high and below_low

    # Retracement from initial edge
    if or_direction == "up":
        initial_edge = or_high
        retrace_low = min(c["low"] for c in open_bars[5:])
        retracement = (initial_edge - retrace_low) / max(or_range, 0.01)
    elif or_direction == "down":
        initial_edge = or_low
        retrace_high = max(c["high"] for c in open_bars[5:])
        retracement = (retrace_high - initial_edge) / max(or_range, 0.01)
    else:
        initial_edge = 0
        retracement = 0

    # Post-OR extension beyond opening range
    extension = 0
    if or_direction == "up" and or_high > 0:
        extension = (session_high - or_high) / max(or_high, 0.01)
    elif or_direction == "down" and or_low > 0:
        extension = (or_low - session_low) / max(or_low, 0.01)

    # Dalton classification
    or_type = "rotation"
    direction = "neutral"

    # Classification logic
    is_driven = or_range / max(or_open, 0.01) > _DRIVE_MIN_RANGE_PCT

    if is_driven and retracement < _TEST_RETRACE_PCT and not tested_both:
        if or_direction == "up":
            or_type = "auction_drive"
            direction = "long"
        else:
            or_type = "auction_drive"
            direction = "short"
    elif is_driven and retracement >= _TEST_RETRACE_PCT:
        if extension > _DRIVE_MIN_RANGE_PCT:
            or_type = "test_drive"
            direction = "long" if extension > 0 else "short"
        else:
            or_type = "rejection"
            direction = "short" if or_direction == "up" else "long"
    elif tested_both and or_range / max(or_open, 0.01) < _DRIVE_MIN_RANGE_PCT * 2:
        or_type = "rotation"
        direction = "neutral"
    elif is_driven and tested_both and or_range / max(or_open, 0.01) > _DRIVE_MIN_RANGE_PCT:
        or_type = "rejection"
        direction = "short" if or_direction == "up" else "long"

    return {
        "type": or_type,
        "direction": direction,
        "or_high": round(or_high, 2),
        "or_low": round(or_low, 2),
        "or_range": round(or_range, 2),
        "or_open": round(or_open, 2),
        "or_close": round(or_close, 2),
        "initial_bias": or_direction,
        "retracement_pct": round(retracement, 4),
        "extension_pct": round(extension, 6),
        "tested_both_sides": tested_both,
        "is_driven": is_driven,
        "current_price": round(current_price, 2),
        "relative_to_or": (
            "above" if current_price > or_high
            else "below" if current_price < or_low
            else "inside"
        ),
    }


def classify_balance_breakout(ohlcv_1m: list, prior_vah: float = 0, prior_val: float = 0) -> dict:
    """Balance/Breakout state machine.

    States:
      - balance: Price oscillating within a defined range, no directional conviction
      - breakout_up: Price breaks above balance high with momentum
      - breakout_down: Price breaks below balance low with momentum
      - breakout_failure: Price breaks out then reverses back into balance
      - developing: Not enough bars to classify

    Args:
        ohlcv_1m: 1-min bars for current session.
        prior_vah: Prior session's value area high.
        prior_val: Prior session's value area low.

    Returns:
        Dict with state, balance range, breakout metrics.
    """
    if not ohlcv_1m or len(ohlcv_1m) < 10:
        return {"state": "developing"}

    rth_bars = []
    for c in ohlcv_1m:
        mins = _minutes_from_ts(c.get("timestamp", ""))
        if mins is not None and 570 <= mins < 960:
            rth_bars.append(c)
    if len(rth_bars) < 10:
        return {"state": "developing"}

    # Compute balance range from first ~60 min (A-B periods)
    ib_bars = rth_bars[:min(60, len(rth_bars))]
    bal_high = max(c["high"] for c in ib_bars)
    bal_low = min(c["low"] for c in ib_bars)
    bal_mid = (bal_high + bal_low) / 2
    bal_range = bal_high - bal_low

    # Current extension
    current = rth_bars[-1]["close"]
    recent_high = max(c["high"] for c in rth_bars[-30:])
    recent_low = min(c["low"] for c in rth_bars[-30:])

    # Breakout detection
    breakout_threshold = bal_range * 0.15 if bal_range > 0 else 0
    above_balance = recent_high > bal_high + max(breakout_threshold, 0.5)
    below_balance = recent_low < bal_low - max(breakout_threshold, 0.5)

    # Invalidation check: did price re-enter balance after breakout?
    if above_balance:
        re_entry = current <= bal_high
        if re_entry:
            state = "breakout_failure_up"
        else:
            state = "breakout_up"
    elif below_balance:
        re_entry = current >= bal_low
        if re_entry:
            state = "breakout_failure_down"
        else:
            state = "breakout_down"
    else:
        state = "balance"

    # Extension from prior value area
    prior_extension = 0
    if prior_vah > 0 and prior_val > 0:
        if current > prior_vah:
            prior_extension = (current - prior_vah) / max(prior_vah - prior_val, 1)
        elif current < prior_val:
            prior_extension = (prior_val - current) / max(prior_vah - prior_val, 1)

    return {
        "state": state,
        "balance_high": round(bal_high, 2),
        "balance_low": round(bal_low, 2),
        "balance_mid": round(bal_mid, 2),
        "balance_range": round(bal_range, 2),
        "current_price": round(current, 2),
        "breakout_distance": round(current - bal_high if above_balance else bal_low - current if below_balance else 0, 2),
        "prior_va_extension": round(prior_extension, 4),
        "above_balance": above_balance,
        "below_balance": below_balance,
        "in_balance": not above_balance and not below_balance,
    }


def compute_overnight_inventory(globex_range: dict, cumulative_delta: dict) -> dict:
    """Compute overnight inventory status from Globex range and delta.

    Args:
        globex_range: Dict from futures_data.get_globex_range()
        cumulative_delta: Dict from tick_engine.get_tick_stats()

    Returns:
        Dict with overnight direction, gap fill status, inventory pressure.
    """
    if not globex_range:
        return {}

    gap_up = globex_range.get("gap_up", False)
    gap_down = globex_range.get("gap_down", False)
    gap_pct = globex_range.get("gap_pct", 0)
    globex_high = globex_range.get("globex_high", 0)
    globex_low = globex_range.get("globex_low", 0)
    rth_open = globex_range.get("rth_open", 0)

    # Gap fill status: price has returned to Globex range
    gap_filled = False
    gap_fill_pct = 0
    if gap_up and globex_high > 0 and rth_open > 0:
        current_gap = rth_open - globex_high
        filled = max(0, rth_open - min(rth_open, globex_high)) if current_gap > 0 else 0
        gap_fill_pct = filled / max(current_gap, 0.01)
        gap_filled = gap_fill_pct >= 0.90
    elif gap_down and globex_low > 0 and rth_open > 0:
        current_gap = globex_low - rth_open
        filled = max(0, globex_low - max(rth_open, globex_low)) if current_gap > 0 else 0
        gap_fill_pct = filled / max(current_gap, 0.01)
        gap_filled = gap_fill_pct >= 0.90

    # Overnight delta direction
    delta_60s = cumulative_delta.get("delta_60s", 0) if cumulative_delta else 0
    overnight_delta_dir = "buying" if delta_60s > 0 else "selling" if delta_60s < 0 else "neutral"

    # Inventory pressure: combined gap + delta
    if gap_up and overnight_delta_dir == "buying":
        inventory = "long_sided"
    elif gap_down and overnight_delta_dir == "selling":
        inventory = "short_sided"
    elif gap_up and overnight_delta_dir == "selling":
        inventory = "gap_fade"
    elif gap_down and overnight_delta_dir == "buying":
        inventory = "gap_fade"
    else:
        inventory = "balanced"

    return {
        "gap_up": gap_up,
        "gap_down": gap_down,
        "gap_pct": round(gap_pct * 100, 2) if gap_pct else 0,
        "gap_filled": gap_filled,
        "gap_fill_pct": round(gap_fill_pct, 4),
        "overnight_delta_dir": overnight_delta_dir,
        "inventory": inventory,
        "globex_high": round(globex_high, 2) if globex_high else 0,
        "globex_low": round(globex_low, 2) if globex_low else 0,
    }


def compute_session_context(
    ohlcv_1m: list,
    cumulative_delta: dict,
    globex_range: dict,
    current_price: float,
    prior_vah: float = 0,
    prior_val: float = 0,
) -> dict:
    """Compute full session context combining opening type, balance/breakout
    state, overnight inventory, and VWAP from 1m bars.

    This is the main entry point for runner.py — returns a richer session_context
    dict replacing the current time_of_day.get_market_session_context().

    Args:
        ohlcv_1m: 1-min bars for current day.
        cumulative_delta: Tick engine cumulative delta.
        globex_range: Globex overnight range.
        current_price: Current live price.
        prior_vah: Prior session value area high.
        prior_val: Prior session value area low.

    Returns:
        Complete session context dict.
    """
    if not ohlcv_1m or len(ohlcv_1m) < 5:
        return {}

    opening = classify_opening_type(ohlcv_1m)
    bb_state = classify_balance_breakout(ohlcv_1m, prior_vah, prior_val)
    overnight = compute_overnight_inventory(globex_range, cumulative_delta)

    # VWAP from 1m bars (accurate)
    cum_pv = 0.0
    cum_vol = 0
    for c in ohlcv_1m:
        typ_price = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = c.get("volume", 0)
        cum_pv += typ_price * vol
        cum_vol += vol
    vwap = round(cum_pv / cum_vol, 2) if cum_vol > 0 else 0

    # VWAP position
    vwap_position = "above" if current_price > vwap else "below"
    vwap_distance = abs(current_price - vwap) / max(vwap, 0.01) if vwap > 0 else 0

    return {
        "vwap": vwap,
        "vwap_position": vwap_position,
        "vwap_distance_pct": round(vwap_distance, 4),
        "opening_type": opening.get("type", "unknown"),
        "opening_direction": opening.get("direction", "neutral"),
        "opening_range_high": opening.get("or_high", 0),
        "opening_range_low": opening.get("or_low", 0),
        "in_opening_range": opening.get("relative_to_or") == "inside",
        "balance_state": bb_state.get("state", "developing"),
        "balance_high": bb_state.get("balance_high", 0),
        "balance_low": bb_state.get("balance_low", 0),
        "in_balance": bb_state.get("in_balance", False),
        "overnight_inventory": overnight.get("inventory", "balanced"),
        "gap_up": overnight.get("gap_up", False),
        "gap_down": overnight.get("gap_down", False),
        "gap_pct": overnight.get("gap_pct", 0),
        "gap_filled": overnight.get("gap_filled", False),
        "day_type": opening.get("type", "unknown"),
        "bb_state": bb_state.get("state", "developing"),
        "source": "session_classifier",
    }


def _minutes_from_ts(ts_str: str) -> Optional[int]:
    try:
        if "T" in ts_str:
            dt = datetime.fromisoformat(ts_str)
        else:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        return dt.hour * 60 + dt.minute
    except Exception:
        return None
