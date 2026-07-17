from engine.v3.base import BaseV3Strategy


class IVRVSpread(BaseV3Strategy):
    name = "iv_rv_spread"
    description = "Implied vs realized volatility spread"
    applies_to = ("option",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        iv = context.get("iv", 0)
        hv = context.get("hv_10", 0)
        if hv <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        spread = (iv - hv) / hv
        if spread > 0.15:
            return {"direction": "short", "confidence": min(spread * 1.2, 0.80), "action": "sell", "spread_pct": spread, "iv": iv, "hv": hv, "strategy": self.name}
        elif spread < -0.10:
            return {"direction": "long", "confidence": min(abs(spread) * 1.5, 0.70), "action": "buy", "spread_pct": spread, "iv": iv, "hv": hv, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "spread_pct": spread, "strategy": self.name}
