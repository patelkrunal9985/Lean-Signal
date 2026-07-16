import logging

from kronos.strategies.v3.base import BaseV3Strategy

logger = logging.getLogger(__name__)


class CarryYield(BaseV3Strategy):
    name = "carry_yield"
    description = "Futures carry/roll yield — contango vs backwardation"
    applies_to = ("future",)
    default_weight = 0.05

    def compute(self, context: dict) -> dict:
        # ── IBKR-primary: _build_v3_context already populated next_month_price via
        #     _v3_next_future() which uses IBKR reqMktData (live) → historical bars fallback.
        #     next_hist provides the full price series for front_vs_next spread analysis.
        front = context.get("front_month_price", 0)
        nxt = context.get("next_month_price", 0)
        # ── Fallback: use last bar of next_hist if live next_month_price unavailable ──
        if nxt <= 0:
            next_hist = context.get("next_hist", [])
            if next_hist and len(next_hist) >= 1:
                nxt = next_hist[-1]
        if front <= 0 or nxt <= 0:
            logger.info("carry_yield: neutral (front=%s, next=%s) — one or both missing", front, nxt)
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        annualized = (nxt / front - 1) * (365 / 90)
        logger.info("carry_yield: front=%s next=%s annualized=%s", front, nxt, round(annualized, 6))
        if annualized > 0.015:
            return {"direction": "short", "confidence": min(annualized * 6, 0.75), "annualized_carry": annualized, "carry_desc": "contango", "strategy": self.name}
        elif annualized < -0.015:
            return {"direction": "long", "confidence": min(abs(annualized) * 6, 0.75), "annualized_carry": annualized, "carry_desc": "backwardation", "strategy": self.name}
        logger.info("carry_yield: neutral (annualized %s within ±0.015 threshold)", round(annualized, 6))
        return {"direction": "neutral", "confidence": 0.0, "annualized_carry": annualized, "strategy": self.name}
