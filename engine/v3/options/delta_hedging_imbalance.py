from kronos.strategies.v3.base import BaseV3Strategy


class DeltaHedgingImbalance(BaseV3Strategy):
    name = "delta_hedging_imbalance"
    description = "Dealer delta hedging pressure from large OI imbalances and gamma flip proximity"
    applies_to = ("option",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        dp = context.get("delta_positioning", {})
        if not dp or not isinstance(dp, dict):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        net_delta = dp.get("net_delta", 0)
        delta_ratio = dp.get("delta_ratio", 1.0)
        call_delta = dp.get("call_delta", 0)
        put_delta = dp.get("put_delta", 0)
        total_delta = call_delta + abs(put_delta)
        if total_delta < 1000000:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        gamma_flip = context.get("gamma_flip_level", 0)
        underlying = context.get("underlying_price", 0)
        flip_proximity = 0.0
        if gamma_flip > 0 and underlying > 0:
            dist_pct = abs(underlying - gamma_flip) / underlying
            flip_proximity = max(0, 1.0 - dist_pct * 20)
        delta_sign = 1 if net_delta > 0 else -1
        delta_magnitude = abs(net_delta)
        delta_score = min(delta_magnitude / 10000000, 0.35)
        flip_score = flip_proximity * 0.25
        concentration = min(abs(delta_ratio - 1.0) * 0.15, 0.20)
        confidence = delta_score + flip_score + concentration
        confidence = min(confidence, 0.80)
        if abs(delta_ratio - 1.0) < 0.3:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        if net_delta > 2000000:
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "net_delta": net_delta, "delta_ratio": delta_ratio, "flip_proximity": round(flip_proximity, 4), "strategy": self.name}
        elif net_delta < -2000000:
            return {"direction": "short", "confidence": round(confidence, 4), "action": "buy", "net_delta": net_delta, "delta_ratio": delta_ratio, "flip_proximity": round(flip_proximity, 4), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
