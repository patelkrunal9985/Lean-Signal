"""
Market Breadth Confirmation Strategy.

Uses market breadth metrics to confirm or diverge index option signals.
This is a CONTEXTUAL strategy — it doesn't generate standalone signals
but rather confirms whether the underlying index move is supported by
broad market participation.

Signal types:
  1. BREADTH CONFIRMATION (trend-following):
     - Underlying moving up + breadth bullish → LONG with high confidence
     - Underlying moving down + breadth bearish → SHORT with high confidence

  2. BREADTH DIVERGENCE (mean-reversion):
     - Underlying up but breadth bearish → SHORT (fade the narrow move)
     - Underlying down but breadth bullish → LONG (buy the dip)

  3. BREADTH THRUST (momentum ignition):
     - All futures surging together + VIX confirming → aggressive follow-through
     - Highest confidence signal — rare but very reliable

  4. TECH DIVERGENCE (sector-specific):
     - NDX/QQQ options: if tech is leading vs broad market → bullish
     - NDX/QQQ options: if tech is lagging → bearish

  5. SMALL-CAP DIVERGENCE:
     - RTY lagging large caps → narrow breadth → fade large-cap moves
"""
from __future__ import annotations
from engine.v3.base import BaseV3Strategy
from utils.logger import get_logger

logger = get_logger("engine.v3.options.breadth_confirmation")

# ── Confidence weights ──
CONFIRMATION_BASE = 0.30          # Base confidence for confirmed moves
DIVERGENCE_BASE = 0.25            # Base for divergence signals
THRUST_BASE = 0.45                # Base for breadth thrust signals
TECH_BIAS_WEIGHT = 0.15           # Additional weight for tech divergence
SMALL_CAP_DIVERGENCE_BOOST = 0.10 # Extra confidence when RTY diverges

# ── Underlying direction threshold ──
PRICE_CHANGE_MIN = 0.002          # 0.2% move minimum to fire


class BreadthConfirmation(BaseV3Strategy):
    name = "breadth_confirmation"
    description = (
        "Market breadth confirmation for index options — validates underlying "
        "moves with broad market participation. Detects confirmations (follow), "
        "divergences (fade), and breadth thrust (momentum ignition). Uses "
        "ES/NQ/YM/RTY futures alignment + VIX + small-cap participation."
    )
    applies_to = ("option",)
    default_weight = 0.13

    def compute(self, context: dict) -> dict:
        breadth = context.get("market_breadth", {})
        if not breadth.get("available"):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        underlying = context.get("underlying_price", 0)
        ticker = context.get("ticker", "")
        price_change = context.get("price_change_1d", 0)

        # ── Determine which index this option is on ──
        is_tech = any(t in ticker for t in ("QQQ", "NDX"))
        is_spx = "SPX" in ticker or "SPY" in ticker

        # ── Extract breadth metrics ──
        composite = breadth.get("composite", {})
        composite_score = composite.get("composite_score", 0)
        composite_state = composite.get("state", "neutral")

        futures_alignment = breadth.get("futures_alignment", {})
        alignment_ratio = futures_alignment.get("alignment_ratio", 0)
        alignment_direction = futures_alignment.get("direction", "neutral")

        tech_div = breadth.get("tech_divergence", 0)
        small_cap = breadth.get("small_cap", {})
        vix_confirm = breadth.get("vix_confirmation", {})
        thrust = breadth.get("breadth_thrust", {})
        breadth_trend = breadth.get("breadth_trend", "stable")

        # ── Not enough data to fire ──
        if alignment_ratio == 0 and composite_score == 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        direction = "neutral"
        confidence = 0.0
        signal_type = "none"

        # ═══════════════════════════════════════════════════════════
        # SIGNAL 1: Breadth Thrust (highest priority)
        # ═══════════════════════════════════════════════════════════
        if thrust.get("thrust_active"):
            thrust_dir = thrust.get("thrust_direction", "none")
            if thrust_dir == "up":
                direction = "long"
                confidence = THRUST_BASE
                signal_type = "breadth_thrust_bullish"
            elif thrust_dir == "down":
                direction = "short"
                confidence = THRUST_BASE
                signal_type = "breadth_thrust_bearish"

            # Boost if price is also moving in same direction
            if direction == "long" and price_change > 0:
                confidence += 0.15
            elif direction == "short" and price_change < 0:
                confidence += 0.15

            # Boost for improving breadth trend
            if breadth_trend == "improving" and direction == "long":
                confidence += 0.05

        # ═══════════════════════════════════════════════════════════
        # SIGNAL 2: Confirmation (breadth supports price direction)
        # ═══════════════════════════════════════════════════════════
        if direction == "neutral" and abs(price_change) >= PRICE_CHANGE_MIN:
            if price_change > 0 and composite_score >= 0.30:
                # Price up + breadth bullish = confirmed uptrend
                direction = "long"
                confidence = CONFIRMATION_BASE
                # Scale with composite strength
                confidence += composite_score * 0.20
                signal_type = "confirmed_uptrend"

            elif price_change < 0 and composite_score <= -0.30:
                # Price down + breadth bearish = confirmed downtrend
                direction = "short"
                confidence = CONFIRMATION_BASE
                confidence += abs(composite_score) * 0.20
                signal_type = "confirmed_downtrend"

        # ═══════════════════════════════════════════════════════════
        # SIGNAL 3: Divergence (breadth contradicts price = reversal)
        # ═══════════════════════════════════════════════════════════
        if direction == "neutral" and abs(price_change) >= PRICE_CHANGE_MIN:
            if price_change > 0 and composite_score <= -0.20:
                # Price up but breadth bearish = bull trap → SHORT
                direction = "short"
                confidence = DIVERGENCE_BASE
                confidence += abs(composite_score) * 0.15
                signal_type = "bearish_divergence"

            elif price_change < 0 and composite_score >= 0.20:
                # Price down but breadth bullish = bear trap → LONG
                direction = "long"
                confidence = DIVERGENCE_BASE
                confidence += composite_score * 0.15
                signal_type = "bullish_divergence"

        # ═══════════════════════════════════════════════════════════
        # SIGNAL 4: VIX Divergence (fallback when composite is neutral)
        # Only fires when price has moved meaningfully
        # ═══════════════════════════════════════════════════════════
        if direction == "neutral" and abs(price_change) >= PRICE_CHANGE_MIN:
            vix_state = vix_confirm.get("state", "neutral_low_vol")

            if vix_state == "bearish_divergence":
                # VIX rising + market flat/up = fear → SHORT
                direction = "short"
                confidence = 0.20
                signal_type = "vix_bearish_divergence"

            elif vix_state == "bullish_divergence":
                # VIX falling + market flat/down = complacency fading → LONG
                direction = "long"
                confidence = 0.18
                signal_type = "vix_bullish_divergence"

        # ═══════════════════════════════════════════════════════════
        # TECH-SPECIFIC ADJUSTMENTS (QQQ/NDX options)
        # ═══════════════════════════════════════════════════════════
        if is_tech and direction != "neutral":
            if abs(tech_div) > 0.003:
                if tech_div > 0:
                    # Tech leading → bullish for QQQ/NDX
                    if direction == "long":
                        confidence += TECH_BIAS_WEIGHT
                    elif direction == "short":
                        confidence -= 0.05  # weaken counter-tech shorts
                else:
                    # Tech lagging → bearish for QQQ/NDX
                    if direction == "short":
                        confidence += TECH_BIAS_WEIGHT
                    elif direction == "long":
                        confidence -= 0.05

        # ═══════════════════════════════════════════════════════════
        # SMALL-CAP DIVERGENCE BOOST
        # ═══════════════════════════════════════════════════════════
        if small_cap.get("lagging") and direction != "neutral":
            # Small caps lagging = narrow breadth = divergence signals stronger
            if signal_type and "divergence" in signal_type:
                confidence += SMALL_CAP_DIVERGENCE_BOOST
                signal_type = f"rtylag_{signal_type}"
            elif signal_type and "confirmed" in signal_type:
                # Narrow breadth weakens confirmation
                confidence -= 0.05

        # ── Clamp ──
        confidence = max(0.0, min(confidence, 0.85))

        if confidence < 0.10:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        return {
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "action": "buy",
            "signal_type": signal_type,
            "composite_score": composite_score,
            "alignment_ratio": alignment_ratio,
            "vix_state": vix_confirm.get("state", "unknown"),
            "tech_divergence": round(tech_div, 4),
            "small_cap_participating": small_cap.get("participating", True),
            "breadth_trend": breadth_trend,
            "thrust_active": thrust.get("thrust_active", False),
        }
