"""
Vanna/Charm Flow Strategy — dealer hedging flow prediction for 0DTE options.

Physics:
  Charm = dDelta/dTime   → dealer delta change from time decay
  Vanna = dDelta/dVol    → dealer delta change from IV moves

On 0DTE, Charm dominates in the final hours — dealers MUST hedge delta
drift to stay neutral.  This strategy predicts the direction and intensity
of that forced flow.

  charm > 0 ("long"):  dealers get longer as time passes
                       → SELL underlying to hedge → BEARISH signal
  charm < 0 ("short"): dealers get shorter as time passes
                       → BUY underlying to hedge  → BULLISH signal

Vanna alignment amplifies: if Vanna and Charm share the same sign, both
forces push dealers in the same direction → stronger signal.

Time weighting: Charm accelerates as t→0 (≈ 1/√t behavior).  Power hour
and closing pin windows get the highest multipliers.
"""
from __future__ import annotations
from kronos.strategies.v3.base import BaseV3Strategy
from utils.logger import get_logger

logger = get_logger("engine.v3.options.vanna_charm_flow")

# ── Charm magnitude thresholds ──
CHARM_MIN_MAGNITUDE = 0.0005       # minimum to fire (ignore trivial flow)
CHARM_STRONG_MAGNITUDE = 0.003     # strong signal threshold
CHARM_EXTREME_MAGNITUDE = 0.008    # extreme — very high conviction

# ── Vanna alignment bonus ──
VANNA_ALIGN_BONUS = 0.12            # added confidence when Vanna same sign as Charm
VANNA_DIVERGE_PENALTY = 0.55        # multiplier when Vanna opposite sign
VANNA_NEUTRAL_PENALTY = 0.80        # multiplier when Vanna ≈ 0

# ── Time-window multipliers (Charm accelerates in final hours) ──
TIME_WINDOW_MULTIPLIERS: dict[str, float] = {
    "opening_drive":   0.6,   # Charm less predictive early in the day
    "morning_session": 0.8,
    "midday_lull":     0.6,   # choppy, less forced hedging
    "power_hour":      1.6,   # Charm accelerates significantly
    "closing_pin":     2.0,   # settlement mechanics — most predictable
}

# ── DTE multipliers ──
DTE_MULTIPLIERS = {
    0:  1.5,   # expiry day — Charm dominates
    1:  0.9,   # 1 DTE — still relevant
    2:  0.6,   # 2 DTE — fading
    3:  0.35,  # 3+ DTE — minor
}

# ── Direction mapping ──
# charm > 0 → dealers go longer → sell to hedge → SHORT signal
# charm < 0 → dealers go shorter → buy to hedge → LONG signal


class VannaCharmFlow(BaseV3Strategy):
    name = "vanna_charm_flow"
    description = "Predicts dealer hedging direction from Vanna/Charm exposure — amplified in final hour for 0DTE"
    applies_to = ("option",)
    default_weight = 0.12

    def compute(self, context: dict) -> dict:
        # ── Extract Charm data from option_metrics ──
        charm_direction = context.get("charm_direction", "neutral")
        charm_magnitude = context.get("charm_magnitude", 0) or 0
        total_charm = context.get("total_charm", 0) or 0
        total_vanna = context.get("total_vanna", 0) or 0
        call_charm = context.get("call_charm", 0) or 0
        put_charm = context.get("put_charm", 0) or 0
        call_vanna = context.get("call_vanna", 0) or 0
        put_vanna = context.get("put_vanna", 0) or 0
        dte = context.get("dte", None)

        # ── Guard: need meaningful Charm data ──
        if charm_direction == "neutral" or charm_magnitude < CHARM_MIN_MAGNITUDE:
            return {
                "direction": "neutral",
                "confidence": 0.0,
                "strategy": self.name,
            }

        # ── Determine signal direction (Charm → dealer hedging) ──
        # Charm > 0 → dealers get longer → sell to hedge → SHORT
        # Charm < 0 → dealers get shorter → buy to hedge → LONG
        signal_direction = "short" if charm_direction == "long" else "long"

        # ── Base confidence from Charm magnitude ──
        # Log-scale so small increases in magnitude map to diminishing returns
        conf_base = min(charm_magnitude * 80, 0.55)

        # ── Vanna alignment: same sign amplifies, opposite dampens ──
        vanna_sign = 1 if total_vanna > 0 else -1 if total_vanna < 0 else 0
        charm_sign = 1 if charm_direction == "long" else -1  # always non-neutral here

        if vanna_sign == charm_sign:
            conf_base += VANNA_ALIGN_BONUS
            vanna_state = "aligned"
        elif vanna_sign == 0:
            conf_base *= VANNA_NEUTRAL_PENALTY
            vanna_state = "neutral"
        else:
            conf_base *= VANNA_DIVERGE_PENALTY
            vanna_state = "divergent"

        # ── Charm asymmetry bonus ──
        # If call_charm and put_charm reinforce each other (both same sign
        # and same direction), dealers on BOTH sides hedge the same way.
        call_charm_sign = 1 if call_charm > 0 else -1 if call_charm < 0 else 0
        put_charm_sign = 1 if put_charm > 0 else -1 if put_charm < 0 else 0
        charm_asymmetry = 0.0
        if call_charm_sign == put_charm_sign == charm_sign:
            # Both call and put dealers hedge same direction — reinforced
            charm_asymmetry = 0.08
            asymmetry_state = "reinforced"
        elif call_charm_sign != put_charm_sign and abs(call_charm) > 0 and abs(put_charm) > 0:
            # Calls and puts have opposing Charm — weaker signal
            conf_base *= 0.75
            asymmetry_state = "conflicting"
        else:
            asymmetry_state = "neutral"

        conf_base += charm_asymmetry

        # ── DTE multiplier ──
        dte_val = dte if isinstance(dte, (int, float)) else 0
        dte_mult = DTE_MULTIPLIERS.get(int(min(max(dte_val, 0), 3)), 0.35)

        # ── Time window multiplier ──
        from engine.time_of_day import get_time_window
        time_win = get_time_window()
        time_mult = TIME_WINDOW_MULTIPLIERS.get(time_win, 0.6)

        # ── Combine ──
        confidence = conf_base * dte_mult * time_mult
        confidence = max(0.0, min(confidence, 0.85))

        # Extra boost for extreme Charm magnitude + aligned Vanna in closing
        if (
            charm_magnitude >= CHARM_EXTREME_MAGNITUDE
            and vanna_state == "aligned"
            and time_win in ("power_hour", "closing_pin")
        ):
            confidence = min(confidence * 1.3, 0.90)

        # ── Build reasoning ──
        reasoning_parts = [
            f"Dealer Charm {charm_direction} (mag={charm_magnitude:.4f})",
            f"→ {signal_direction.upper()} under hedging pressure",
            f"Vanna={vanna_state} | asymmetry={asymmetry_state}",
            f"DTE={dte_val} | window={time_win}",
            f"total_charm={total_charm:.1f} total_vanna={total_vanna:.1f}",
        ]

        return {
            "direction": signal_direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "action": "sell" if signal_direction == "short" else "buy",
            "reasoning": " | ".join(reasoning_parts),
            "charm_direction": charm_direction,
            "charm_magnitude": round(charm_magnitude, 6),
            "vanna_state": vanna_state,
            "asymmetry_state": asymmetry_state,
            "dte": dte_val,
            "time_window": time_win,
        }
