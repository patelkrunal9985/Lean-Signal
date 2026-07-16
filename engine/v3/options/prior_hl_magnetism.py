"""
Prior Day/Week High-Low Magnetism + Option Confirmation.

Prior day's high and low are statistically the most significant intraday
support/resistance levels.  When price approaches these levels AND option
gamma/OI clusters at the same strike zone, you get a structure+positioning
confluence that heavily favors either:

  1. REJECTION (most common):   Price bounces off prior H/L, option flow fades
  2. BREAKOUT (high conviction): Price breaks through WITH option flow confirming

The prior week high/low add a second, higher-timeframe magnet — when both
day and week levels align, the signal is very strong.
"""
from __future__ import annotations
from kronos.strategies.v3.base import BaseV3Strategy
from utils.logger import get_logger

logger = get_logger("engine.v3.options.prior_hl_magnetism")

# ── Proximity thresholds ──
PROXIMITY_PCT = 0.005       # Within 0.5% of level = "approaching"
AT_LEVEL_PCT = 0.0015       # Within 0.15% = "at" level
BREAKOUT_PCT = 0.003        # 0.3% beyond level = breakout attempt

# ── Alignment bonuses ──
DAY_WEEK_ALIGN_BONUS = 0.12      # Bonus when day + week levels align
PROXIMITY_WEIGHT = 0.30          # Weight for proximity score
OPTION_CONF_WEIGHT = 0.40        # Weight for option confirmation score
TIME_WEIGHT = 0.30               # Weight for time-of-day (morning/afternoon edge)


def _find_prior_day_hl(ohlcv: list) -> tuple[float, float]:
    """Extract prior trading day's high and low from OHLCV."""
    if not ohlcv or len(ohlcv) < 2:
        return 0.0, 0.0
    prev = ohlcv[-2]
    if isinstance(prev, dict):
        return float(prev.get("high", 0) or 0), float(prev.get("low", 0) or 0)
    return 0.0, 0.0


def _find_prior_week_hl(ohlcv: list) -> tuple[float, float]:
    """Extract prior week's high and low (last 5 trading days excluding today)."""
    if not ohlcv or len(ohlcv) < 6:
        return 0.0, 0.0
    week_bars = ohlcv[-6:-1]  # 5 bars before today
    highs = []
    lows = []
    for bar in week_bars:
        if isinstance(bar, dict):
            h = bar.get("high", 0) or 0
            l = bar.get("low", 0) or 0
            if h > 0:
                highs.append(h)
            if l > 0:
                lows.append(l)
    if not highs or not lows:
        return 0.0, 0.0
    return max(highs), min(lows)


def _compute_gamma_at_strike(chain: dict, strike_target: float, side: str = "all", tolerance_pct: float = 0.01) -> float:
    """Sum gamma for contracts near a target strike, filtered by side.

    Args:
        side: "calls" (resistance levels), "puts" (support levels), or "all".
    """
    total_gamma = 0.0
    if side == "calls":
        contracts = chain.get("calls", [])
    elif side == "puts":
        contracts = chain.get("puts", [])
    else:
        contracts = chain.get("calls", []) + chain.get("puts", [])
    for c in contracts:
        stk = c.get("strike", 0) or 0
        if stk <= 0 or strike_target <= 0:
            continue
        if abs(stk - strike_target) / strike_target < tolerance_pct:
            gamma = c.get("gamma", 0) or 0
            oi = c.get("openInterest", 0) or 0
            total_gamma += gamma * oi
    return total_gamma


def _compute_oi_at_strike(chain: dict, strike_target: float, side: str = "all", tolerance_pct: float = 0.01) -> float:
    """Sum open interest near a target strike, filtered by side.

    Args:
        side: "calls" (resistance levels), "puts" (support levels), or "all".
    """
    total_oi = 0
    if side == "calls":
        contracts = chain.get("calls", [])
    elif side == "puts":
        contracts = chain.get("puts", [])
    else:
        contracts = chain.get("calls", []) + chain.get("puts", [])
    for c in contracts:
        stk = c.get("strike", 0) or 0
        if stk <= 0 or strike_target <= 0:
            continue
        if abs(stk - strike_target) / strike_target < tolerance_pct:
            oi = c.get("openInterest", 0) or 0
            total_oi += oi
    return total_oi


def _get_time_of_day_score() -> tuple[float, str]:
    """Morning and afternoon give higher scores; midday is choppy."""
    try:
        from engine.time_of_day import get_time_window
        window = get_time_window()
        scores = {
            "opening_drive": 0.90,
            "morning_session": 0.80,
            "midday_lull": 0.40,
            "power_hour": 0.85,
            "closing_pin": 0.70,
        }
        return scores.get(window, 0.50), window
    except ImportError:
        return 0.50, "unknown"


class PriorHLMagnetism(BaseV3Strategy):
    name = "prior_hl_magnetism"
    description = (
        "Prior day/week high-low magnetism with option gamma/OI confirmation. "
        "Detects high-probability rejections and breakouts at key structural levels "
        "validated by option positioning."
    )
    applies_to = ("option",)
    default_weight = 0.12

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        ohlcv = context.get("ohlcv", [])
        if underlying <= 0 or not chain or not ohlcv:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        # ── Prior day H/L ──
        pd_high, pd_low = _find_prior_day_hl(ohlcv)
        # ── Prior week H/L ──
        pw_high, pw_low = _find_prior_week_hl(ohlcv)

        if pd_high <= 0 and pd_low <= 0 and pw_high <= 0 and pw_low <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        price_change = context.get("price_change_1d", 0)
        tod_score, tod_window = _get_time_of_day_score()

        # ── Find the nearest magnetic level and direction ──
        # Priority: day levels > week levels (tighter, more immediate)
        best_signal = None
        best_confidence = 0.0

        # -- Check prior day high (resistance, favor short reversals) --
        if pd_high > 0:
            dist_pct_high = abs(underlying - pd_high) / pd_high
            if dist_pct_high < PROXIMITY_PCT:
                gamma_at_high = _compute_gamma_at_strike(chain, pd_high, side="calls")
                oi_at_high = _compute_oi_at_strike(chain, pd_high, side="calls")
                option_conf = min(gamma_at_high / 500.0, 0.40) + min(oi_at_high / 5000.0, 0.20)
                prox_score = max(0, (PROXIMITY_PCT - dist_pct_high) / PROXIMITY_PCT) * PROXIMITY_WEIGHT

                # Rejection signal (bearish): price rising into resistance + option flow fading
                if price_change > 0 and underlying < pd_high:
                    reject_conf = prox_score + option_conf * OPTION_CONF_WEIGHT + tod_score * TIME_WEIGHT
                    # Bonus: prior week high also nearby
                    if pw_high > 0 and abs(pd_high - pw_high) / pd_high < 0.01:
                        reject_conf += DAY_WEEK_ALIGN_BONUS
                    reject_conf = min(reject_conf, 0.80)
                    if reject_conf > best_confidence:
                        best_confidence = reject_conf
                        best_signal = {
                            "direction": "short",
                            "confidence": round(reject_conf, 4),
                            "action": "buy",
                            "signal_type": "rejection",
                            "level": round(pd_high, 2),
                            "level_type": "prior_day_high",
                            "dist_pct": round(dist_pct_high * 100, 2),
                            "gamma_oi_score": round(option_conf, 4),
                        }

                # Breakout signal (bullish): price pushing through with option flow
                if underlying > pd_high and dist_pct_high < BREAKOUT_PCT and price_change > 0:
                    break_conf = prox_score + option_conf * OPTION_CONF_WEIGHT + tod_score * TIME_WEIGHT
                    break_conf = min(break_conf * 1.1, 0.85)  # breakout bonus
                    if break_conf > best_confidence:
                        best_confidence = break_conf
                        best_signal = {
                            "direction": "long",
                            "confidence": round(break_conf, 4),
                            "action": "buy",
                            "signal_type": "breakout",
                            "level": round(pd_high, 2),
                            "level_type": "prior_day_high",
                            "dist_pct": round(dist_pct_high * 100, 2),
                            "gamma_oi_score": round(option_conf, 4),
                        }

        # -- Check prior day low (support, favor long bounces) --
        if pd_low > 0:
            dist_pct_low = abs(underlying - pd_low) / pd_low
            if dist_pct_low < PROXIMITY_PCT:
                gamma_at_low = _compute_gamma_at_strike(chain, pd_low, side="puts")
                oi_at_low = _compute_oi_at_strike(chain, pd_low, side="puts")
                option_conf = min(gamma_at_low / 500.0, 0.40) + min(oi_at_low / 5000.0, 0.20)
                prox_score = max(0, (PROXIMITY_PCT - dist_pct_low) / PROXIMITY_PCT) * PROXIMITY_WEIGHT

                # Bounce signal (bullish): price falling into support + option flow supporting
                if price_change < 0 and underlying > pd_low:
                    bounce_conf = prox_score + option_conf * OPTION_CONF_WEIGHT + tod_score * TIME_WEIGHT
                    if pw_low > 0 and abs(pd_low - pw_low) / pd_low < 0.01:
                        bounce_conf += DAY_WEEK_ALIGN_BONUS
                    bounce_conf = min(bounce_conf, 0.80)
                    if bounce_conf > best_confidence:
                        best_confidence = bounce_conf
                        best_signal = {
                            "direction": "long",
                            "confidence": round(bounce_conf, 4),
                            "action": "buy",
                            "signal_type": "bounce",
                            "level": round(pd_low, 2),
                            "level_type": "prior_day_low",
                            "dist_pct": round(dist_pct_low * 100, 2),
                            "gamma_oi_score": round(option_conf, 4),
                        }

                # Breakdown signal (bearish): price dropping through with option flow
                if underlying < pd_low and dist_pct_low < BREAKOUT_PCT and price_change < 0:
                    break_conf = prox_score + option_conf * OPTION_CONF_WEIGHT + tod_score * TIME_WEIGHT
                    break_conf = min(break_conf * 1.1, 0.85)
                    if break_conf > best_confidence:
                        best_confidence = break_conf
                        best_signal = {
                            "direction": "short",
                            "confidence": round(break_conf, 4),
                            "action": "buy",
                            "signal_type": "breakdown",
                            "level": round(pd_low, 2),
                            "level_type": "prior_day_low",
                            "dist_pct": round(dist_pct_low * 100, 2),
                            "gamma_oi_score": round(option_conf, 4),
                        }

        # -- Check prior week levels (wider, use only with strong confirmation) --
        if best_signal is None and pw_high > 0:
            dist_pct_wh = abs(underlying - pw_high) / pw_high
            if dist_pct_wh < PROXIMITY_PCT * 1.5:
                gamma_at_wh = _compute_gamma_at_strike(chain, pw_high, side="calls")
                oi_at_wh = _compute_oi_at_strike(chain, pw_high, side="calls")
                option_conf = min(gamma_at_wh / 500.0, 0.40) + min(oi_at_wh / 5000.0, 0.20)
                # Week levels need stronger option confirmation to fire
                if option_conf > 0.20:
                    if price_change > 0:
                        wh_conf = min(option_conf * 0.70 + tod_score * 0.20, 0.65)
                        best_signal = {
                            "direction": "short",
                            "confidence": round(wh_conf, 4),
                            "action": "buy",
                            "signal_type": "rejection",
                            "level": round(pw_high, 2),
                            "level_type": "prior_week_high",
                            "dist_pct": round(dist_pct_wh * 100, 2),
                            "gamma_oi_score": round(option_conf, 4),
                        }

        if best_signal is None and pw_low > 0:
            dist_pct_wl = abs(underlying - pw_low) / pw_low
            if dist_pct_wl < PROXIMITY_PCT * 1.5:
                gamma_at_wl = _compute_gamma_at_strike(chain, pw_low, side="puts")
                oi_at_wl = _compute_oi_at_strike(chain, pw_low, side="puts")
                option_conf = min(gamma_at_wl / 500.0, 0.40) + min(oi_at_wl / 5000.0, 0.20)
                if option_conf > 0.20:
                    if price_change < 0:
                        wl_conf = min(option_conf * 0.70 + tod_score * 0.20, 0.65)
                        best_signal = {
                            "direction": "long",
                            "confidence": round(wl_conf, 4),
                            "action": "buy",
                            "signal_type": "bounce",
                            "level": round(pw_low, 2),
                            "level_type": "prior_week_low",
                            "dist_pct": round(dist_pct_wl * 100, 2),
                            "gamma_oi_score": round(option_conf, 4),
                        }

        if best_signal is None or best_signal["confidence"] < 0.10:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        best_signal["strategy"] = self.name
        best_signal["time_window"] = tod_window
        return best_signal
