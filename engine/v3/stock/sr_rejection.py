"""
SR Rejection Strategy — Stocks.

Detects high-probability bounce/rejection behavior at key support and
resistance levels for stocks. Cross-references price action (wick rejection,
volume spike, RSI divergence) with the level engine's key level map to
identify when price is likely to reverse at a level rather than break through.

Similar to prior_hl_magnetism.py for options, but adapted for stock-specific
data (no option chain, uses volume/RSI/price action instead).

Key signals:
  1. SUPPORT BOUNCE (long): Price approaching support, wick rejection, RSI oversold
  2. RESISTANCE REJECTION (short): Price at resistance, wick rejection, RSI overbought
  3. FALSE BREAKOUT (counter): Price breaks level, no follow-through, snaps back
"""
from __future__ import annotations
from engine.v3.base import BaseV3Strategy
from utils.logger import get_logger

logger = get_logger("engine.v3.stock.sr_rejection")

# ── Proximity thresholds ──
PROXIMITY_PCT = 0.005       # Within 0.5% = approaching
AT_LEVEL_PCT = 0.0015       # Within 0.15% = at level
BREAKOUT_PCT = 0.003        # 0.3% beyond = breakout attempt

# ── Wick/body ratios for rejection detection ──
WICK_REJECTION_RATIO = 0.6   # Wick is 60%+ of candle range = rejection

# ── Confidence weights ──
LEVEL_PROXIMITY_WEIGHT = 0.30
PRICE_ACTION_WEIGHT = 0.35
VOLUME_WEIGHT = 0.20
RSI_WEIGHT = 0.15


def _detect_wick_rejection(ohlcv: list, direction: str) -> tuple[bool, float]:
    """Detect wick rejection patterns in recent candles.

    For support bounce (long): long lower wick, small body, close near high
    For resistance rejection (short): long upper wick, small body, close near low

    The caller is responsible for proximity filtering to ensure these
    patterns occurred near a relevant level.

    Returns (is_rejection, confidence 0-1).
    """
    if not ohlcv or len(ohlcv) < 2:
        return False, 0.0

    # Check last 2 candles for rejection pattern
    recent = ohlcv[-2:]
    rejection_score = 0.0

    for bar in recent:
        if not isinstance(bar, dict):
            continue
        o = bar.get("open", 0) or 0
        h = bar.get("high", 0) or 0
        l = bar.get("low", 0) or 0
        c = bar.get("close", 0) or 0
        if h <= 0 or l <= 0 or h == l:
            continue

        candle_range = h - l
        body = abs(c - o)

        if direction == "long":
            # Looking for bullish rejection: long lower wick
            lower_wick = min(o, c) - l
            upper_wick = h - max(o, c)
            wick_ratio = lower_wick / max(candle_range, 0.01)

            if wick_ratio >= WICK_REJECTION_RATIO and lower_wick > upper_wick:
                # Strong bullish rejection: most of the candle is lower wick
                rejection_score = min(wick_ratio * 1.2, 0.80)
                # Bonus if close is in upper half
                if c > (h + l) / 2:
                    rejection_score = min(rejection_score + 0.10, 0.90)
        else:
            # Looking for bearish rejection: long upper wick
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l
            wick_ratio = upper_wick / max(candle_range, 0.01)

            if wick_ratio >= WICK_REJECTION_RATIO and upper_wick > lower_wick:
                rejection_score = min(wick_ratio * 1.2, 0.80)
                if c < (h + l) / 2:
                    rejection_score = min(rejection_score + 0.10, 0.90)

    return rejection_score > 0.3, round(rejection_score, 4)


def _detect_volume_spike(ohlcv: list) -> tuple[bool, float]:
    """Detect volume spike on rejection candles (confirms conviction).

    Returns (has_spike, spike_ratio).
    """
    if not ohlcv or len(ohlcv) < 5:
        return False, 0.0

    recent_vols = []
    for bar in ohlcv[-5:]:
        if isinstance(bar, dict):
            v = bar.get("volume", 0) or 0
            if v > 0:
                recent_vols.append(v)

    if len(recent_vols) < 3:
        return False, 0.0

    avg_vol = sum(recent_vols[:-1]) / max(len(recent_vols) - 1, 1)
    last_vol = recent_vols[-1]

    if avg_vol <= 0:
        return False, 0.0

    spike_ratio = last_vol / avg_vol
    if spike_ratio >= 1.5:
        return True, min(spike_ratio / 3.0, 0.50)  # Cap at 0.50 boost

    return False, 0.0


def _get_rsi_zone_score(indicators: dict, direction: str) -> tuple[bool, float]:
    """Check RSI for overbought/oversold zone confirmation.

    Returns (is_confirming, score 0-1).
    """
    rsi = indicators.get("rsi_14", indicators.get("rsi", 50))
    rsi = float(rsi) if rsi else 50

    if direction == "long" and rsi <= 35:
        # Oversold + support bounce = strong buy signal
        return True, min((35 - rsi) / 35 * 0.30 + 0.10, 0.35)
    elif direction == "long" and rsi <= 45:
        return True, 0.10
    elif direction == "short" and rsi >= 65:
        return True, min((rsi - 65) / 35 * 0.30 + 0.10, 0.35)
    elif direction == "short" and rsi >= 55:
        return True, 0.10

    return False, 0.0


def _get_failed_breakout_score(ohlcv: list, level_price: float, direction: str, current_price: float) -> float:
    """Detect a failed breakout and snap-back using proximity constants.

    Price broke through a level, then reversed back. This is a powerful signal.
    Uses BREAKOUT_PCT and AT_LEVEL_PCT for consistent threshold detection.
    """
    if not ohlcv or len(ohlcv) < 3:
        return 0.0

    recent = ohlcv[-3:]
    broke_through = False
    snapped_back = False

    for bar in recent:
        if not isinstance(bar, dict):
            continue
        h = bar.get("high", 0) or 0
        l = bar.get("low", 0) or 0
        c = bar.get("close", 0) or 0

        if direction == "long":
            # Support level: price went below support, then came back above
            if l < level_price * (1 - BREAKOUT_PCT):
                broke_through = True
            if broke_through and c > level_price * (1 + AT_LEVEL_PCT):
                snapped_back = True
        else:
            # Resistance level: price went above resistance, then came back below
            if h > level_price * (1 + BREAKOUT_PCT):
                broke_through = True
            if broke_through and c < level_price * (1 - AT_LEVEL_PCT):
                snapped_back = True

    if broke_through and snapped_back:
        return 0.70

    return 0.0


class SRRejectionStock(BaseV3Strategy):
    name = "sr_rejection"
    description = (
        "Support/resistance rejection detection for stocks. "
        "Identifies high-probability bounces at support and rejections at resistance "
        "using wick analysis, volume spikes, RSI divergence, and failed breakouts. "
        "Cross-references with Fibonacci, prior day/week HL, SMA, and pivot levels."
    )
    applies_to = ("stock",)
    default_weight = 0.12
    family = "technical"

    def compute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        ohlcv = context.get("ohlcv", [])
        indicators = context.get("indicators", {})
        current_price = context.get("current_price", 0)

        if current_price <= 0 or not ohlcv or len(ohlcv) < 3:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        # ── Get key levels from level engine ──
        try:
            from engine.level_engine import aggregate_key_levels
            key_levels = aggregate_key_levels(ticker, context, current_price, "neutral", "stock")
        except ImportError:
            # Fallback: use simple prior day HL
            key_levels = _simple_levels(ohlcv, current_price)

        if not key_levels or not key_levels.get("levels"):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        price_change_1d = context.get("price_change_1d", 0)

        best_signal = None
        best_confidence = 0.0

        # ── Check each support level for bounce signals ──
        for supp in key_levels.get("support_levels", [])[:3]:
            level_price = supp.get("price", 0)
            if level_price <= 0:
                continue

            dist_pct = abs(current_price - level_price) / max(current_price, 0.01)

            # Only check levels within proximity
            if dist_pct > PROXIMITY_PCT * 2:
                continue

            # ── Support bounce (long signal) ──
            # Price should be near/above support, ideally falling into it
            is_rejection, wick_score = _detect_wick_rejection(ohlcv, "long")
            has_vol_spike, vol_score = _detect_volume_spike(ohlcv)
            rsi_confirm, rsi_score = _get_rsi_zone_score(indicators, "long")
            fb_score = _get_failed_breakout_score(ohlcv, level_price, "long", current_price)

            if is_rejection or fb_score > 0:
                prox_score = max(0, (PROXIMITY_PCT * 2 - dist_pct) / (PROXIMITY_PCT * 2)) * LEVEL_PROXIMITY_WEIGHT
                pa_score = (wick_score + fb_score) * PRICE_ACTION_WEIGHT
                vol_bonus = vol_score * VOLUME_WEIGHT
                rsi_bonus = rsi_score * RSI_WEIGHT

                bounce_conf = prox_score + pa_score + vol_bonus + rsi_bonus

                # Bonus for high-priority levels (fib 618, prior day low, etc.)
                if supp.get("priority", 1) >= 6:
                    bounce_conf = min(bounce_conf * 1.15, 0.85)

                bounce_conf = min(bounce_conf, 0.85)

                if bounce_conf > best_confidence:
                    best_confidence = bounce_conf
                    best_signal = {
                        "direction": "long",
                        "confidence": round(bounce_conf, 4),
                        "action": "buy",
                        "signal_type": "support_bounce",
                        "level": round(level_price, 2),
                        "level_type": supp.get("type", "unknown"),
                        "dist_pct": round(dist_pct * 100, 2),
                        "wick_score": round(wick_score, 4),
                        "volume_spike": has_vol_spike,
                    }
                    if fb_score > 0:
                        best_signal["signal_type"] = "failed_breakdown_bounce"

        # ── Check each resistance level for rejection signals ──
        for res in key_levels.get("resistance_levels", [])[:3]:
            level_price = res.get("price", 0)
            if level_price <= 0:
                continue

            dist_pct = abs(current_price - level_price) / max(current_price, 0.01)
            if dist_pct > PROXIMITY_PCT * 2:
                continue

            # ── Resistance rejection (short signal) ──
            is_rejection, wick_score = _detect_wick_rejection(ohlcv, "short")
            has_vol_spike, vol_score = _detect_volume_spike(ohlcv)
            rsi_confirm, rsi_score = _get_rsi_zone_score(indicators, "short")
            fb_score = _get_failed_breakout_score(ohlcv, level_price, "short", current_price)

            if is_rejection or fb_score > 0:
                prox_score = max(0, (PROXIMITY_PCT * 2 - dist_pct) / (PROXIMITY_PCT * 2)) * LEVEL_PROXIMITY_WEIGHT
                pa_score = (wick_score + fb_score) * PRICE_ACTION_WEIGHT
                vol_bonus = vol_score * VOLUME_WEIGHT
                rsi_bonus = rsi_score * RSI_WEIGHT

                reject_conf = prox_score + pa_score + vol_bonus + rsi_bonus

                if res.get("priority", 1) >= 6:
                    reject_conf = min(reject_conf * 1.15, 0.85)

                reject_conf = min(reject_conf, 0.85)

                if reject_conf > best_confidence:
                    best_confidence = reject_conf
                    best_signal = {
                        "direction": "short",
                        "confidence": round(reject_conf, 4),
                        "action": "buy",  # buy puts / sell calls
                        "signal_type": "resistance_rejection",
                        "level": round(level_price, 2),
                        "level_type": res.get("type", "unknown"),
                        "dist_pct": round(dist_pct * 100, 2),
                        "wick_score": round(wick_score, 4),
                        "volume_spike": has_vol_spike,
                    }
                    if fb_score > 0:
                        best_signal["signal_type"] = "failed_breakout_rejection"

        if best_signal is None or best_signal["confidence"] < 0.10:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["no_rejection_detected"]}

        best_signal["strategy"] = self.name
        best_signal["reasons"] = [f"sr_rejection_{best_signal['signal_type']}_{best_signal['level_type']}"]
        return best_signal


def _simple_levels(ohlcv: list, current_price: float) -> dict:
    """Fallback level computation when level_engine is unavailable."""
    if not ohlcv or len(ohlcv) < 2:
        return {}

    prev = ohlcv[-2]
    if not isinstance(prev, dict):
        return {}

    pd_high = prev.get("high", 0) or 0
    pd_low = prev.get("low", 0) or 0
    pc = prev.get("close", 0) or 0

    levels = []
    if pd_high > 0:
        levels.append({"price": pd_high, "type": "prior_day_high", "priority": 7, "side": "resistance", "source": "price_structure", "label": "Prior Day High"})
    if pd_low > 0:
        levels.append({"price": pd_low, "type": "prior_day_low", "priority": 7, "side": "support", "source": "price_structure", "label": "Prior Day Low"})

    if pd_high > 0 and pd_low > 0 and pc > 0:
        pivot = (pd_high + pd_low + pc) / 3.0
        r1 = 2 * pivot - pd_low
        s1 = 2 * pivot - pd_high
        if r1 > current_price:
            levels.append({"price": r1, "type": "pivot_r1", "priority": 2, "side": "resistance", "source": "price_structure", "label": "Pivot R1"})
        if s1 < current_price:
            levels.append({"price": s1, "type": "pivot_s1", "priority": 2, "side": "support", "source": "price_structure", "label": "Pivot S1"})

    supports = [l for l in levels if l["side"] == "support"]
    resistances = [l for l in levels if l["side"] == "resistance"]
    supports.sort(key=lambda l: -l["price"])
    resistances.sort(key=lambda l: l["price"])

    return {
        "levels": levels,
        "support_levels": supports,
        "resistance_levels": resistances,
        "nearest_support": supports[0]["price"] if supports else None,
        "nearest_resistance": resistances[0]["price"] if resistances else None,
    }
