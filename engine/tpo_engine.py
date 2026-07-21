"""
TPO/Market Profile Engine — Dalton/Steidlmayer framework from 1m IBKR bars.

Computes:
  - Period-by-period TPO letters (A–M for 9:30–16:00 ET RTH)
  - Developing and final initial balance (first 60 min = A–B periods)
  - POC per period + overall POC
  - Value Area (VAH/VAL) per period + overall
  - Buying/selling tails, failed auction detection
  - Day-type classification (Normal/Balance/Breakout/Neutral)
  - TPO count distribution (long vs short profile)

All from `ohlcv_1m` bars fetched via IBKR. No external data needed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

logger = get_logger("engine.tpo_engine")

# RTH periods: 9:30–16:00 ET in 30-min blocks → letters A (9:30) through M (15:30)
# Each period is 30 minutes = 30 one-minute bars.
_TPO_PERIODS: list[tuple[str, int, int]] = [
    ("A", 570, 600), ("B", 600, 630), ("C", 630, 660), ("D", 660, 690),
    ("E", 690, 720), ("F", 720, 750), ("G", 750, 780), ("H", 780, 810),
    ("I", 810, 840), ("J", 840, 870), ("K", 870, 900), ("L", 900, 930),
    ("M", 930, 960),
]

_INITIAL_BALANCE_PERIODS = {"A", "B"}  # First 60 min = IB

# Day-type thresholds (Dalton)
_NEUTRAL_DAY_MAX = 0.35       # TPO overlap with prior day VA > 35%
_BALANCE_MAX_DELTA = 0.15     # POC-to-mid displacement < 15% of range
_BREAKOUT_MIN_EXTENSION = 0.30  # price extends beyond prior day VA > 30% of range

# RTH bucket for timestamp classification
_RTH_START_MINUTES = 570  # 9:30
_RTH_END_MINUTES = 960    # 16:00


def _minutes_from_ts(ts_str: str) -> Optional[int]:
    try:
        if "T" in ts_str:
            dt = datetime.fromisoformat(ts_str)
        else:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        return dt.hour * 60 + dt.minute
    except Exception:
        return None


def _assign_period(minutes: int) -> Optional[str]:
    for letter, start, end in _TPO_PERIODS:
        if start <= minutes < end:
            return letter
    return None


def compute_tpo_profile(ohlcv_1m: list, prior_day_va: Optional[dict] = None) -> dict:
    """Compute full TPO profile from 1-min OHLCV bars.

    Args:
        ohlcv_1m: List of 1-min bars with open/high/low/close/volume/timestamp.
        prior_day_va: Optional prior-day value area {vah, val}.

    Returns:
        Dict with tpo_periods, poc, vah, val, initial_balance,
        day_type, tails, etc.
    """
    if not ohlcv_1m or len(ohlcv_1m) < 10:
        return {}

    # Filter to RTH bars only (9:30–16:00 ET)
    rth_bars = []
    for c in ohlcv_1m:
        mins = _minutes_from_ts(c.get("timestamp", ""))
        if mins is not None and _RTH_START_MINUTES <= mins < _RTH_END_MINUTES:
            rth_bars.append({**c, "_minutes": mins})
    if len(rth_bars) < 10:
        return {}

    # Build per-period TPO
    periods: dict[str, dict] = {}
    for bar in rth_bars:
        letter = _assign_period(bar["_minutes"])
        if not letter:
            continue
        if letter not in periods:
            periods[letter] = {
                "letter": letter,
                "high": -1e9, "low": 1e9,
                "open": bar["open"], "close": bar["close"],
                "volume": 0, "bar_count": 0,
                "tpo_prices": set(),
            }
        p = periods[letter]
        p["high"] = max(p["high"], bar["high"])
        p["low"] = min(p["low"], bar["low"])
        p["close"] = bar["close"]
        p["volume"] += bar.get("volume", 0)
        p["bar_count"] += 1
        # TPO letter is placed at each price level touched (rounded to tick)
        tick = _infer_tick_size(bar.get("high", 0))
        price_level = round(bar["high"] / tick) * tick
        p["tpo_prices"].add(price_level)
        price_level_l = round(bar["low"] / tick) * tick
        p["tpo_prices"].add(price_level_l)

    if not periods:
        return {}

    # Sort by position in _TPO_PERIODS
    period_order = {p[0]: i for i, p in enumerate(_TPO_PERIODS)}
    sorted_letters = sorted(periods.keys(), key=lambda l: period_order.get(l, 99))

    # Overall profile range
    all_highs = [periods[l]["high"] for l in sorted_letters]
    all_lows = [periods[l]["low"] for l in sorted_letters]
    profile_high = max(all_highs)
    profile_low = min(all_lows)
    profile_range = profile_high - profile_low if profile_high > profile_low else 1.0

    # TPO count per price level (for POC)
    tpo_count: dict[float, int] = {}
    for l in sorted_letters:
        for price in periods[l]["tpo_prices"]:
            tpo_count[price] = tpo_count.get(price, 0) + 1

    # POC = price level with most TPO letters
    poc = max(tpo_count, key=tpo_count.get) if tpo_count else (profile_high + profile_low) / 2

    # Value Area (70% of TPOs around POC)
    total_tpos = sum(tpo_count.values())
    va_target = total_tpos * 0.70
    sorted_prices = sorted(tpo_count.keys())
    poc_idx = sorted_prices.index(poc) if poc in sorted_prices else len(sorted_prices) // 2

    cum = tpo_count.get(poc, 0)
    left = poc_idx - 1
    right = poc_idx + 1
    vah = poc
    val = poc
    while cum < va_target and (left >= 0 or right < len(sorted_prices)):
        next_left = tpo_count.get(sorted_prices[left], 0) if left >= 0 else 0
        next_right = tpo_count.get(sorted_prices[right], 0) if right < len(sorted_prices) else 0
        if next_left >= next_right and left >= 0:
            cum += next_left
            val = sorted_prices[left]
            left -= 1
        elif right < len(sorted_prices):
            cum += next_right
            vah = sorted_prices[right]
            right += 1
        else:
            break

    # Initial Balance (periods A-B)
    ib_high = -1e9
    ib_low = 1e9
    for l in sorted_letters:
        if l in _INITIAL_BALANCE_PERIODS:
            p = periods[l]
            ib_high = max(ib_high, p["high"])
            ib_low = min(ib_low, p["low"])
    ib_range = ib_high - ib_low if ib_high > ib_low else 0
    ib_mid = (ib_high + ib_low) / 2 if ib_high > ib_low else 0

    # Current position within profile
    last_close = rth_bars[-1]["close"]
    close_vs_poc = (last_close - poc) / max(poc, 0.01)
    close_above_poc = last_close > poc
    close_above_vah = last_close > vah
    close_below_val = last_close < val

    # Buying/selling tails
    tails = []
    for l in sorted_letters:
        p = periods[l]
        p_range = p["high"] - p["low"]
        if p_range <= 0:
            continue
        upper_tail = (p["high"] - max(p["open"], p["close"])) / p_range
        lower_tail = (min(p["open"], p["close"]) - p["low"]) / p_range
        if upper_tail > 0.50:
            tails.append({"period": l, "type": "selling", "extent": round(upper_tail, 2), "price": p["high"]})
        if lower_tail > 0.50:
            tails.append({"period": l, "type": "buying", "extent": round(lower_tail, 2), "price": p["low"]})

    # Failed auction detection: price rejected at VAH or VAL with tail
    failed_auction_at_vah = any(t["type"] == "selling" and t["price"] >= vah * 0.999 for t in tails) if tails else False
    failed_auction_at_val = any(t["type"] == "buying" and t["price"] <= val * 1.001 for t in tails) if tails else False

    # POC migration direction
    poc_migration = "neutral"
    if len(sorted_letters) >= 3:
        first_half = sorted_letters[:len(sorted_letters)//2]
        second_half = sorted_letters[len(sorted_letters)//2:]
        first_poc = max(tpo_count, key=lambda p: sum(1 for l in first_half if p in periods[l]["tpo_prices"]))
        second_poc = max(tpo_count, key=lambda p: sum(1 for l in second_half if p in periods[l]["tpo_prices"]))
        if second_poc > first_poc * 1.001:
            poc_migration = "up"
        elif second_poc < first_poc * 0.999:
            poc_migration = "down"

    # Day-type classification (simplified Dalton)
    day_type = _classify_day_type(periods, sorted_letters, profile_range, poc, ib_high, ib_low, prior_day_va)

    # Per-period TPO summary
    period_summaries = []
    for l in sorted_letters:
        p = periods[l]
        p_range = p["high"] - p["low"]
        period_summaries.append({
            "letter": l,
            "high": round(p["high"], 2),
            "low": round(p["low"], 2),
            "open": round(p["open"], 2),
            "close": round(p["close"], 2),
            "range": round(p_range, 2),
            "volume": p["volume"],
            "tpo_count": len(p["tpo_prices"]),
            "direction": "up" if p["close"] > p["open"] else "down" if p["close"] < p["open"] else "flat",
        })

    result = {
        "poc": round(poc, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "value_area_width": round(vah - val, 2),
        "ib_high": round(ib_high, 2),
        "ib_low": round(ib_low, 2),
        "ib_range": round(ib_range, 2),
        "ib_mid": round(ib_mid, 2),
        "profile_high": round(profile_high, 2),
        "profile_low": round(profile_low, 2),
        "profile_range": round(profile_range, 2),
        "close_above_poc": close_above_poc,
        "close_above_vah": close_above_vah,
        "close_below_val": close_below_val,
        "close_vs_poc": round(close_vs_poc, 6),
        "day_type": day_type,
        "poc_migration": poc_migration,
        "failed_auction_at_vah": failed_auction_at_vah,
        "failed_auction_at_val": failed_auction_at_val,
        "tails": tails,
        "tail_count": len(tails),
        "periods": period_summaries,
        "period_count": len(period_summaries),
        "total_tpos": total_tpos,
        "price_in_ib": ib_low <= last_close <= ib_high if ib_high > ib_low else True,
        "price_above_ib": last_close > ib_high if ib_high > 0 else False,
        "price_below_ib": last_close < ib_low if ib_low > 0 else False,
        "ib_breakout_direction": "up" if last_close > ib_high else "down" if last_close < ib_low else "inside",
        "in_value_area": val <= last_close <= vah,
        "source": "tpo_engine",
    }

    return result


def compute_volume_profile(candles_1m: list, num_bins: int = 25) -> dict:
    """Compute intraday volume profile from 1-min candles.

    Returns POC, VAH, VAL from volume distribution (not TPO).
    """
    if not candles_1m or len(candles_1m) < 5:
        return {}
    low = min(c["low"] for c in candles_1m)
    high = max(c["high"] for c in candles_1m)
    if high <= low:
        return {}
    bin_size = (high - low) / num_bins
    bins = {i: 0 for i in range(num_bins)}
    for c in candles_1m:
        avg = (c["high"] + c["low"]) / 2.0
        idx = min(int((avg - low) / bin_size), num_bins - 1)
        bins[idx] += c.get("volume", 0)
    if not bins or max(bins.values()) == 0:
        return {}
    poc_idx = max(bins, key=lambda k: bins[k])
    poc = low + (poc_idx + 0.5) * bin_size
    total_vol = sum(bins.values())
    vol_target = total_vol * 0.70
    sorted_idxs = sorted(bins.keys())
    poc_pos = sorted_idxs.index(poc_idx)
    cum_vol = bins[poc_idx]
    left = poc_pos - 1
    right = poc_pos + 1
    included = {poc_idx}
    while cum_vol < vol_target and (left >= 0 or right < len(sorted_idxs)):
        next_left = bins.get(sorted_idxs[left], 0) if left >= 0 else 0
        next_right = bins.get(sorted_idxs[right], 0) if right < len(sorted_idxs) else 0
        if next_left >= next_right and left >= 0:
            included.add(sorted_idxs[left])
            cum_vol += next_left
            left -= 1
        elif right < len(sorted_idxs):
            included.add(sorted_idxs[right])
            cum_vol += next_right
            right += 1
        else:
            break
    val_idx = min(included)
    vah_idx = max(included)
    val = low + val_idx * bin_size
    vah = low + (vah_idx + 1) * bin_size
    last_close = candles_1m[-1]["close"]
    return {
        "poc": round(poc, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "value_area_width": round(vah - val, 2),
        "close_vs_poc": round((last_close - poc) / poc, 6) if poc else 0,
        "close_above_poc": last_close > poc,
        "close_above_vah": last_close > vah,
        "close_below_val": last_close < val,
        "source": "volume_profile",
    }


def _infer_tick_size(price: float) -> float:
    """Infer tick size from price level."""
    if price < 5: return 0.01
    if price < 100: return 0.05
    if price < 1000: return 0.25
    if price < 10000: return 0.50
    return 1.0


def _classify_day_type(
    periods: dict, sorted_letters: list,
    profile_range: float, poc: float,
    ib_high: float, ib_low: float,
    prior_day_va: Optional[dict] = None,
) -> str:
    """Classify day type per Dalton framework.

    Types:
      - normal_day: Typical value-area development, neutral POC
      - balance_day: Tight range, no directional extension, POC centered
      - breakout_day: Initial balance break with extension, trending
      - neutral_day: No clear direction, overlapping periods
      - trend_day: Strong directional move, POC migrates with price
    """
    if profile_range <= 0 or len(sorted_letters) < 3:
        return "normal_day"

    # Check for IB breakout: price outside IB after first 2 periods
    if len(sorted_letters) >= 3:
        later_highs = [periods[l]["high"] for l in sorted_letters[2:]]
        later_lows = [periods[l]["low"] for l in sorted_letters[2:]]
        max_high = max(later_highs) if later_highs else 0
        min_low = min(later_lows) if later_lows else 0
        if ib_high > 0 and max_high > ib_high * (1 + _BREAKOUT_MIN_EXTENSION * profile_range / max(ib_high, 1)):
            return "breakout_day_up"
        if ib_low > 0 and min_low < ib_low * (1 - _BREAKOUT_MIN_EXTENSION * profile_range / max(ib_low, 1)):
            return "breakout_day_down"

    # Check POC centrality
    if profile_range > 0:
        all_highs = [periods[l]["high"] for l in sorted_letters]
        all_lows = [periods[l]["low"] for l in sorted_letters]
        mid = (max(all_highs) + min(all_lows)) / 2
        poc_disp = abs(poc - mid) / profile_range
        if poc_disp < _BALANCE_MAX_DELTA:
            # Check range width relative to ATR
            avg_range = sum(periods[l]["high"] - periods[l]["low"] for l in sorted_letters) / max(len(sorted_letters), 1)
            profile_vs_avg = profile_range / max(avg_range, 0.01)
            if profile_vs_avg < 2.0:
                return "balance_day"

    # Trend day: POC migrates strongly and price extends in one direction
    first_half_pocs = []
    second_half_pocs = []
    mid_point = len(sorted_letters) // 2
    for i, l in enumerate(sorted_letters):
        p = periods[l]
        avg = (p["high"] + p["low"]) / 2
        if i < mid_point:
            first_half_pocs.append(avg)
        else:
            second_half_pocs.append(avg)
    if first_half_pocs and second_half_pocs:
        first_avg = sum(first_half_pocs) / len(first_half_pocs)
        second_avg = sum(second_half_pocs) / len(second_half_pocs)
        trend_pct = abs(second_avg - first_avg) / max(first_avg, 0.01)
        if trend_pct > 0.005:  # 0.5% migration = trending
            return "trend_day_up" if second_avg > first_avg else "trend_day_down"

    # Prior day VA overlap for neutral classification
    if prior_day_va and prior_day_va.get("vah") and prior_day_va.get("val"):
        overlap = max(0, min(vah := prior_day_va["vah"], periods.get(sorted_letters[-1], {}).get("high", 0))
                      - max(val := prior_day_va["val"], periods.get(sorted_letters[-1], {}).get("low", 0)))
        overlap_pct = overlap / max(profile_range, 0.01)
        if overlap_pct > _NEUTRAL_DAY_MAX:
            return "neutral_day"

    return "normal_day"
