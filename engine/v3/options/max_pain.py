from datetime import datetime
from engine.v3.base import BaseV3Strategy


class MaxPain(BaseV3Strategy):
    name = "max_pain"
    description = "Max pain / pin risk for 0-2 DTE"
    applies_to = ("option",)
    default_weight = 0.07

    def _calc_max_pain(self, chain: dict) -> float:
        strikes = set()
        for opt_type in ["calls", "puts"]:
            for s in chain.get(opt_type, []):
                stk = s.get("strike", 0) or 0
                if stk > 0:
                    strikes.add(stk)
        best_strike = 0
        best_pain = 0
        for strike in strikes:
            if not strike:
                continue
            total_pain = 0.0
            for s in chain.get("calls", []):
                k = s.get("strike", 0) or 0
                if k > strike:
                    oi = s.get("openInterest", 0) or 0
                    total_pain += (k - strike) * oi * 100
            for s in chain.get("puts", []):
                k = s.get("strike", 0) or 0
                if k < strike:
                    oi = s.get("openInterest", 0) or 0
                    total_pain += (strike - k) * oi * 100
            if total_pain > best_pain:
                best_pain = total_pain
                best_strike = strike
        return best_strike

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        expiry = context.get("expiry")
        dte = context.get("dte", 3)
        if underlying <= 0 or dte > 21:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        mp = self._calc_max_pain(chain)
        if mp == 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        dist = (underlying - mp) / mp
        abs_dist = abs(dist)
        if dte <= 1 and abs_dist > 0.005:
            direction = "short" if dist > 0 else "long"
            confidence = min(abs_dist * 30, 0.85)
            return {"direction": direction, "confidence": confidence, "action": "sell", "max_pain": mp, "price_distance_pct": dist, "dte": dte, "strategy": self.name}
        elif dte <= 2 and abs_dist > 0.01:
            direction = "short" if dist > 0 else "long"
            confidence = min(abs_dist * 20, 0.70)
            return {"direction": direction, "confidence": confidence, "action": "sell", "max_pain": mp, "price_distance_pct": dist, "dte": dte, "strategy": self.name}
        else:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
