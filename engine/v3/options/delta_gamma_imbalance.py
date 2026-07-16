"""
Delta-Gamma Imbalance — Crash-Up / Flash-Crash Feedback Loop Detection.

When delta positioning is extremely one-sided AND gamma flip is approaching
AND volatility is elevated, dealer hedging creates a mechanical feedback loop:

  CRASH-UP (bullish feedback):
    - Net delta is heavily positive (dealers short calls/puts)
    - Price is BELOW gamma flip and rising toward it
    - Dealers BUY underlying to hedge as gamma increases
    - Buying drives price higher → gamma increases more → MORE buying
    - VIX elevated amplifies the effect (larger gamma)

  FLASH-CRASH (bearish feedback):
    - Net delta is heavily negative (dealers long puts)
    - Price is ABOVE gamma flip and falling toward it
    - Dealers SELL underlying to hedge as gamma increases
    - Selling drives price lower → gamma increases more → MORE selling

These are the 3-5 sigma events most strategies miss because they assume
linear price behavior.  The feedback loop creates NON-linear acceleration.

The strategy detects:
  1. Delta-gamma imbalance magnitude (how one-sided is positioning?)
  2. Proximity to gamma flip (how close to the acceleration zone?)
  3. Velocity toward flip (how fast is price moving there?)
  4. VIX regime (amplification factor)
"""
from __future__ import annotations
from kronos.strategies.v3.base import BaseV3Strategy
from utils.logger import get_logger

logger = get_logger("engine.v3.options.delta_gamma_imbalance")

# ── Delta thresholds ──
DELTA_MIN_MAGNITUDE = 500000       # Minimum net delta * OI to consider
DELTA_HEAVY = 2000000              # Heavy one-sided positioning
DELTA_EXTREME = 5000000            # Extreme imbalance — very rare

# ── Gamma flip proximity thresholds ──
FLIP_PROXIMITY_FAR = 0.03          # 3% from flip — early warning
FLIP_PROXIMITY_NEAR = 0.015         # 1.5% from flip — active zone
FLIP_PROXIMITY_CRITICAL = 0.005    # 0.5% from flip — imminent

# ── VIX thresholds ──
VIX_ELEVATED = 20                  # VIX > 20 → elevated, gamma effects amplified
VIX_HIGH = 30                      # VIX > 30 → high vol, feedback loops violent


def _compute_delta_imbalance(context: dict, underlying: float) -> dict:
    """Compute delta positioning asymmetry from chain data.

    Returns dict with net_delta, call_delta, put_delta, delta_skew.
    """
    chain = context.get("option_chain", {})
    delta_pos = context.get("delta_positioning", 0)

    # delta_positioning from option_metrics is a float (net delta * OI)
    # If it's a dict (legacy), extract net_delta
    if isinstance(delta_pos, dict):
        net_delta = delta_pos.get("net_delta", 0)
    elif isinstance(delta_pos, (int, float)):
        net_delta = float(delta_pos)
    else:
        net_delta = 0.0

    # Also compute from chain as fallback
    if abs(net_delta) < DELTA_MIN_MAGNITUDE and chain:
        call_delta_total = 0.0
        put_delta_total = 0.0
        for c in chain.get("calls", []):
            d = c.get("delta", 0) or 0
            oi = c.get("openInterest", 0) or 0
            call_delta_total += d * oi
        for p in chain.get("puts", []):
            d = p.get("delta", 0) or 0
            oi = p.get("openInterest", 0) or 0
            put_delta_total += abs(d) * oi
        # Dealer delta: they're short what customers are long
        # If call delta > put delta, dealers are net short → bearish pressure
        # But for this strategy, we care about the IMBALANCE magnitude
        fallback_delta = call_delta_total - put_delta_total
        if abs(fallback_delta) > abs(net_delta):
            net_delta = fallback_delta

    return {
        "net_delta": net_delta,
        "delta_magnitude": abs(net_delta),
        "delta_sign": 1 if net_delta > 0 else -1 if net_delta < 0 else 0,
    }


class DeltaGammaImbalance(BaseV3Strategy):
    name = "delta_gamma_imbalance"
    description = (
        "Detects delta-gamma feedback loops (crash-up / flash-crash). "
        "When delta positioning is extreme AND gamma flip is approaching "
        "AND VIX is elevated, dealer hedging creates non-linear price "
        "acceleration. Catches 3-5 sigma events."
    )
    applies_to = ("option",)
    default_weight = 0.14

    def compute(self, context: dict) -> dict:
        underlying = context.get("underlying_price", 0)
        gamma_flip = context.get("gamma_flip_level", 0)
        vix = context.get("vix_spot", 0)

        if underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        # ── Delta imbalance ──
        delta_info = _compute_delta_imbalance(context, underlying)
        delta_mag = delta_info["delta_magnitude"]
        delta_sign = delta_info["delta_sign"]

        if delta_mag < DELTA_MIN_MAGNITUDE:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        # ── Gamma flip proximity ──
        if gamma_flip <= 0:
            # No flip level computed — fallback: use extreme delta alone
            # Only fires on extreme delta without flip context
            if delta_mag < DELTA_EXTREME:
                return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
            dist_pct = 0.0
            proximity_level = "no_flip"
        else:
            dist_pct = abs(underlying - gamma_flip) / underlying
            if dist_pct > FLIP_PROXIMITY_FAR:
                # Too far from flip — use only extreme delta
                if delta_mag < DELTA_HEAVY:
                    return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
                proximity_level = "far"
            elif dist_pct > FLIP_PROXIMITY_NEAR:
                proximity_level = "near"
            elif dist_pct > FLIP_PROXIMITY_CRITICAL:
                proximity_level = "critical"
            else:
                proximity_level = "imminent"

        # ── Direction: is price moving TOWARD or AWAY from gamma flip? ──
        price_change = context.get("price_change_1d", 0)
        daily_range = context.get("daily_range", 0)

        if gamma_flip > 0:
            moving_toward_flip = (
                (underlying < gamma_flip and price_change > 0) or
                (underlying > gamma_flip and price_change < 0)
            )
        else:
            moving_toward_flip = True  # no flip reference, use delta alone

        if not moving_toward_flip:
            # Moving away from flip = less urgency
            confidence_mult = 0.40
        else:
            confidence_mult = 1.0

        # ── Velocity scoring ──
        # daily_range from option_metrics is already a ratio ((high-low)/low)
        if daily_range > 0:
            velocity = abs(price_change) / max(daily_range, 0.001)
        else:
            velocity = 0.0
        vel_score = min(velocity * 0.20, 0.25)

        # ── Delta magnitude scoring ──
        if delta_mag >= DELTA_EXTREME:
            delta_score = 0.40
        elif delta_mag >= DELTA_HEAVY:
            delta_score = 0.30
        else:
            delta_score = min(delta_mag / DELTA_HEAVY * 0.25, 0.25)

        # ── Proximity scoring ──
        prox_score = 0.0
        if gamma_flip > 0:
            if proximity_level == "imminent":
                prox_score = 0.25
            elif proximity_level == "critical":
                prox_score = 0.20
            elif proximity_level == "near":
                prox_score = 0.12
            elif proximity_level == "far":
                prox_score = 0.05
            else:
                prox_score = 0.03

        # ── VIX amplification ──
        if vix >= VIX_HIGH:
            vix_mult = 1.40   # high vol amplifies gamma effects
            vix_label = "high"
        elif vix >= VIX_ELEVATED:
            vix_mult = 1.15   # elevated vol
            vix_label = "elevated"
        else:
            vix_mult = 0.85   # low vol dampens feedback loops
            vix_label = "low"

        # ── Combine ──
        base_conf = delta_score + prox_score + vel_score
        confidence = base_conf * confidence_mult * vix_mult
        confidence = max(0.0, min(confidence, 0.88))

        if confidence < 0.15:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        # ── Determine signal direction ──
        # Delta > 0 means dealers are net-short calls (customers long calls)
        # When price rises toward gamma flip, dealers must buy MORE → bullish feedback
        # Simplification: use the delta sign and flip approach direction
        if gamma_flip > 0:
            if underlying < gamma_flip:
                # Price below flip → approaching from below → dealers buy → BULLISH
                direction = "long"
            else:
                # Price above flip → approaching from above → dealers sell → BEARISH
                direction = "short"
        else:
            # No flip — use delta alone
            # Positive delta = dealers net short → bullish risk (dealers must buy)
            direction = "long" if delta_sign > 0 else "short"

        loop_type = (
            "crash_up_risk" if direction == "long"
            else "flash_crash_risk"
        )

        return {
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "action": "buy",
            "delta_magnitude": int(delta_mag),
            "gamma_flip_level": round(gamma_flip, 2) if gamma_flip > 0 else None,
            "dist_to_flip_pct": round(dist_pct * 100, 2) if gamma_flip > 0 else None,
            "proximity": proximity_level,
            "velocity": round(velocity, 2),
            "vix_regime": vix_label,
            "vix_spot": round(vix, 1),
            "feedback_type": loop_type,
        }
