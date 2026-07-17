from engine.v3.base import BaseV3Strategy


class ExpectedVsActual(BaseV3Strategy):
    name = "expected_vs_actual"
    description = "Expected move (ATM straddle) vs actual realized range"
    applies_to = ("option",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        straddle = context.get("atm_straddle_price", 0)
        underlying = context.get("underlying_price", 0)
        daily_range = context.get("daily_range", 0)
        if underlying <= 0 or straddle <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        expected_pct = (straddle * 0.8) / underlying
        actual_pct = daily_range / underlying
        ratio = actual_pct / max(expected_pct, 0.0001)
        # Under-reaction: actual range is <50% of expected straddle
        if ratio < 0.5:
            under_conf = min((0.5 - ratio) / 0.5 * 0.50, 0.50)
            return {"direction": "long", "confidence": round(under_conf, 4), "action": "buy", "ratio": ratio, "expected_pct": expected_pct, "actual_pct": actual_pct, "strategy": self.name}
        # Over-reaction: actual range is >150% of expected straddle → sell premium (reversion)
        if ratio > 1.5:
            return {"direction": "short", "confidence": min((ratio - 1.5) * 0.5, 0.60), "action": "sell", "ratio": ratio, "expected_pct": expected_pct, "actual_pct": actual_pct, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "ratio": ratio, "expected_pct": expected_pct, "strategy": self.name}
