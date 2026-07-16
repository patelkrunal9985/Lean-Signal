"""
OI Change Rate — Smart Money vs Noise Flow.

Uses chain snapshots (current vs previous) to compute OI deltas per strike.
Distinguishes:

  POSITIONING (smart money):
    - OI building significantly (>20% increase) at specific strikes
    - Volume moderate relative to OI change
    - Indicates institutional accumulation of directional exposure
    - High signal-to-noise: positions are "sticky"

  NOISE (retail/day-trading flow):
    - OI flat but volume high
    - Indicates day-trading churn, not directional conviction
    - Low signal-to-noise: positions are transient

The net OI change direction (calls building vs puts building) gives the
signal direction.  The magnitude and volume/OI ratio give confidence.

Strikes are weighted by proximity to the underlying price — OI building
at ATM strikes is far more significant than far OTM positioning.
"""
from __future__ import annotations
import math
from kronos.strategies.v3.base import BaseV3Strategy
from utils.logger import get_logger

logger = get_logger("engine.v3.options.oi_change_rate")

# ── Thresholds ──
OI_DELTA_MIN_TOTAL = 1000       # Minimum total OI change across strikes to fire
OI_DELTA_PCT_MIN = 0.15         # Minimum % change at individual strike to count
OI_VOLUME_RATIO_MAX = 5.0       # Volume/OI delta ratio: above this = noise, not positioning
SMART_VOL_RATIO = 2.0           # Volume/OI delta: below this = smart positioning
STRIKE_WEIGHT_MIN = 3           # Minimum strikes with confirmed OI building

# ── Proximity-to-ATM weighting ──
# OI building at ATM is far more significant than far-OTM positioning.
# Weight decays exponentially: weight = exp(-PROXIMITY_DECAY * dist_pct)
#   ATM (0%):  1.00
#   2% OTM:    0.55
#   5% OTM:    0.22
#   10% OTM:   0.05
PROXIMITY_DECAY = 30
PROXIMITY_FLOOR = 0.05           # Minimum weight for far-OTM strikes


def _proximity_weight(strike: float, underlying: float) -> float:
    """Weight OI changes by distance from underlying — ATM matters most."""
    if underlying <= 0:
        return 1.0
    dist_pct = abs(strike - underlying) / underlying
    return max(PROXIMITY_FLOOR, math.exp(-PROXIMITY_DECAY * dist_pct))


def _compute_oi_deltas(chain_current: dict, chain_prev: dict, underlying: float) -> dict:
    """Compute OI deltas for calls and puts, normalized by proximity to underlying.

    Returns dict with call_oi_delta, put_oi_delta, call_delta_strikes, put_delta_strikes,
    call_vol_delta, put_vol_delta, total_oi_delta.
    """
    result = {
        "call_oi_delta": 0,
        "put_oi_delta": 0,
        "call_delta_strikes": 0,
        "put_delta_strikes": 0,
        "call_vol_delta": 0,
        "put_vol_delta": 0,
        "total_oi_delta": 0,
        "smart_call_oi": 0,
        "smart_put_oi": 0,
        "noise_call_vol": 0,
        "noise_put_vol": 0,
        # Weighted by proximity to ATM (diagnostic)
        "weighted_smart_call": 0.0,
        "weighted_smart_put": 0.0,
        "total_proximity_weight": 0.0,
    }

    # Index previous contracts by side+strike for O(log n) lookup
    prev_index: dict[tuple[str, float], dict] = {}
    for side_key, side_label in [("calls", "call"), ("puts", "put")]:
        for c in chain_prev.get(side_key, []):
            stk = c.get("strike", 0) or 0
            if stk > 0:
                prev_index[(side_label, stk)] = c

    for side_key, side_label in [("calls", "call"), ("puts", "put")]:
        for current_c in chain_current.get(side_key, []):
            stk = current_c.get("strike", 0) or 0
            if stk <= 0:
                continue

            current_oi = current_c.get("openInterest", 0) or 0
            current_vol = current_c.get("volume", 0) or 0
            prev_c = prev_index.get((side_label, stk), {})
            prev_oi = prev_c.get("openInterest", 0) or 0
            prev_vol = prev_c.get("volume", 0) or 0

            oi_delta = current_oi - prev_oi
            vol_delta = current_vol - prev_vol

            # Only count meaningful OI changes (> minimum %)
            if prev_oi > 0 and abs(oi_delta) / prev_oi < OI_DELTA_PCT_MIN:
                oi_delta = 0

            if oi_delta > 0:
                vol_oi_ratio = abs(vol_delta) / oi_delta if oi_delta > 0 else 999
                prox_w = _proximity_weight(stk, underlying)
                if side_label == "call":
                    result["call_oi_delta"] += oi_delta
                    result["call_vol_delta"] += vol_delta
                    result["call_delta_strikes"] += 1
                    if vol_oi_ratio < SMART_VOL_RATIO:
                        result["smart_call_oi"] += oi_delta
                        result["weighted_smart_call"] += oi_delta * prox_w
                        result["total_proximity_weight"] += prox_w
                    elif vol_oi_ratio > OI_VOLUME_RATIO_MAX:
                        result["noise_call_vol"] += vol_delta * prox_w
                else:
                    result["put_oi_delta"] += oi_delta
                    result["put_vol_delta"] += vol_delta
                    result["put_delta_strikes"] += 1
                    if vol_oi_ratio < SMART_VOL_RATIO:
                        result["smart_put_oi"] += oi_delta
                        result["weighted_smart_put"] += oi_delta * prox_w
                        result["total_proximity_weight"] += prox_w
                    elif vol_oi_ratio > OI_VOLUME_RATIO_MAX:
                        result["noise_put_vol"] += vol_delta * prox_w

    return result


class OIChangeRate(BaseV3Strategy):
    name = "oi_change_rate"
    description = (
        "OI change rate analysis — distinguishes smart money positioning "
        "(OI building, moderate vol) from noise (high vol, flat OI). "
        "Net OI building direction gives trade signal."
    )
    applies_to = ("option",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        chain_current = context.get("chain_current", {})
        chain_prev = context.get("chain_prev", {})
        underlying = context.get("underlying_price", 0)

        if not chain_current or not chain_prev or underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        # Skip if this is the first snapshot (chain_prev == chain_current)
        if chain_current is chain_prev:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        deltas = _compute_oi_deltas(chain_current, chain_prev, underlying)

        total_oi_change = deltas["call_oi_delta"] + deltas["put_oi_delta"]
        if total_oi_change < OI_DELTA_MIN_TOTAL:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        total_strikes = deltas["call_delta_strikes"] + deltas["put_delta_strikes"]
        if total_strikes < STRIKE_WEIGHT_MIN:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        # ── Smart money OI analysis (proximity-weighted) ──
        smart_call = deltas["smart_call_oi"]
        smart_put = deltas["smart_put_oi"]
        smart_total = smart_call + smart_put
        weighted_smart_call = deltas["weighted_smart_call"]
        weighted_smart_put = deltas["weighted_smart_put"]
        weighted_total = weighted_smart_call + weighted_smart_put

        noise_call_vol = deltas["noise_call_vol"]
        noise_put_vol = deltas["noise_put_vol"]

        # ── ATM concentration: OI-weighted average proximity (1.0 = all ATM, ~0.05 = all far OTM) ──
        avg_proximity = weighted_total / max(smart_total, 1) if smart_total > 0 else 0.0

        # ── Determine signal ──
        # Net smart OI building direction
        net_smart = smart_call - smart_put

        if net_smart == 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        # Confidence: scales with proximity-weighted smart OI magnitude and smart-to-noise ratio
        smart_magnitude_score = min(weighted_total / 15000.0, 0.45)
        noise_total = noise_call_vol + noise_put_vol
        smart_noise_ratio = smart_total / max(noise_total, 1)
        ratio_score = min(smart_noise_ratio / 3.0, 0.25) if noise_total > 0 else 0.25

        # Strike breadth bonus: more strikes with OI building = broader conviction
        breadth = total_strikes
        breadth_score = min((breadth - STRIKE_WEIGHT_MIN) / 10.0, 0.15)

        # Directional skew: how one-sided is the OI building?
        total_oi_delta_all = deltas["call_oi_delta"] + deltas["put_oi_delta"]
        if total_oi_delta_all > 0:
            skew = abs(deltas["call_oi_delta"] - deltas["put_oi_delta"]) / total_oi_delta_all
        else:
            skew = 0
        skew_score = min(skew * 0.15, 0.15)

        confidence = smart_magnitude_score + ratio_score + breadth_score + skew_score
        confidence = max(0.0, min(confidence, 0.80))

        if confidence < 0.12:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        direction = "long" if net_smart > 0 else "short"

        return {
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "action": "buy",
            "smart_call_oi_delta": int(smart_call),
            "smart_put_oi_delta": int(smart_put),
            "weighted_smart_call": int(weighted_smart_call),
            "weighted_smart_put": int(weighted_smart_put),
            "noise_call_vol": int(noise_call_vol),
            "noise_put_vol": int(noise_put_vol),
            "smart_noise_ratio": round(smart_noise_ratio, 2),
            "strike_breadth": total_strikes,
            "net_smart_delta": int(net_smart),
            "avg_proximity": round(avg_proximity, 2),
            "signal_quality": "positioning" if smart_noise_ratio > 2.0 else "mixed",
        }
