import numpy as np
from kronos.strategies.v3.base import BaseV3Strategy


class PutCallDivergence(BaseV3Strategy):
    name = "put_call_divergence"
    description = "Put/call ratio divergence with price action"
    applies_to = ("option", "stock")
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        pc = context.get("pc_ratio", 1.0)
        pc_5da = context.get("pc_ratio_5day_avg", 1.0)
        chg_1d = context.get("price_change_1d", 0)
        chg_5d = context.get("price_change_5d", 0)
        dev = (pc - pc_5da) / max(pc_5da, 0.01)
        confidence = 0.0
        direction = "neutral"
        if chg_5d < -0.02 and dev < -0.15:
            confidence = min(abs(dev) * 2, 0.80)
            direction = "long"
        elif chg_5d > 0.02 and dev > 0.15:
            confidence = min(abs(dev) * 2, 0.75)
            direction = "short"
        elif chg_1d > 0.02 and pc < 0.5:
            confidence = 0.55
            direction = "long"
        elif chg_1d < -0.02 and pc > 0.7:
            confidence = 0.55
            direction = "short"
        # OI source penalty: if the P/C ratio fell back to OI (volume < 100)
        # and that OI came from yfinance backfill, penalize confidence by 30%.
        # Volume-based ratio is IBKR live data and needs no penalty.
        pc_source = context.get("pc_ratio_source", "oi")
        if pc_source == "oi":
            oi_source = context.get("pc_oi_source", "yfinance_eod")
            if oi_source == "yfinance_eod":
                confidence *= 0.7
        return {"direction": direction, "confidence": confidence, "action": "buy", "pc_ratio": pc, "deviation": dev, "strategy": self.name}
