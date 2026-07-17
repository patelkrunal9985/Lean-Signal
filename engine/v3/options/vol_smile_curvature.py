"""
Volatility Smile Curvature — Wing Premium & Smile Dynamics.

While IV Skew measures the SLOPE (put IV minus call IV), this strategy
measures the CURVATURE of the volatility smile — how much OTM wing options
are bidding up relative to ATM options.

Physics:
  - FLAT smile:    Market expects range-bound, low tail risk
  - STEEP wings:   Market pricing in tail risk (crash protection or upside FOMO)
  - Asymmetry:     Put wing premium > call wing premium → bearish tail risk
                   Call wing premium > put wing premium → bullish breakout risk
  - Acceleration:  Rate of change of curvature is more predictive than level

This is a LEADING indicator — wing premium often expands BEFORE price moves.
The strategy detects:
  1. Absolute curvature: are wings expensive relative to the body?
  2. Asymmetry: is one wing bidding up faster than the other?
  3. Shift: is the entire smile shifting up/down?
"""
from __future__ import annotations
from engine.v3.base import BaseV3Strategy
from utils.logger import get_logger

logger = get_logger("engine.v3.options.vol_smile_curvature")

# ── Wing distance (as % from underlying) ──
WING_DISTANCE_PCT = 0.05       # 5% OTM for wing measurement
FAR_WING_DISTANCE_PCT = 0.10   # 10% OTM for far wing (tail risk)

# ── Curvature thresholds ──
BUTTERFLY_MIN = 0.02           # Minimum butterfly spread (2 IV points) to fire
BUTTERFLY_STRONG = 0.05        # Strong signal
ASYMMETRY_THRESHOLD = 0.03     # Wing asymmetry threshold (3 IV points)

# ── Signal thresholds ──
TAIL_RISK_BEARISH = 0.06       # Put wing premium > 6 IV points → crash risk
TAIL_RISK_BULLISH = 0.04       # Call wing premium > 4 IV points → breakout FOMO


def _compute_atm_iv(chain: dict, underlying: float) -> float:
    """Compute ATM IV from nearest-strike calls and puts."""
    calls = chain.get("calls", [])
    puts = chain.get("puts", [])
    if not calls or not puts:
        return 0.0

    nearest_call = min(calls, key=lambda c: abs((c.get("strike", 0) or 0) - underlying))
    nearest_put = min(puts, key=lambda p: abs((p.get("strike", 0) or 0) - underlying))

    call_iv = nearest_call.get("impliedVolatility", 0) or 0
    put_iv = nearest_put.get("impliedVolatility", 0) or 0

    if call_iv > 0 and put_iv > 0:
        return (call_iv + put_iv) / 2
    return max(call_iv, put_iv)


def _compute_wing_iv(chain: dict, underlying: float, side: str, distance_pct: float) -> float:
    """Compute average IV at wing strikes (OTM call or put).

    side: 'call' or 'put'
    distance_pct: e.g., 0.05 = 5% OTM
    """
    target_strike = underlying * (1 + distance_pct) if side == "call" else underlying * (1 - distance_pct)
    contracts = chain.get("calls" if side == "call" else "puts", [])

    if not contracts:
        return 0.0

    # Find contracts within 2% of target strike (relative to underlying)
    nearby = [
        c for c in contracts
        if abs((c.get("strike", 0) or 0) - target_strike) / underlying < 0.02
    ]
    if not nearby:
        # Fall back to closest strike
        nearby = sorted(contracts, key=lambda c: abs((c.get("strike", 0) or 0) - target_strike))[:3]

    ivs = [c.get("impliedVolatility", 0) or 0 for c in nearby if (c.get("impliedVolatility", 0) or 0) > 0]
    if not ivs:
        return 0.0
    return sum(ivs) / len(ivs)


def _compute_smile_metrics(chain: dict, underlying: float) -> dict:
    """Compute all smile curvature metrics.

    Returns dict with:
      - atm_iv: ATM IV
      - put_wing_iv: 5% OTM put IV
      - call_wing_iv: 5% OTM call IV
      - far_put_wing_iv: 10% OTM put IV
      - far_call_wing_iv: 10% OTM call IV
      - butterfly: (put_wing + call_wing)/2 - atm_iv (curvature)
      - far_butterfly: far wing curvature (tail risk)
      - asymmetry: put_wing - call_wing (skew within wings)
      - smile_level: atm_iv level vs historical (normalized)
    """
    atm_iv = _compute_atm_iv(chain, underlying)
    put_wing_iv = _compute_wing_iv(chain, underlying, "put", WING_DISTANCE_PCT)
    call_wing_iv = _compute_wing_iv(chain, underlying, "call", WING_DISTANCE_PCT)
    far_put_wing_iv = _compute_wing_iv(chain, underlying, "put", FAR_WING_DISTANCE_PCT)
    far_call_wing_iv = _compute_wing_iv(chain, underlying, "call", FAR_WING_DISTANCE_PCT)

    if atm_iv <= 0:
        return {"atm_iv": 0.0}

    butterfly = (put_wing_iv + call_wing_iv) / 2 - atm_iv if put_wing_iv > 0 and call_wing_iv > 0 else 0.0
    far_butterfly = (far_put_wing_iv + far_call_wing_iv) / 2 - atm_iv if far_put_wing_iv > 0 and far_call_wing_iv > 0 else 0.0
    asymmetry = put_wing_iv - call_wing_iv if put_wing_iv > 0 and call_wing_iv > 0 else 0.0
    far_asymmetry = far_put_wing_iv - far_call_wing_iv if far_put_wing_iv > 0 and far_call_wing_iv > 0 else 0.0

    return {
        "atm_iv": round(atm_iv, 2),
        "put_wing_iv": round(put_wing_iv, 2),
        "call_wing_iv": round(call_wing_iv, 2),
        "far_put_wing_iv": round(far_put_wing_iv, 2),
        "far_call_wing_iv": round(far_call_wing_iv, 2),
        "butterfly": round(butterfly, 2),
        "far_butterfly": round(far_butterfly, 2),
        "asymmetry": round(asymmetry, 2),
        "far_asymmetry": round(far_asymmetry, 2),
    }


class VolSmileCurvature(BaseV3Strategy):
    name = "vol_smile_curvature"
    description = (
        "Volatility smile curvature analysis — detects tail risk pricing via OTM "
        "wing premium expansion. Leading indicator: wings bid up BEFORE price moves. "
        "Measures butterfly spread (curvature) and put/call wing asymmetry."
    )
    applies_to = ("option",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        if underlying <= 0 or not chain:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        metrics = _compute_smile_metrics(chain, underlying)
        if metrics["atm_iv"] <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        butterfly = metrics["butterfly"]
        far_butterfly = metrics["far_butterfly"]
        asymmetry = metrics["asymmetry"]
        far_asymmetry = metrics["far_asymmetry"]
        atm_iv = metrics["atm_iv"]
        vix = context.get("vix_spot", 0)

        # ── No meaningful curvature → neutral ──
        if abs(butterfly) < BUTTERFLY_MIN and abs(far_butterfly) < BUTTERFLY_MIN:
            return {
                "direction": "neutral",
                "confidence": 0.0,
                "strategy": self.name,
                "butterfly": butterfly,
                "far_butterfly": far_butterfly,
                "asymmetry": asymmetry,
            }

        # ── Signal logic ──
        # 1. Far-wing butterfly (tail risk) weight: 60%
        # 2. Wing asymmetry weight: 25%
        # 3. IV level vs VIX weight: 15%

        direction = "neutral"
        confidence = 0.0

        # ── Tail risk scoring ──
        # Far put wing premium → crash risk being priced → bearish
        # Far call wing premium → upside FOMO → bullish
        # BUT: elevated wings generally mean uncertainty → bearish tilt
        if far_butterfly > BUTTERFLY_STRONG:
            # Wings are very expensive → tail risk → bearish
            tail_score = min(far_butterfly / 0.15, 0.50)
            direction = "short"
        elif far_butterfly > BUTTERFLY_MIN:
            tail_score = min(far_butterfly / 0.10, 0.35)
            direction = "short"
        elif far_butterfly < -BUTTERFLY_MIN:
            # Wings are cheap (smile flattened) → complacency → neutral/bullish
            # But this is a weaker signal
            tail_score = min(abs(far_butterfly) / 0.10, 0.20)
            direction = "long"
        else:
            tail_score = 0.0

        # ── Asymmetry scoring ──
        # Put wing > call wing → crash protection demand → bearish
        # Call wing > put wing → upside FOMO → bullish
        asym_direction = "neutral"
        asym_score = 0.0
        if abs(far_asymmetry) > ASYMMETRY_THRESHOLD:
            asym_score = min(abs(far_asymmetry) / 0.10, 0.25)
            if far_asymmetry > 0:
                asym_direction = "short"  # put wing premium
            else:
                asym_direction = "long"   # call wing premium

        # ── IV regime (vs VIX) ──
        iv_score = 0.0
        if vix > 0 and atm_iv > 0:
            iv_vix_ratio = atm_iv / max(vix, 1)
            if iv_vix_ratio > 1.3:
                iv_score = min((iv_vix_ratio - 1.3) / 0.5, 0.15)
            elif iv_vix_ratio < 0.85:
                iv_score = min((0.85 - iv_vix_ratio) / 0.3, 0.10)

        # ── Combine ──
        if tail_score > 0 and direction != "neutral":
            confidence = tail_score * 0.60 + asym_score * 0.25 + iv_score * 0.15
            # If asymmetry aligns with tail direction, boost
            if asym_direction == direction:
                confidence *= 1.20
            elif asym_direction != "neutral":
                confidence *= 0.70  # asymmetry contradicts → weaker signal
        elif asym_score > 0.15 and asym_direction != "neutral":
            # No strong tail signal, but asymmetry is significant
            direction = asym_direction
            confidence = asym_score * 0.50 + iv_score * 0.20
        else:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        confidence = max(0.0, min(confidence, 0.80))
        if confidence < 0.10:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        return {
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "action": "sell" if direction == "short" else "buy",
            "butterfly": butterfly,
            "far_butterfly": far_butterfly,
            "asymmetry": asymmetry,
            "far_asymmetry": far_asymmetry,
            "atm_iv": atm_iv,
            "vix_spot": vix,
            "signal_type": (
                "tail_risk_priced" if direction == "short" and far_butterfly > BUTTERFLY_MIN
                else "breakout_fomo" if direction == "long" and far_asymmetry < -ASYMMETRY_THRESHOLD
                else "asymmetry_shift"
            ),
        }
