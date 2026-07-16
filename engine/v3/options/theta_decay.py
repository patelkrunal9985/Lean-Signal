from kronos.strategies.v3.base import BaseV3Strategy


class ThetaDecay(BaseV3Strategy):
    name = "theta_decay"
    description = "Theta acceleration — sell premium 2-4 DTE, hold 0-1 DTE gamma"
    applies_to = ("option",)
    default_weight = 0.03

    def compute(self, context: dict) -> dict:
        dte = context.get("dte", 5)
        if dte > 4 or dte < 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        acceleration = (10.0 / max(dte, 0.5)) ** 2
        if dte <= 1:
            return {"direction": "long", "confidence": min(acceleration / 150, 0.80), "action": "buy", "acceleration": acceleration, "note": "Gamma risk — avoid short premium 0-1 DTE", "strategy": self.name}
        elif dte <= 4:
            return {"direction": "short", "confidence": min(acceleration / 120, 0.65), "action": "sell", "acceleration": acceleration, "note": "Theta acceleration — sell premium", "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "acceleration": acceleration, "strategy": self.name}
