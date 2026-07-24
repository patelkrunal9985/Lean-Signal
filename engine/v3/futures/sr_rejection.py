"""
SR Rejection Strategy — Futures.

Detects high-probability bounce/rejection behavior at key support and
resistance levels for futures. Cross-references price action (wick rejection,
volume spike, order book depth imbalance) with the level engine's key level map.

Futures-specific enhancements:
  - Order book depth imbalance detection (bid/ask concentration at levels)
  - Cumulative delta divergence (delta going one way, price going another)
  - Time-of-day awareness (power hour breakouts more reliable)

Key signals:
  1. SUPPORT BOUNCE (long): Price at support, wick rejection, depth imbalance bullish
  2. RESISTANCE REJECTION (short): Price at resistance, wick rejection, depth bearish
  3. LIQUIDITY SWEEP (counter): Price sweeps level, reverses with delta divergence
"""
from __future__ import annotations
from engine.v3.base import BaseV3Strategy
from utils.logger import get_logger

logger = get_logger("engine.v3.futures.sr_rejection")

# ── Proximity thresholds ──
PROXIMITY_PCT = 0.005
AT_LEVEL_PCT = 0.0015
BREAKOUT_PCT = 0.003

# ── Wick rejection ratio ──
WICK_REJECTION_RATIO = 0.55

# ── Confidence weights ──
LEVEL_PROXIMITY_WEIGHT = 0.25
PRICE_ACTION_WEIGHT = 0.30
DEPTH_WEIGHT = 0.25
DELTA_WEIGHT = 0.20


def _detect_wick_rejection(ohlcv: list, direction: str) -> tuple[bool, float]:
    """Detect wick rejection patterns in recent candles for futures."""
    if not ohlcv or len(ohlcv) < 2:
        return False, 0.0

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

        if direction == "long":
            lower_wick = min(o, c) - l
            wick_ratio = lower_wick / max(candle_range, 0.01)
            if wick_ratio >= WICK_REJECTION_RATIO:
                rejection_score = min(wick_ratio * 1.2, 0.80)
                if c > (h + l) / 2:
                    rejection_score = min(rejection_score + 0.10, 0.90)
        else:
            upper_wick = h - max(o, c)
            wick_ratio = upper_wick / max(candle_range, 0.01)
            if wick_ratio >= WICK_REJECTION_RATIO:
                rejection_score = min(wick_ratio * 1.2, 0.80)
                if c < (h + l) / 2:
                    rejection_score = min(rejection_score + 0.10, 0.90)

    return rejection_score > 0.3, round(rejection_score, 4)


def _get_depth_imbalance(depth_data: dict, direction: str) -> tuple[bool, float]:
    """Extract order book imbalance and check if it supports the signal direction.

    For long (support bounce): want bullish depth (bids > asks)
    For short (resistance rejection): want bearish depth (asks > bids)
    """
    if not depth_data:
        return False, 0.0

    bids = depth_data.get("bids", depth_data.get("bid", []))
    asks = depth_data.get("asks", depth_data.get("ask", []))

    bid_vol = 0.0
    ask_vol = 0.0

    for b in (bids if isinstance(bids, list) else [bids])[:5]:
        bid_vol += float(b.get("size", 0) or (b[1] if isinstance(b, (list, tuple)) else 0))

    for a in (asks if isinstance(asks, list) else [asks])[:5]:
        ask_vol += float(a.get("size", 0) or (a[1] if isinstance(a, (list, tuple)) else 0))

    if bid_vol + ask_vol <= 0:
        return False, 0.0

    ratio = bid_vol / max(ask_vol, 1)

    if direction == "long" and ratio > 1.2:
        return True, min((ratio - 1.0) * 0.40, 0.35)
    elif direction == "short" and ratio < 0.8:
        return True, min((1.0 - ratio) * 0.40, 0.35)

    return False, 0.0


def _get_cumulative_delta_divergence(context: dict, direction: str, current_price: float) -> tuple[bool, float]:
    """Check if cumulative delta is diverging from price action.

    Delta going up but price going down = bullish divergence (support bounce incoming)
    Delta going down but price going up = bearish divergence (resistance rejection)
    """
    cd = context.get("cumulative_delta", 0)
    cd_change = context.get("cumulative_delta_change", 0)
    price_change = context.get("price_change_1d", 0)

    if abs(cd_change) < 0.01 or abs(price_change) < 0.001:
        return False, 0.0

    if direction == "long" and cd_change > 0 and price_change < 0:
        # Delta positive, price negative = hidden buying
        div_strength = min(abs(cd_change) / max(abs(price_change) * 10, 0.001), 1.0)
        return True, round(div_strength * 0.30, 4)
    elif direction == "short" and cd_change < 0 and price_change > 0:
        div_strength = min(abs(cd_change) / max(abs(price_change) * 10, 0.001), 1.0)
        return True, round(div_strength * 0.30, 4)

    return False, 0.0


def _get_volume_spike(ohlcv: list) -> tuple[bool, float]:
    """Detect volume spike on rejection candles."""
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
    if spike_ratio >= 1.4:
        return True, min(spike_ratio / 3.0, 0.45)

    return False, 0.0


def _get_tod_bonus() -> float:
    """Time-of-day bonus: power hour breakouts are more reliable."""
    try:
        from engine.time_of_day import get_time_window
        window = get_time_window()
        if window in ("power_hour", "closing_pin"):
            return 0.05
        elif window == "opening_drive":
            return 0.03
        elif window == "midday_lull":
            return -0.05
    except ImportError:
        pass
    return 0.0


class SRRejectionFutures(BaseV3Strategy):
    name = "sr_rejection"
    description = (
        "Support/resistance rejection detection for futures. "
        "Identifies high-probability bounces at support and rejections at resistance "
        "using wick analysis, order book depth imbalance, cumulative delta divergence, "
        "and volume spikes. Leverages level_engine for Fibonacci, prior HL, VP levels."
    )
    applies_to = ("future",)
    default_weight = 0.12
    family = "technical"

    def compute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        ohlcv = context.get("ohlcv", [])
        indicators = context.get("indicators", {})
        current_price = context.get("current_price", 0)
        depth = context.get("depth", {})

        if current_price <= 0 or not ohlcv or len(ohlcv) < 3:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        # ── Get key levels ──
        try:
            from engine.level_engine import aggregate_key_levels
            key_levels = aggregate_key_levels(ticker, context, current_price, "neutral", "future")
        except ImportError:
            key_levels = _simple_futures_levels(ohlcv, current_price)

        if not key_levels or not key_levels.get("levels"):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        best_signal = None
        best_confidence = 0.0
        tod_bonus = _get_tod_bonus()

        # ── Check support levels for bounce (long) ──
        for supp in key_levels.get("support_levels", [])[:3]:
            level_price = supp.get("price", 0)
            if level_price <= 0:
                continue

            dist_pct = abs(current_price - level_price) / max(current_price, 0.01)
            if dist_pct > PROXIMITY_PCT * 2:
                continue

            is_rejection, wick_score = _detect_wick_rejection(ohlcv, "long")
            depth_ok, depth_score = _get_depth_imbalance(depth, "long")
            delta_div, delta_score = _get_cumulative_delta_divergence(context, "long", current_price)
            has_vol, vol_score = _get_volume_spike(ohlcv)

            if is_rejection or delta_div:
                prox_score = max(0, (PROXIMITY_PCT * 2 - dist_pct) / (PROXIMITY_PCT * 2)) * LEVEL_PROXIMITY_WEIGHT
                pa_score = wick_score * PRICE_ACTION_WEIGHT
                depth_bonus = depth_score * DEPTH_WEIGHT
                delta_bonus = delta_score * DELTA_WEIGHT

                bounce_conf = prox_score + pa_score + depth_bonus + delta_bonus

                if supp.get("priority", 1) >= 6:
                    bounce_conf = min(bounce_conf * 1.15, 0.85)

                bounce_conf = min(bounce_conf + tod_bonus, 0.85)

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
                        "depth_support": depth_ok,
                        "delta_divergence": delta_div,
                    }

        # ── Check resistance levels for rejection (short) ──
        for res in key_levels.get("resistance_levels", [])[:3]:
            level_price = res.get("price", 0)
            if level_price <= 0:
                continue

            dist_pct = abs(current_price - level_price) / max(current_price, 0.01)
            if dist_pct > PROXIMITY_PCT * 2:
                continue

            is_rejection, wick_score = _detect_wick_rejection(ohlcv, "short")
            depth_ok, depth_score = _get_depth_imbalance(depth, "short")
            delta_div, delta_score = _get_cumulative_delta_divergence(context, "short", current_price)
            has_vol, vol_score = _get_volume_spike(ohlcv)

            if is_rejection or delta_div:
                prox_score = max(0, (PROXIMITY_PCT * 2 - dist_pct) / (PROXIMITY_PCT * 2)) * LEVEL_PROXIMITY_WEIGHT
                pa_score = wick_score * PRICE_ACTION_WEIGHT
                depth_bonus = depth_score * DEPTH_WEIGHT
                delta_bonus = delta_score * DELTA_WEIGHT

                reject_conf = prox_score + pa_score + depth_bonus + delta_bonus

                if res.get("priority", 1) >= 6:
                    reject_conf = min(reject_conf * 1.15, 0.85)

                reject_conf = min(reject_conf + tod_bonus, 0.85)

                if reject_conf > best_confidence:
                    best_confidence = reject_conf
                    best_signal = {
                        "direction": "short",
                        "confidence": round(reject_conf, 4),
                        "action": "buy",
                        "signal_type": "resistance_rejection",
                        "level": round(level_price, 2),
                        "level_type": res.get("type", "unknown"),
                        "dist_pct": round(dist_pct * 100, 2),
                        "depth_support": depth_ok,
                        "delta_divergence": delta_div,
                    }

        if best_signal is None or best_signal["confidence"] < 0.10:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["no_rejection_detected"]}

        best_signal["strategy"] = self.name
        best_signal["reasons"] = [f"sr_rejection_{best_signal['signal_type']}_{best_signal['level_type']}"]
        return best_signal


def _simple_futures_levels(ohlcv: list, current_price: float) -> dict:
    """Fallback level computation for futures when level_engine is unavailable."""
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
