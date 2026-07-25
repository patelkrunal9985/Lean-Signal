"""
Level Engine — Centralized Key Level Computation + Entry Zone Optimization.

Computes all significant price levels for a ticker and provides:
  1. Fibonacci retracements & extensions from recent swing structure
  2. Aggregated key levels map (fib, prior HL, gamma flip/walls, VP, SMA)
  3. Suggested entry zone (better price than chasing at market)
  4. Level proximity analysis (how close to support/resistance?)
  5. Multi-level confluence scoring (how many levels agree at a zone?)

Integrates with entry_exit.py, consensus_coordinator.py, and runner.py
to add level-aware entry/exit logic across all instrument types.
"""
from __future__ import annotations
from typing import Any

from utils.logger import get_logger

logger = get_logger("engine.level_engine")

# ── Fibonacci retracement ratios ──
FIB_RETRACEMENTS = [0.236, 0.382, 0.5, 0.618, 0.786]
FIB_EXTENSIONS = [1.272, 1.618, 2.0, 2.618]

# ── Level proximity thresholds (as % of price) — per-instrument ──
AT_LEVEL_PCT = 0.0015      # Within 0.15% = "at" the level
NEAR_LEVEL_PCT = 0.005     # Within 0.5% = "near"
CLOSE_LEVEL_PCT = 0.015    # Within 1.5% = "approaching" (was 1.0%, too sensitive for futures)
ENTRY_ZONE_PCT = 0.008     # Within 0.8% = valid entry zone

# ── Level type priority (higher = more significant) ──
LEVEL_PRIORITY = {
    "gamma_flip": 10,       # Dealer reversal zone (highest)
    "gamma_wall": 9,        # Dealer concentration
    "prior_week_high": 8,
    "prior_week_low": 8,
    "prior_day_high": 7,
    "prior_day_low": 7,
    "fib_618": 6,           # Golden ratio
    "fib_50": 5,
    "fib_382": 5,
    "poc": 5,               # Volume profile POC
    "value_area_high": 4,
    "value_area_low": 4,
    "gex_magnet": 9,       # Dealer gamma concentration magnet (Phase 3)
    "sma_50": 4,
    "sma_20": 3,
    "fib_236": 3,
    "fib_786": 3,
    "fib_1272": 2,
    "fib_1618": 2,
    "pivot_r1": 2,
    "pivot_s1": 2,
    "fib_200": 1,
    "fib_2618": 1,
}

# ── Entry zone scoring weights ──
ENTRY_ZONE_WEIGHTS = {
    "level_confluence": 0.40,   # Multiple levels agreeing at same zone
    "level_priority": 0.30,     # How significant the level is
    "proximity": 0.20,          # How close is the level to current price?
    "trend_alignment": 0.10,    # Is the level in the trend direction?
}


def _find_recent_swing(ohlcv: list) -> tuple[float, float, str]:
    """Find the most recent significant swing high and swing low using pivot detection.

    A swing high is a bar whose high is higher than its 2 neighbors on each side.
    A swing low is a bar whose low is lower than its 2 neighbors on each side.
    Uses the last 20 bars. Falls back to simple max/min if no pivots found.

    Returns (swing_high, swing_low, direction_of_last_move).
    """
    if not ohlcv or len(ohlcv) < 5:
        return 0.0, 0.0, "none"

    lookback = min(len(ohlcv), 20)
    recent = ohlcv[-lookback:]

    # Extract highs and lows (zip to ensure equal lengths)
    highs = []
    lows = []
    closes = []
    for bar in recent:
        if isinstance(bar, dict):
            h = bar.get("high", 0) or 0
            l = bar.get("low", 0) or 0
            c = bar.get("close", 0) or 0
            if h > 0 and l > 0:
                highs.append(h)
                lows.append(l)
                closes.append(c)

    if len(highs) < 5 or len(lows) < 5:
        return 0.0, 0.0, "none"

    # ── Pivot detection: look for local maxima/minima ──
    swing_highs = []
    swing_lows = []
    for i in range(2, len(highs) - 2):
        # Swing high: higher than 2 neighbors on each side
        if highs[i] > max(highs[i-2], highs[i-1], highs[i+1], highs[i+2]):
            swing_highs.append(i)
        # Swing low: lower than 2 neighbors on each side
        if lows[i] < min(lows[i-2], lows[i-1], lows[i+1], lows[i+2]):
            swing_lows.append(i)

    # Fall back to simple max/min if no pivots detected
    if not swing_highs:
        swing_high = max(highs)
    else:
        # Use the most recent swing high
        swing_high = highs[swing_highs[-1]]

    if not swing_lows:
        swing_low = min(lows)
    else:
        swing_low = lows[swing_lows[-1]]

    if swing_high <= 0 or swing_low <= 0 or swing_high == swing_low:
        return 0.0, 0.0, "none"

    # Determine direction of last move
    last_close = closes[-1] if closes else 0
    mid = (swing_high + swing_low) / 2
    if last_close > mid:
        direction = "up"
    elif last_close < mid:
        direction = "down"
    else:
        direction = "none"

    return swing_high, swing_low, direction


def _find_extended_swing(ohlcv: list) -> tuple[float, float]:
    """Find a longer-term swing (50 bars) for extension projections."""
    if not ohlcv or len(ohlcv) < 20:
        return 0.0, 0.0

    lookback = min(len(ohlcv), 50)
    recent = ohlcv[-lookback:]

    highs = []
    lows = []
    for bar in recent:
        if isinstance(bar, dict):
            h = bar.get("high", 0) or 0
            l = bar.get("low", 0) or 0
            if h > 0 and l > 0:
                highs.append(h)
                lows.append(l)

    return max(highs) if highs else 0.0, min(lows) if lows else 0.0


def compute_fibonacci_levels(ohlcv: list, current_price: float) -> dict:
    """Compute Fibonacci retracement and extension levels.

    Retracements are computed from the recent swing structure.
    Extensions are computed from the extended swing for TP projection.

    Returns:
        {
            "retracements": {0.236: price, 0.382: price, ...},
            "extensions": {1.272: price, 1.618: price, ...},
            "swing_high": float,
            "swing_low": float,
            "trend_direction": "up"|"down"|"none",
        }
    """
    swing_high, swing_low, trend_dir = _find_recent_swing(ohlcv)
    if swing_high <= 0 or swing_low <= 0 or swing_high == swing_low:
        return {
            "retracements": {},
            "extensions": {},
            "swing_high": 0.0,
            "swing_low": 0.0,
            "trend_direction": "none",
        }

    price_range = swing_high - swing_low

    # ── Retracements ──
    retracements = {}
    for ratio in FIB_RETRACEMENTS:
        if trend_dir == "up":
            # In uptrend: retracement is a pullback from the high
            level = swing_high - (price_range * ratio)
        else:
            # In downtrend: retracement is a bounce from the low
            level = swing_low + (price_range * ratio)
        key = f"fib_{int(ratio * 1000)}"
        retracements[key] = round(level, 2)

    # ── Extensions (for TP projection) ──
    ext_high, ext_low = _find_extended_swing(ohlcv)
    if ext_high <= 0 or ext_low <= 0 or ext_high == ext_low:
        ext_range = price_range
        ext_high = swing_high
        ext_low = swing_low
    else:
        ext_range = ext_high - ext_low

    extensions = {}
    for ratio in FIB_EXTENSIONS:
        if trend_dir == "up":
            level = swing_low + (ext_range * ratio)
        else:
            level = swing_high - (ext_range * ratio)
        key = f"fib_{int(ratio * 1000)}"
        extensions[key] = round(level, 2)

    return {
        "retracements": retracements,
        "extensions": extensions,
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "trend_direction": trend_dir,
    }


def _get_prior_day_hl(ohlcv: list) -> tuple[float, float]:
    """Extract prior day's high and low."""
    if not ohlcv or len(ohlcv) < 2:
        return 0.0, 0.0
    prev = ohlcv[-2]
    if isinstance(prev, dict):
        return float(prev.get("high", 0) or 0), float(prev.get("low", 0) or 0)
    return 0.0, 0.0


def _get_prior_week_hl(ohlcv: list) -> tuple[float, float]:
    """Extract prior week's high and low (last 5 bars)."""
    if not ohlcv or len(ohlcv) < 6:
        return 0.0, 0.0
    week_bars = ohlcv[-6:-1]
    highs = [b.get("high", 0) for b in week_bars if isinstance(b, dict) and b.get("high", 0) > 0]
    lows = [b.get("low", 0) for b in week_bars if isinstance(b, dict) and b.get("low", 0) > 0]
    return (max(highs), min(lows)) if highs and lows else (0.0, 0.0)


def _get_pivot_levels(ohlcv: list) -> tuple[float, float]:
    """Compute pivot R1 and S1 from prior bar."""
    if not ohlcv or len(ohlcv) < 2:
        return 0.0, 0.0
    prev = ohlcv[-2]
    if not isinstance(prev, dict):
        return 0.0, 0.0
    ph = prev.get("high", 0) or 0
    pl = prev.get("low", 0) or 0
    pc = prev.get("close", 0) or 0
    if ph <= 0 or pl <= 0 or pc <= 0:
        return 0.0, 0.0
    pivot = (ph + pl + pc) / 3.0
    r1 = 2 * pivot - pl
    s1 = 2 * pivot - ph
    return round(r1, 2), round(s1, 2)


def _get_sma(data: dict, period: int) -> float:
    """Extract SMA from indicator data."""
    indicators = data.get("indicators", {})
    sma = indicators.get(f"sma_{period}", 0)
    return float(sma or 0)


def _get_vp_levels(data: dict) -> dict:
    """Extract volume profile levels."""
    vp = data.get("volume_profile", data.get("vp", {}))
    return {
        "poc": float(vp.get("poc", 0) or 0),
        "vah": float(vp.get("value_area_high", vp.get("vah", 0)) or 0),
        "val": float(vp.get("value_area_low", vp.get("val", 0)) or 0),
    }


def aggregate_key_levels(
    ticker: str,
    data: dict,
    current_price: float,
    direction: str = "neutral",
    instr_type: str = "stock",
) -> dict:
    """Aggregate ALL key levels into a unified map with priorities and types.

    This is the central function that every other module calls to get the full
    picture of significant price levels.

    Args:
        ticker: Symbol
        data: Full ticker data dict
        current_price: Current price
        direction: Signal direction for entry zone computation
        instr_type: 'stock', 'future', or 'option'

    Returns:
        {
            "levels": [{price, type, priority, label, side}, ...],
            "nearest_support": float | None,
            "nearest_resistance": float | None,
            "support_levels": [{price, type, priority, label}, ...],
            "resistance_levels": [{price, type, priority, label}, ...],
            "fibonacci": {retracements, extensions, swing_high, swing_low},
            "suggested_entry": float | None,
            "suggested_entry_type": str,
            "entry_zone_quality": float (0-1),
            "proximity_warning": str | None,
            "confluence_zones": [{price, count, types}, ...],
            "at_level": str | None,
        }
    """
    if current_price <= 0:
        return _empty_level_result()

    ohlcv = data.get("ohlcv", [])
    indicators = data.get("indicators", {})

    all_levels: list[dict] = []

    # ── 1. Fibonacci levels ──
    fib = compute_fibonacci_levels(ohlcv, current_price)
    for ratio_key, price in fib.get("retracements", {}).items():
        if price > 0:
            priority = LEVEL_PRIORITY.get(ratio_key, 3)
            side = _classify_level(price, current_price)
            all_levels.append({
                "price": price, "type": ratio_key,
                "priority": priority, "label": _fib_label(ratio_key),
                "side": side, "source": "fibonacci",
            })
    for ratio_key, price in fib.get("extensions", {}).items():
        if price > 0:
            priority = LEVEL_PRIORITY.get(ratio_key, 2)
            side = _classify_level(price, current_price)
            all_levels.append({
                "price": price, "type": ratio_key,
                "priority": priority, "label": _fib_label(ratio_key),
                "side": side, "source": "fibonacci",
            })

    # ── 2. Prior day high/low ──
    pd_high, pd_low = _get_prior_day_hl(ohlcv)
    if pd_high > 0:
        all_levels.append({
            "price": pd_high, "type": "prior_day_high",
            "priority": LEVEL_PRIORITY["prior_day_high"],
            "label": "Prior Day High",
            "side": "resistance", "source": "price_structure",
        })
    if pd_low > 0:
        all_levels.append({
            "price": pd_low, "type": "prior_day_low",
            "priority": LEVEL_PRIORITY["prior_day_low"],
            "label": "Prior Day Low",
            "side": "support", "source": "price_structure",
        })

    # ── 3. Prior week high/low ──
    pw_high, pw_low = _get_prior_week_hl(ohlcv)
    if pw_high > 0:
        all_levels.append({
            "price": pw_high, "type": "prior_week_high",
            "priority": LEVEL_PRIORITY["prior_week_high"],
            "label": "Prior Week High",
            "side": "resistance", "source": "price_structure",
        })
    if pw_low > 0:
        all_levels.append({
            "price": pw_low, "type": "prior_week_low",
            "priority": LEVEL_PRIORITY["prior_week_low"],
            "label": "Prior Week Low",
            "side": "support", "source": "price_structure",
        })

    # ── 4. Pivot levels ──
    r1, s1 = _get_pivot_levels(ohlcv)
    if r1 > 0:
        all_levels.append({
            "price": r1, "type": "pivot_r1",
            "priority": LEVEL_PRIORITY["pivot_r1"],
            "label": "Pivot R1",
            "side": "resistance", "source": "price_structure",
        })
    if s1 > 0:
        all_levels.append({
            "price": s1, "type": "pivot_s1",
            "priority": LEVEL_PRIORITY["pivot_s1"],
            "label": "Pivot S1",
            "side": "support", "source": "price_structure",
        })

    # ── 5. Gamma flip level (cross-populated from options) ──
    gamma_flip = data.get("gamma_flip_level", 0) or 0
    if gamma_flip > 0:
        side = _classify_level(gamma_flip, current_price)
        all_levels.append({
            "price": gamma_flip, "type": "gamma_flip",
            "priority": LEVEL_PRIORITY["gamma_flip"],
            "label": "Gamma Flip (Dealer Reversal)",
            "side": side, "source": "options_gex",
        })

    # ── 6. Gamma walls ──
    gamma_walls = data.get("gamma_walls", []) or []
    for w in gamma_walls:
        strike = w.get("strike", 0) or 0
        if strike > 0:
            side = _classify_level(strike, current_price)
            all_levels.append({
                "price": strike, "type": "gamma_wall",
                "priority": LEVEL_PRIORITY["gamma_wall"],
                "label": f"Gamma Wall {strike}",
                "side": side, "source": "options_gex",
            })

    # ── 7. Volume profile levels ──
    vp = _get_vp_levels(data)
    if vp["poc"] > 0:
        side = _classify_level(vp["poc"], current_price)
        all_levels.append({
            "price": vp["poc"], "type": "poc",
            "priority": LEVEL_PRIORITY["poc"],
            "label": "Volume POC",
            "side": side, "source": "volume_profile",
        })
    if vp["vah"] > 0:
        side = _classify_level(vp["vah"], current_price)
        all_levels.append({
            "price": vp["vah"], "type": "value_area_high",
            "priority": LEVEL_PRIORITY["value_area_high"],
            "label": "VA High",
            "side": side, "source": "volume_profile",
        })
    if vp["val"] > 0:
        side = _classify_level(vp["val"], current_price)
        all_levels.append({
            "price": vp["val"], "type": "value_area_low",
            "priority": LEVEL_PRIORITY["value_area_low"],
            "label": "VA Low",
            "side": side, "source": "volume_profile",
        })

    # ── 8. GEX price magnet (from gamma_flip_levels / option_metrics) ──
    gex_magnet = data.get("gex_magnet")
    if gex_magnet and isinstance(gex_magnet, dict):
        magnet_strike = gex_magnet.get("strike", 0)
        if magnet_strike > 0:
            side = _classify_level(magnet_strike, current_price)
            magnet_dir = gex_magnet.get("direction", "neutral")
            all_levels.append({
                "price": magnet_strike, "type": "gex_magnet",
                "priority": LEVEL_PRIORITY["gex_magnet"],
                "label": f"GEX Magnet ({magnet_dir})",
                "side": side, "source": "options_gex",
            })

    # ── 9. SMA levels ──
    sma_20 = _get_sma(data, 20)
    sma_50 = _get_sma(data, 50)
    if sma_20 > 0:
        side = _classify_level(sma_20, current_price)
        all_levels.append({
            "price": sma_20, "type": "sma_20",
            "priority": LEVEL_PRIORITY["sma_20"],
            "label": "SMA 20",
            "side": side, "source": "moving_average",
        })
    if sma_50 > 0:
        side = _classify_level(sma_50, current_price)
        all_levels.append({
            "price": sma_50, "type": "sma_50",
            "priority": LEVEL_PRIORITY["sma_50"],
            "label": "SMA 50",
            "side": side, "source": "moving_average",
        })

    # ── Sort by price ──
    all_levels.sort(key=lambda l: l["price"])

    # ── Separate support and resistance relative to current price ──
    supports = [l for l in all_levels if l["price"] < current_price]
    resistances = [l for l in all_levels if l["price"] > current_price]

    # Sort supports descending (nearest first), resistances ascending
    supports.sort(key=lambda l: -l["price"])
    resistances.sort(key=lambda l: l["price"])

    nearest_support = supports[0]["price"] if supports else None
    nearest_resistance = resistances[0]["price"] if resistances else None

    # ── Confluence zones (levels within 0.3% of each other) ──
    confluence_zones = _find_confluence_zones(all_levels, current_price)

    # ── "At level" detection ──
    at_level = None
    for lvl in all_levels:
        dist_pct = abs(current_price - lvl["price"]) / max(current_price, 0.01)
        if dist_pct <= AT_LEVEL_PCT:
            at_level = lvl["type"]
            break

    # ── Proximity warning ──
    proximity_warning = _compute_proximity_warning(
        direction, current_price, nearest_support, nearest_resistance,
    )

    # ── Suggested entry zone ──
    suggested_entry, suggested_entry_type, entry_zone_quality = _compute_suggested_entry(
        direction, current_price, supports, resistances, confluence_zones, instr_type,
    )

    return {
        "levels": all_levels,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "support_levels": supports[:5],   # Top 5 nearest supports
        "resistance_levels": resistances[:5],  # Top 5 nearest resistances
        "fibonacci": fib,
        "suggested_entry": suggested_entry,
        "suggested_entry_type": suggested_entry_type,
        "entry_zone_quality": round(entry_zone_quality, 4),
        "proximity_warning": proximity_warning,
        "confluence_zones": confluence_zones[:5],
        "at_level": at_level,
    }


def _empty_level_result() -> dict:
    return {
        "levels": [],
        "nearest_support": None,
        "nearest_resistance": None,
        "support_levels": [],
        "resistance_levels": [],
        "fibonacci": {"retracements": {}, "extensions": {}, "swing_high": 0, "swing_low": 0, "trend_direction": "none"},
        "suggested_entry": None,
        "suggested_entry_type": "none",
        "entry_zone_quality": 0.0,
        "proximity_warning": None,
        "confluence_zones": [],
        "at_level": None,
    }


def _classify_level(price: float, current_price: float) -> str:
    """Classify whether a level is support or resistance relative to current price."""
    if price < current_price * 0.999:
        return "support"
    elif price > current_price * 1.001:
        return "resistance"
    return "at_price"


def _fib_label(ratio_key: str) -> str:
    """Create human-readable Fibonacci label."""
    mapping = {
        "fib_236": "Fib 23.6%", "fib_382": "Fib 38.2%", "fib_50": "Fib 50%",
        "fib_618": "Fib 61.8%", "fib_786": "Fib 78.6%",
        "fib_1272": "Fib 127.2%", "fib_1618": "Fib 161.8%",
        "fib_2000": "Fib 200%", "fib_2618": "Fib 261.8%",
    }
    return mapping.get(ratio_key, ratio_key)


def _find_confluence_zones(levels: list[dict], current_price: float) -> list[dict]:
    """Group levels that cluster within 0.3% of each other into confluence zones."""
    if len(levels) < 2:
        return []

    zones = []
    used = set()

    for i, lvl1 in enumerate(levels):
        if i in used:
            continue
        cluster = [lvl1]
        for j, lvl2 in enumerate(levels):
            if j <= i or j in used:
                continue
            dist_pct = abs(lvl1["price"] - lvl2["price"]) / max(current_price, 0.01)
            if dist_pct <= 0.003:
                cluster.append(lvl2)
                used.add(j)

        if len(cluster) >= 2:
            used.add(i)
            avg_price = sum(l["price"] for l in cluster) / len(cluster)
            zones.append({
                "price": round(avg_price, 2),
                "count": len(cluster),
                "types": [l["type"] for l in cluster],
                "max_priority": max(l["priority"] for l in cluster),
                "distance_pct": round(abs(avg_price - current_price) / max(current_price, 0.01) * 100, 2),
            })

    zones.sort(key=lambda z: -z["count"])  # Most confluence first
    return zones


def _compute_proximity_warning(
    direction: str,
    current_price: float,
    nearest_support: float | None,
    nearest_resistance: float | None,
) -> str | None:
    """Generate a proximity warning if entry at current price is risky.

    If we want to go LONG but we're very close to resistance → warn.
    If we want to go SHORT but we're very close to support → warn.
    """
    if direction == "long" and nearest_resistance is not None:
        dist_pct = (nearest_resistance - current_price) / max(current_price, 0.01)
        if dist_pct <= NEAR_LEVEL_PCT:
            if dist_pct <= AT_LEVEL_PCT:
                return f"AT_RESISTANCE_{nearest_resistance:.2f}_risk_of_rejection"
            return f"NEAR_RESISTANCE_{nearest_resistance:.2f}_{dist_pct:.2%}_away"
        if dist_pct <= CLOSE_LEVEL_PCT:
            return f"approaching_resistance_{nearest_resistance:.2f}_consider_limit_entry"

    if direction == "short" and nearest_support is not None:
        dist_pct = (current_price - nearest_support) / max(current_price, 0.01)
        if dist_pct <= NEAR_LEVEL_PCT:
            if dist_pct <= AT_LEVEL_PCT:
                return f"AT_SUPPORT_{nearest_support:.2f}_risk_of_bounce"
            return f"NEAR_SUPPORT_{nearest_support:.2f}_{dist_pct:.2%}_away"
        if dist_pct <= CLOSE_LEVEL_PCT:
            return f"approaching_support_{nearest_support:.2f}_consider_limit_entry"

    return None


def _compute_suggested_entry(
    direction: str,
    current_price: float,
    supports: list[dict],
    resistances: list[dict],
    confluence_zones: list[dict],
    instr_type: str,
) -> tuple[float | None, str, float]:
    """Compute the best suggested entry level and its quality score.

    For LONG: find the nearest support/confluence zone below current price.
    For SHORT: find the nearest resistance/confluence zone above current price.

    Returns (suggested_price, entry_type, quality_score).
    """
    if direction not in ("long", "short"):
        return None, "neutral", 0.0

    if direction == "long":
        candidates = supports.copy()
        # Also consider confluence zones below price
        for cz in confluence_zones:
            if cz["price"] < current_price * 0.999:
                candidates.append({
                    "price": cz["price"], "type": "confluence_zone",
                    "priority": cz["max_priority"],
                    "label": f"Confluence ({', '.join(cz['types'][:3])})",
                    "side": "support", "source": "confluence",
                })
    else:
        candidates = resistances.copy()
        for cz in confluence_zones:
            if cz["price"] > current_price * 1.001:
                candidates.append({
                    "price": cz["price"], "type": "confluence_zone",
                    "priority": cz["max_priority"],
                    "label": f"Confluence ({', '.join(cz['types'][:3])})",
                    "side": "resistance", "source": "confluence",
                })

    if not candidates:
        return current_price, "market", 0.3  # No better level found

    # Score each candidate
    best_score = 0.0
    best_entry = current_price
    best_type = "market"

    for c in candidates:
        dist_pct = abs(current_price - c["price"]) / max(current_price, 0.01)

        # Only consider levels within reasonable distance (2% for stocks, 3% for futures/options)
        max_dist = 0.03 if instr_type in ("future", "option") else 0.02
        if dist_pct > max_dist:
            continue

        # Scoring:
        # - Priority bonus: higher priority level → better
        priority_score = min(c.get("priority", 1) / 10.0, 0.30)

        # - Proximity bonus: closer level → better (but not at-level, that's risky)
        if dist_pct <= AT_LEVEL_PCT:
            proximity_score = 0.10  # Too close = risky
        elif dist_pct <= NEAR_LEVEL_PCT:
            proximity_score = 0.20  # Good entry zone
        elif dist_pct <= CLOSE_LEVEL_PCT:
            proximity_score = 0.15
        else:
            proximity_score = max(0, 0.10 - dist_pct * 2)  # Decays with distance

        # - Confluence bonus: zone with multiple levels → much better
        confluence_bonus = 0.0
        if c.get("source") == "confluence":
            confluence_bonus = 0.30
        elif c.get("type") in ("gamma_flip", "fib_618", "fib_50", "poc"):
            confluence_bonus = 0.10  # High-significance single level

        score = priority_score + proximity_score + confluence_bonus

        if score > best_score:
            best_score = score
            best_entry = c["price"]
            best_type = c.get("type", "level")

    quality = best_score

    # If the suggested entry is better than current price (for longs: lower entry),
    # return it. Otherwise, default to market.
    if direction == "long" and best_entry >= current_price * 0.995:
        # Suggested entry is at or above current price — not useful
        return current_price, "market", 0.3
    if direction == "short" and best_entry <= current_price * 1.005:
        return current_price, "market", 0.3

    return round(best_entry, 2), best_type, quality


def compute_level_confluence_boost(
    direction: str,
    current_price: float,
    key_levels: dict,
) -> tuple[float, str]:
    """Compute a confidence boost/penalty based on level confluence.

    Used by consensus_coordinator.py to adjust signal confidence.

    Returns (multiplier, reason_label).
    - >1.0 = boost (multiple levels support the signal direction)
    - <1.0 = penalty (levels are against the signal)
    - 1.0 = neutral
    """
    if direction not in ("long", "short") or not key_levels:
        return 1.0, "no_levels"

    nearest_support = key_levels.get("nearest_support")
    nearest_resistance = key_levels.get("nearest_resistance")
    confluence_zones = key_levels.get("confluence_zones", [])
    at_level = key_levels.get("at_level")

    # ── Check for strong confluence in the signal direction ──
    signal_confluence = 0
    for cz in confluence_zones:
        if direction == "long" and cz["price"] < current_price:
            signal_confluence += cz["count"]
        elif direction == "short" and cz["price"] > current_price:
            signal_confluence += cz["count"]

    # ── Calculate boost ──
    boost = 1.0
    reasons = []

    # Strong confluence in signal direction = big boost
    if signal_confluence >= 3:
        boost = 1.15
        reasons.append(f"strong_confluence_{signal_confluence}")
    elif signal_confluence >= 2:
        boost = 1.08
        reasons.append(f"moderate_confluence_{signal_confluence}")

    # At a key level in signal direction = confirmation
    if at_level:
        if (direction == "long" and nearest_support and
                abs(current_price - nearest_support) / max(current_price, 0.01) <= AT_LEVEL_PCT):
            boost = max(boost, 1.10)
            reasons.append("at_support_long")
        elif (direction == "short" and nearest_resistance and
                abs(current_price - nearest_resistance) / max(current_price, 0.01) <= AT_LEVEL_PCT):
            boost = max(boost, 1.10)
            reasons.append("at_resistance_short")

    # Very close to counter-directional level = penalty
    if direction == "long" and nearest_resistance:
        dist_pct = (nearest_resistance - current_price) / max(current_price, 0.01)
        if dist_pct <= AT_LEVEL_PCT:
            boost = min(boost, 0.80)
            reasons.append("at_resistance_while_long")
        elif dist_pct <= NEAR_LEVEL_PCT:
            boost = min(boost, 0.90)
            reasons.append("near_resistance_while_long")

    if direction == "short" and nearest_support:
        dist_pct = (current_price - nearest_support) / max(current_price, 0.01)
        if dist_pct <= AT_LEVEL_PCT:
            boost = min(boost, 0.80)
            reasons.append("at_support_while_short")
        elif dist_pct <= NEAR_LEVEL_PCT:
            boost = min(boost, 0.90)
            reasons.append("near_support_while_short")

    reason_label = "|".join(reasons) if reasons else "neutral"
    return round(boost, 4), reason_label
