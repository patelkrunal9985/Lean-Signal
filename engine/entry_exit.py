"""
Entry/Exit Level Computation -- Centralized SL/TP engine.

Per-instrument-type logic:
  Futures  -> ATR + depth + gamma flip (if cross-populated from options)
  Stocks   -> ATR + SMA levels + recent swing points
  Options  -> Gamma flip levels + single-leg ATM premium + gamma walls

Output for every signal:
  { entry_price, stop_loss, take_profit, suggested_entry,
    option_entry_premium, option_sl_premium, option_tp_premium,
    nearest_support, nearest_resistance, proximity_warning }

Level awareness is powered by engine/level_engine.py (Fibonacci,
prior HL, gamma flip/walls, VP, SMA, confluence zones).
"""
from __future__ import annotations
import math
from typing import Any

from utils.logger import get_logger

logger = get_logger("engine.entry_exit")

# -- Futures tick sizes for price rounding --
FUTURES_TICK_SIZES: dict[str, float] = {
    "ES=F": 0.25,
    "NQ=F": 0.25,
    "YM=F": 1.0,
    "RTY=F": 0.10,
    "GC=F": 0.10, "MGC=F": 0.10,
    "SI=F": 0.005,
    "CL=F": 0.01, "MCL=F": 0.01,
    "NG=F": 0.001,
    "HO=F": 0.0001, "RB=F": 0.0001,
}

DEFAULT_TICK = 0.01
MAX_RISK_REWARD = 99.0  # cap to prevent absurd ratios

# -- ATR multipliers --
FUTURE_SL_ATR_MULT = 0.75   # tighter for futures (intraday)
FUTURE_TP_ATR_MULT = 1.50
STOCK_SL_ATR_MULT = 1.0
STOCK_TP_ATR_MULT = 1.5
OPTION_SL_ATR_MULT = 0.5    # tighter for options (gamma flip primary)
OPTION_TP_ATR_MULT = 1.0

# -- Floor percentages (min SL / TP as % of entry) --
FUTURE_SL_FLOOR_PCT = 0.003   # 0.3%
FUTURE_TP_FLOOR_PCT = 0.005   # 0.5%
STOCK_SL_FLOOR_PCT = 0.005
STOCK_TP_FLOOR_PCT = 0.008
OPTION_SL_FLOOR_PCT = 0.002
OPTION_TP_FLOOR_PCT = 0.003

# -- Option premium multipliers (applied to SINGLE-LEG entry premium) --
OPTION_SL_PREMIUM_MULT = 0.50   # SL when premium drops to 50% of entry
OPTION_TP_PREMIUM_MULT = 2.50   # TP at 2.5x entry premium (0DTE gamma payoff)

# -- 0DTE ATR tightening -- 
# Gamma scales as 1/sqrt(t); at 0DTE price swings are more violent per tick
# so we scale the ATR stop multiplier by sqrt(dte/1) when dte < 1
OPTION_MIN_DTE_FOR_FULL_ATR = 1.0


def _extract_atr(data: dict) -> float:
    """Extract ATR from indicators, with fallback computation."""
    indicators = data.get("indicators", {})
    atr = float(indicators.get("atr_14", indicators.get("atr", 0)) or 0)
    if atr > 0:
        return atr
    # Fallback: compute from OHLCV
    ohlcv = data.get("ohlcv", [])
    if ohlcv and len(ohlcv) >= 15:
        trs = []
        for i in range(1, min(15, len(ohlcv))):
            h = ohlcv[-i].get("high", 0) if isinstance(ohlcv[-i], dict) else ohlcv[-i]
            l = ohlcv[-i].get("low", 0) if isinstance(ohlcv[-i], dict) else ohlcv[-i]
            pc = ohlcv[-i-1].get("close", 0) if isinstance(ohlcv[-i-1], dict) else ohlcv[-i-1]
            h, l, pc = float(h or 0), float(l or 0), float(pc or 0)
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        if trs:
            return sum(trs) / len(trs)
    return 0.0


def _round_to_tick(ticker: str, price: float) -> float:
    tick = FUTURES_TICK_SIZES.get(ticker, DEFAULT_TICK)
    return round(price / tick) * tick


def _get_sma(data: dict, period: int) -> float:
    """Extract SMA from indicators."""
    indicators = data.get("indicators", {})
    sma = indicators.get(f"sma_{period}", 0)
    return float(sma or 0)


def _get_depth_imbalance(depth_data: dict) -> dict:
    """Extract order book imbalance from depth data."""
    if not depth_data:
        return {}
    bids = depth_data.get("bids", depth_data.get("bid", []))
    asks = depth_data.get("asks", depth_data.get("ask", []))
    bid_vol = sum(float(b.get("size", 0) or b[1] if isinstance(b, (list, tuple)) else 0) for b in (bids if isinstance(bids, list) else [bids])[:5])
    ask_vol = sum(float(a.get("size", 0) or a[1] if isinstance(a, (list, tuple)) else 0) for a in (asks if isinstance(asks, list) else [asks])[:5])
    if bid_vol + ask_vol > 0:
        ratio = bid_vol / max(ask_vol, 1)
        if ratio > 1.3:
            return {"direction": "bullish", "bid_vol": bid_vol, "ask_vol": ask_vol, "ratio": ratio}
        if ratio < 0.7:
            return {"direction": "bearish", "bid_vol": bid_vol, "ask_vol": ask_vol, "ratio": ratio}
    return {"direction": "neutral", "bid_vol": bid_vol, "ask_vol": ask_vol}


def _get_swing_points(ohlcv: list, direction: str, lookback: int = 10) -> float:
    """Get nearest swing high/low from OHLCV."""
    if not ohlcv or len(ohlcv) < lookback:
        return 0.0
    recent = ohlcv[-lookback:]
    if direction == "long":
        highs = [float(c.get("high", 0) or 0) for c in recent if isinstance(c, dict)]
        return max(highs) if highs else 0.0
    else:
        lows = [float(c.get("low", 0) or 0) for c in recent if isinstance(c, dict)]
        return min(lows) if lows else 0.0


def _clamp_rr(rr: float) -> float:
    """Cap risk/reward to a sane maximum (no floor -- 0.0 is valid)."""
    return min(rr, MAX_RISK_REWARD)


def _get_tod_tightness() -> float:
    """Get time-of-day stop tightness, or 1.0 if module unavailable."""
    try:
        from engine.time_of_day import get_stop_tightness
        return get_stop_tightness()
    except ImportError:
        return 1.0


# ================================================================
# Per-instrument-type computation
# ================================================================

def compute_futures_levels(ticker: str, data: dict, direction: str,
                           confidence: float) -> dict:
    """Compute entry/exit levels for a futures signal.

    NOTE: gamma_flip_level and gamma_walls are only available when
    runner.py cross-populates futures data from SPY/QQQ option metrics.
    When absent, we fall through to ATR + depth + floor.
    """
    entry = float(data.get("current_price", 0))
    if entry <= 0:
        return {}

    atr = _extract_atr(data)
    depth_info = _get_depth_imbalance(data.get("depth", {}))

    # -- Stop loss candidates --
    sl_candidates: list[tuple[str, float]] = []

    # 1. Gamma flip (cross-populated from options -- see runner.py)
    gamma_flip = data.get("gamma_flip_level", 0) or 0
    if gamma_flip > 0:
        if direction == "long" and gamma_flip < entry:
            sl_candidates.append(("gamma_flip", gamma_flip))
        elif direction == "short" and gamma_flip > entry:
            sl_candidates.append(("gamma_flip", gamma_flip))

    # 2. ATR-based
    if atr > 0:
        if direction == "long":
            sl_candidates.append(("atr", entry - atr * FUTURE_SL_ATR_MULT))
        else:
            sl_candidates.append(("atr", entry + atr * FUTURE_SL_ATR_MULT))

    # 3. Depth imbalance -- tighten SL if book is against us
    if depth_info and depth_info.get("direction") != "neutral":
        if direction == "long" and depth_info["direction"] == "bearish":
            if depth_info.get("ask_vol", 0) > depth_info.get("bid_vol", 0) * 1.5:
                sl_candidates.append(("depth", entry * 0.997))
        elif direction == "short" and depth_info["direction"] == "bullish":
            if depth_info.get("bid_vol", 0) > depth_info.get("ask_vol", 0) * 1.5:
                sl_candidates.append(("depth", entry * 1.003))

    # 4. Floor
    if direction == "long":
        sl_candidates.append(("floor", entry - entry * FUTURE_SL_FLOOR_PCT))
    else:
        sl_candidates.append(("floor", entry + entry * FUTURE_SL_FLOOR_PCT))

    if direction == "long":
        sl = max(sl_candidates, key=lambda c: c[1])[1]
    else:
        sl = min(sl_candidates, key=lambda c: c[1])[1]
    sl = _round_to_tick(ticker, sl)

    # -- Take profit candidates --
    tp_candidates: list[tuple[str, float]] = []

    # 1. Gamma wall (cross-populated from options)
    gamma_walls = data.get("gamma_walls", []) or []
    if gamma_walls:
        if direction == "long":
            valid = [w["strike"] for w in gamma_walls if w["strike"] > entry]
            if valid:
                tp_candidates.append(("gamma_wall", min(valid)))
        else:
            valid = [w["strike"] for w in gamma_walls if w["strike"] < entry]
            if valid:
                tp_candidates.append(("gamma_wall", max(valid)))

    # 2. SMA level (acting as target)
    sma_20 = _get_sma(data, 20)
    if sma_20 > 0:
        if direction == "long" and sma_20 > entry:
            tp_candidates.append(("sma_20", sma_20))
        elif direction == "short" and sma_20 < entry:
            tp_candidates.append(("sma_20", sma_20))

    # 3. ATR-based
    if atr > 0:
        if direction == "long":
            tp_candidates.append(("atr", entry + atr * FUTURE_TP_ATR_MULT))
        else:
            tp_candidates.append(("atr", entry - atr * FUTURE_TP_ATR_MULT))

    # 4. Floor
    if direction == "long":
        tp_candidates.append(("floor", entry + entry * FUTURE_TP_FLOOR_PCT))
    else:
        tp_candidates.append(("floor", entry - entry * FUTURE_TP_FLOOR_PCT))

    if direction == "long":
        tp = min(tp_candidates, key=lambda c: c[1])[1]
    else:
        tp = max(tp_candidates, key=lambda c: c[1])[1]
    tp = _round_to_tick(ticker, tp)

    # -- Risk/reward --
    rr = _clamp_rr(abs(tp - entry) / max(abs(sl - entry), 0.01))
    entry_rounded = _round_to_tick(ticker, entry)

    # ── Level engine: override SL/TP with nearest meaningful support/resistance ──
    # This replaces the raw ATR-based SL with a level-aware stop that sits
    # BEYOND the nearest support (for longs) or resistance (for shorts),
    # preventing stop-outs at noise levels while keeping risk defined.
    suggested_entry = entry_rounded
    suggested_entry_type = "market"
    nearest_support = None
    nearest_resistance = None
    proximity_warning = None
    level_sl = None
    level_tp = None
    sl_method_used = "atr+depth" + ("+gamma_flip" if gamma_flip > 0 else "")
    tp_method_used = "atr+sma" + ("+gamma_wall" if gamma_walls else "")
    try:
        from engine.level_engine import aggregate_key_levels
        levels = aggregate_key_levels(ticker, data, entry, direction, "future")
        if levels.get("suggested_entry") and levels["suggested_entry"] != entry_rounded:
            suggested_entry = levels["suggested_entry"]
            suggested_entry_type = levels.get("suggested_entry_type", "level")
        nearest_support = levels.get("nearest_support")
        nearest_resistance = levels.get("nearest_resistance")
        proximity_warning = levels.get("proximity_warning")
        # Level-aware SL: place stop BEYOND nearest support (longs) / resistance (shorts)
        if direction == "long" and nearest_support:
            atr_sl = entry - atr * FUTURE_SL_ATR_MULT if atr > 0 else entry * 0.997
            # Use the tighter of: nearest support vs ATR-based SL
            # Stop goes BELOW support, not at it
            level_based_sl = nearest_support - max(atr * 0.15, 0.05)
            sl = max(level_based_sl, atr_sl)  # Wider stop = more room
            if level_based_sl > atr_sl:
                sl_method_used = "level_support"
        if direction == "short" and nearest_resistance:
            atr_sl = entry + atr * FUTURE_SL_ATR_MULT if atr > 0 else entry * 1.003
            level_based_sl = nearest_resistance + max(atr * 0.15, 0.05)
            sl = min(level_based_sl, atr_sl)
            if level_based_sl < atr_sl:
                sl_method_used = "level_resistance"
        # Level-aware TP: target next confluence zone or gamma wall
        if direction == "long" and levels.get("resistance_levels"):
            nearest_res = levels["resistance_levels"][0]
            if nearest_res["price"] > entry:
                level_tp = nearest_res["price"]
                tp = min(tp, level_tp)  # Don't exceed the nearest resistance
                tp_method_used = "level_first_resistance"
        if direction == "short" and levels.get("support_levels"):
            nearest_sup = levels["support_levels"][0]
            if nearest_sup["price"] < entry:
                level_tp = nearest_sup["price"]
                tp = max(tp, level_tp)
                tp_method_used = "level_first_support"
        # Recompute R:R with level-adjusted SL/TP
        rr = _clamp_rr(abs(tp - entry) / max(abs(sl - entry), 0.01))
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        logger.debug("Level engine unavailable for %s: %s", ticker, e)

    logger.debug(
        "Futures %s: entry=%.2f sl=%.2f tp=%.2f rr=%.1f atr=%.2f gamma_flip=%.2f sl_method=%s suggested=%s",
        ticker, entry_rounded, sl, tp, rr, atr, gamma_flip, sl_method_used,
        f"{suggested_entry}({suggested_entry_type})" if suggested_entry != entry_rounded else "market",
    )

    return {
        "entry_price": round(entry_rounded, 4),
        "stop_loss": round(sl, 4),
        "take_profit": round(tp, 4),
        "risk_reward": round(rr, 2),
        "suggested_entry": round(suggested_entry, 4),
        "suggested_entry_type": suggested_entry_type,
        "nearest_support": round(nearest_support, 4) if nearest_support else None,
        "nearest_resistance": round(nearest_resistance, 4) if nearest_resistance else None,
        "proximity_warning": proximity_warning,
        "sl_method": sl_method_used,
        "tp_method": tp_method_used,
    }


def compute_stock_levels(ticker: str, data: dict, direction: str,
                         confidence: float) -> dict:
    """Compute entry/exit levels for a stock signal."""
    entry = float(data.get("current_price", 0))
    if entry <= 0:
        return {}

    atr = _extract_atr(data)
    depth_info = _get_depth_imbalance(data.get("depth", {}))

    # -- Stop loss candidates --
    sl_candidates: list[tuple[str, float]] = []

    # 1. SMA level (strong support/resistance)
    sma_20 = _get_sma(data, 20)
    if sma_20 > 0:
        if direction == "long" and sma_20 < entry:
            sl_candidates.append(("sma_20", sma_20))
        elif direction == "short" and sma_20 > entry:
            sl_candidates.append(("sma_20", sma_20))

    # 2. Recent swing low/high
    swing = _get_swing_points(data.get("ohlcv", []), direction)
    if swing > 0:
        if direction == "long" and swing < entry:
            sl_candidates.append(("swing", swing))
        elif direction == "short" and swing > entry:
            sl_candidates.append(("swing", swing))

    # 3. ATR-based
    if atr > 0:
        if direction == "long":
            sl_candidates.append(("atr", entry - atr * STOCK_SL_ATR_MULT))
        else:
            sl_candidates.append(("atr", entry + atr * STOCK_SL_ATR_MULT))

    # 4. Depth-based
    if depth_info and depth_info.get("direction") != "neutral":
        if direction == "long" and depth_info["direction"] == "bearish":
            sl_candidates.append(("depth", entry * 0.995))
        elif direction == "short" and depth_info["direction"] == "bullish":
            sl_candidates.append(("depth", entry * 1.005))

    # 5. Floor
    if direction == "long":
        sl_candidates.append(("floor", entry - entry * STOCK_SL_FLOOR_PCT))
    else:
        sl_candidates.append(("floor", entry + entry * STOCK_SL_FLOOR_PCT))

    if direction == "long":
        sl = max(sl_candidates, key=lambda c: c[1])[1]
    else:
        sl = min(sl_candidates, key=lambda c: c[1])[1]

    # -- Take profit candidates --
    tp_candidates: list[tuple[str, float]] = []

    # 1. SMA level
    sma_50 = _get_sma(data, 50)
    if sma_50 > 0:
        if direction == "long" and sma_50 > entry:
            tp_candidates.append(("sma_50", sma_50))
        elif direction == "short" and sma_50 < entry:
            tp_candidates.append(("sma_50", sma_50))

    # 2. Recent swing
    swing_tp = _get_swing_points(data.get("ohlcv", []), direction, lookback=20)
    if swing_tp > 0:
        if direction == "long" and swing_tp > entry:
            tp_candidates.append(("swing", swing_tp))
        elif direction == "short" and swing_tp < entry:
            tp_candidates.append(("swing", swing_tp))

    # 3. ATR-based
    if atr > 0:
        if direction == "long":
            tp_candidates.append(("atr", entry + atr * STOCK_TP_ATR_MULT))
        else:
            tp_candidates.append(("atr", entry - atr * STOCK_TP_ATR_MULT))

    # 4. Floor
    if direction == "long":
        tp_candidates.append(("floor", entry + entry * STOCK_TP_FLOOR_PCT))
    else:
        tp_candidates.append(("floor", entry - entry * STOCK_TP_FLOOR_PCT))

    if direction == "long":
        tp = min(tp_candidates, key=lambda c: c[1])[1]
    else:
        tp = max(tp_candidates, key=lambda c: c[1])[1]

    rr = _clamp_rr(abs(tp - entry) / max(abs(sl - entry), 0.01))

    # ── Level engine: override SL/TP with nearest meaningful support/resistance ──
    suggested_entry = entry
    suggested_entry_type = "market"
    nearest_support = None
    nearest_resistance = None
    proximity_warning = None
    sl_method_used = "sma+swing+atr+depth"
    tp_method_used = "sma+swing+atr"
    try:
        from engine.level_engine import aggregate_key_levels
        levels = aggregate_key_levels(ticker, data, entry, direction, "stock")
        if levels.get("suggested_entry") and levels["suggested_entry"] != entry:
            suggested_entry = levels["suggested_entry"]
            suggested_entry_type = levels.get("suggested_entry_type", "level")
        nearest_support = levels.get("nearest_support")
        nearest_resistance = levels.get("nearest_resistance")
        proximity_warning = levels.get("proximity_warning")
        # Level-aware SL: stop BEYOND nearest support/resistance
        if direction == "long" and nearest_support:
            level_sl = nearest_support - max(atr * 0.15, 0.02)
            if level_sl > sl:
                sl = level_sl
                sl_method_used = "level_support"
        if direction == "short" and nearest_resistance:
            level_sl = nearest_resistance + max(atr * 0.15, 0.02)
            if level_sl < sl:
                sl = level_sl
                sl_method_used = "level_resistance"
        # Level-aware TP: target next key level
        if direction == "long" and levels.get("resistance_levels"):
            nearest_res = levels["resistance_levels"][0]
            if nearest_res["price"] > entry:
                level_tp = nearest_res["price"]
                if level_tp < tp:
                    tp = level_tp
                    tp_method_used = "level_first_resistance"
        if direction == "short" and levels.get("support_levels"):
            nearest_sup = levels["support_levels"][0]
            if nearest_sup["price"] < entry:
                level_tp = nearest_sup["price"]
                if level_tp > tp:
                    tp = level_tp
                    tp_method_used = "level_first_support"
        # Recompute R:R with level-adjusted SL/TP
        rr = _clamp_rr(abs(tp - entry) / max(abs(sl - entry), 0.01))
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        logger.debug("Level engine unavailable for %s: %s", ticker, e)

    logger.debug(
        "Stock %s: entry=%.2f sl=%.2f tp=%.2f rr=%.1f atr=%.2f sl_method=%s suggested=%s",
        ticker, entry, sl, tp, rr, atr, sl_method_used,
        f"{suggested_entry}({suggested_entry_type})" if suggested_entry != entry else "market",
    )

    return {
        "entry_price": round(entry, 2),
        "stop_loss": round(sl, 2),
        "take_profit": round(tp, 2),
        "risk_reward": round(rr, 2),
        "suggested_entry": round(suggested_entry, 2),
        "suggested_entry_type": suggested_entry_type,
        "nearest_support": round(nearest_support, 2) if nearest_support else None,
        "nearest_resistance": round(nearest_resistance, 2) if nearest_resistance else None,
        "proximity_warning": proximity_warning,
        "sl_method": sl_method_used,
        "tp_method": tp_method_used,
    }


def compute_option_levels(ticker: str, data: dict, direction: str,
                          confidence: float) -> dict:
    """Compute entry/exit levels for an option signal.

    Returns BOTH underlying price levels AND single-leg option premium levels.

    For 0DTE traders:
      - Entry premium: what you pay for ONE ATM option (~50% of straddle)
      - Entry trigger: current underlying price
      - SL (underlying): gamma flip level (where gamma flips = dealer reversal)
      - SL (premium): 50% of entry premium (cut losses early)
      - TP (underlying): next gamma wall or ATR extension (price magnet)
      - TP (premium): 2.5x entry premium (0DTE gamma payoff)

    ATR stops are scaled by sqrt(dte/1) when DTE < 1 to tighten for 0DTE
    where gamma acceleration makes price moves more violent per tick.
    """
    entry = float(data.get("current_price", data.get("underlying_price", 0)))
    if entry <= 0:
        return {}

    atr = _extract_atr(data)
    dte_raw = data.get("dte", 1)
    dte = float(dte_raw) if dte_raw is not None else 1.0
    atm_straddle = float(data.get("atm_straddle_price", 0) or 0)
    gamma_flip = float(data.get("gamma_flip_level", 0) or 0)
    gamma_walls = data.get("gamma_walls", []) or []
    iv = float(data.get("iv", 0) or 0)

    # -- Option premium levels (single leg: ~50% of ATM straddle) --
    # The straddle is call+put; a directional trader buys only ONE side.
    single_option_premium = atm_straddle / 2.0 if atm_straddle > 0 else 0.0

    # If no straddle data, estimate single-leg premium from IV
    if single_option_premium <= 0 and iv > 0:
        t = max(dte / 365, 1/365)  # minimum 1 day
        # ATM option approximation: S * sigma * sqrt(t) * 0.4 (single leg)
        single_option_premium = round(entry * iv * math.sqrt(t) * 0.4, 2)

    option_entry_premium = single_option_premium
    option_sl_premium = round(option_entry_premium * OPTION_SL_PREMIUM_MULT, 2) if option_entry_premium > 0 else 0.0
    option_tp_premium = round(option_entry_premium * OPTION_TP_PREMIUM_MULT, 2) if option_entry_premium > 0 else 0.0

    # -- 0DTE ATR scaling: tighten stops as DTE approaches 0 --
    # Minimum scale of 0.3 so ATR stops remain meaningful even at 0DTE
    dte_scale = min(1.0, max(0.30, math.sqrt(max(dte, 1/365) / OPTION_MIN_DTE_FOR_FULL_ATR)))
    # -- Time-of-day stop tightness (power hour = tighter, opening = wider) --
    tod_tightness = _get_tod_tightness()
    sl_atr_mult = OPTION_SL_ATR_MULT * dte_scale * tod_tightness
    tp_atr_mult = OPTION_TP_ATR_MULT * dte_scale * tod_tightness

    # -- Underlying stop loss candidates --
    sl_candidates: list[tuple[str, float]] = []

    # 1. Gamma flip level (primary for 0DTE -- dealer reversal zone)
    if gamma_flip > 0:
        if direction == "long" and gamma_flip < entry:
            sl_candidates.append(("gamma_flip", gamma_flip))
        elif direction == "short" and gamma_flip > entry:
            sl_candidates.append(("gamma_flip", gamma_flip))

    # 2. ATR-based (scaled for DTE)
    if atr > 0:
        if direction == "long":
            sl_candidates.append(("atr", entry - atr * sl_atr_mult))
        else:
            sl_candidates.append(("atr", entry + atr * sl_atr_mult))

    # 3. Floor
    if direction == "long":
        sl_candidates.append(("floor", entry - entry * OPTION_SL_FLOOR_PCT))
    else:
        sl_candidates.append(("floor", entry + entry * OPTION_SL_FLOOR_PCT))

    if direction == "long":
        sl = max(sl_candidates, key=lambda c: c[1])[1]
    else:
        sl = min(sl_candidates, key=lambda c: c[1])[1]

    # -- Underlying take profit candidates --
    tp_candidates: list[tuple[str, float]] = []

    # 1. Gamma wall (next dealer concentration zone -- price magnet)
    if gamma_walls:
        if direction == "long":
            valid = [w["strike"] for w in gamma_walls if w["strike"] > entry]
            if valid:
                tp_candidates.append(("gamma_wall", min(valid)))
        else:
            valid = [w["strike"] for w in gamma_walls if w["strike"] < entry]
            if valid:
                tp_candidates.append(("gamma_wall", max(valid)))

    # 2. ATR-based (scaled for DTE)
    if atr > 0:
        if direction == "long":
            tp_candidates.append(("atr", entry + atr * tp_atr_mult))
        else:
            tp_candidates.append(("atr", entry - atr * tp_atr_mult))

    # 3. Floor
    if direction == "long":
        tp_candidates.append(("floor", entry + entry * OPTION_TP_FLOOR_PCT))
    else:
        tp_candidates.append(("floor", entry - entry * OPTION_TP_FLOOR_PCT))

    if direction == "long":
        tp = min(tp_candidates, key=lambda c: c[1])[1]
    else:
        tp = max(tp_candidates, key=lambda c: c[1])[1]

    # -- Risk/reward --
    # Underlying price R:R
    rr = _clamp_rr(abs(tp - entry) / max(abs(sl - entry), 0.01))
    # Option premium R:R = potential profit / risked capital
    # e.g., pay $1.00, risk losing $0.50 (to SL), target $2.50 profit
    premium_rr = 0.0
    if option_entry_premium > 0 and option_entry_premium > option_sl_premium:
        risk_per_contract = option_entry_premium - option_sl_premium
        reward_per_contract = option_tp_premium - option_entry_premium
        if risk_per_contract > 0:
            premium_rr = _clamp_rr(reward_per_contract / risk_per_contract)

    # ── Level engine: override SL/TP with nearest gamma/support/resistance data ──
    sl_method_used = "gamma_flip+atr(dte_scaled)"
    tp_method_used = "gamma_wall+atr(dte_scaled)"
    suggested_entry = entry
    suggested_entry_type = "market"
    nearest_support = None
    nearest_resistance = None
    proximity_warning = None
    try:
        # Use cached key_levels if available (computed once in data_prep)
        from engine.level_engine import aggregate_key_levels
        cached = data.get("key_levels")
        if cached:
            levels = cached
        else:
            levels = aggregate_key_levels(ticker, data, entry, direction, "option")
        if levels.get("suggested_entry") and levels["suggested_entry"] != entry:
            suggested_entry = levels["suggested_entry"]
            suggested_entry_type = levels.get("suggested_entry_type", "level")
        nearest_support = levels.get("nearest_support")
        nearest_resistance = levels.get("nearest_resistance")
        proximity_warning = levels.get("proximity_warning")
        # Level-aware SL: gamma flip is already primary, but use near support/resistance as fallback
        if direction == "long" and nearest_support:
            level_sl = nearest_support - max(atr * dte_scale * 0.1, 0.02)
            if level_sl > sl:
                sl = level_sl
                sl_method_used = "level_support"
        if direction == "short" and nearest_resistance:
            level_sl = nearest_resistance + max(atr * dte_scale * 0.1, 0.02)
            if level_sl < sl:
                sl = level_sl
                sl_method_used = "level_resistance"
        # Recompute R:R with level-adjusted SL
        rr = _clamp_rr(abs(tp - entry) / max(abs(sl - entry), 0.01))
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        logger.debug("Level engine unavailable for %s: %s", ticker, e)

    logger.debug(
        "Option %s: entry=%.2f sl=%.2f tp=%.2f rr=%.1f "
        "opt_prem=%.2f opt_sl=%.2f opt_tp=%.2f prem_rr=%.1f "
        "gamma_flip=%.2f dte=%.0f dte_scale=%.2f sl_method=%s suggested=%s",
        ticker, entry, sl, tp, rr,
        option_entry_premium, option_sl_premium, option_tp_premium,
        premium_rr, gamma_flip, dte, dte_scale, sl_method_used,
        f"{suggested_entry}({suggested_entry_type})" if suggested_entry != entry else "market",
    )

    return {
        "entry_price": round(entry, 2),
        "stop_loss": round(sl, 2),
        "take_profit": round(tp, 2),
        "risk_reward": round(rr, 2),
        "suggested_entry": round(suggested_entry, 2),
        "suggested_entry_type": suggested_entry_type,
        "nearest_support": round(nearest_support, 2) if nearest_support else None,
        "nearest_resistance": round(nearest_resistance, 2) if nearest_resistance else None,
        "proximity_warning": proximity_warning,
        "option_entry_premium": round(option_entry_premium, 2),
        "option_sl_premium": round(option_sl_premium, 2),
        "option_tp_premium": round(option_tp_premium, 2),
        "premium_risk_reward": round(premium_rr, 2),
        "sl_method": sl_method_used,
        "tp_method": tp_method_used,
        "gamma_flip_level": gamma_flip,
        "dte": dte,
    }


# ================================================================
# Unified entry point
# ================================================================

def compute_entry_exit_levels(ticker: str, data: dict, direction: str,
                              confidence: float, instr_type: str) -> dict:
    """Compute entry price, stop loss, and take profit for a signal.

    Args:
        ticker: Symbol
        data: Full ticker data dict (from ticker_data_map or option_metrics)
        direction: 'long' or 'short'
        confidence: Signal confidence (0-1)
        instr_type: 'stock', 'future', or 'option'

    Returns:
        Dict with entry_price, stop_loss, take_profit, and instrument-specific
        fields like option premium levels.
    """
    if direction == "neutral" or confidence <= 0:
        return {}

    if instr_type == "future":
        return compute_futures_levels(ticker, data, direction, confidence)
    elif instr_type == "option":
        return compute_option_levels(ticker, data, direction, confidence)
    else:  # stock
        return compute_stock_levels(ticker, data, direction, confidence)
