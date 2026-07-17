import numpy as np
from engine.v3.base import BaseV3Strategy


class IVSkew(BaseV3Strategy):
    name = "iv_skew"
    description = "Put vs call implied volatility skew"
    applies_to = ("option",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        atm = context.get("underlying_price", 0)
        if atm == 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Use actual delta from IBKR chain records if available (tick type 106 provides greeks).
        # Fall back to strike-distance proxy only if delta is missing.
        # 25-delta is typically 5-15% OTM depending on DTE and IV.
        puts_by_delta = [p for p in chain.get("puts", []) if abs(abs(p.get("delta", 0) or 0) - 0.25) <= 0.05]
        calls_by_delta = [c for c in chain.get("calls", []) if abs(abs(c.get("delta", 0) or 0) - 0.25) <= 0.05]
        if puts_by_delta and calls_by_delta:
            puts, calls = puts_by_delta, calls_by_delta
        else:
            lo = 0.05 * atm
            hi = 0.15 * atm
            puts = [p for p in chain.get("puts", []) if lo <= (atm - (p.get("strike", 0) or 0)) <= hi]
            calls = [c for c in chain.get("calls", []) if lo <= ((c.get("strike", 0) or 0) - atm) <= hi]
        if not puts or not calls:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        iv_25p = np.mean([(p.get("impliedVolatility", 0) or 0) for p in puts])
        iv_25c = np.mean([(c.get("impliedVolatility", 0) or 0) for c in calls])
        skew = iv_25p - iv_25c
        if skew > 8.0:
            return {"direction": "long", "confidence": min((skew - 8.0) / 5.0, 0.80), "action": "sell", "skew": skew, "strategy": self.name}
        elif skew < 1.0:
            return {"direction": "short", "confidence": min((1.0 - skew) / 2.0, 0.60), "action": "buy", "skew": skew, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "skew": skew, "strategy": self.name}
