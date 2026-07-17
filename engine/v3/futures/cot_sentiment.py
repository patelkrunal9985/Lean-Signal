from engine.v3.base import BaseV3Strategy


class COTSentiment(BaseV3Strategy):
    name = "cot_sentiment"
    description = "Commitment of Traders — extreme commercial positioning"
    applies_to = ("future",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        cot = context.get("cot_data", {})
        total = cot.get("total_open_interest", 1)
        com_net = (cot.get("commercial_long", 0) - cot.get("commercial_short", 0)) / max(total, 1)
        spec_net = (cot.get("noncommercial_long", 0) - cot.get("noncommercial_short", 0)) / max(total, 1)
        confidence = 0.0
        direction = "neutral"
        if abs(com_net) > 0.15:
            if com_net < -0.15:
                direction = "short"
                confidence = min(abs(com_net) * 2.5, 0.75)
            elif com_net > 0.15:
                direction = "long"
                confidence = min(abs(com_net) * 2.5, 0.75)
        small_net = cot.get("small_trader_net_pct", 0)
        if abs(small_net) > 0.60:
            if small_net > 0.60 and direction != "short":
                direction = "short"
                confidence = max(confidence, 0.50)
            elif small_net < -0.80 and direction != "long":
                direction = "long"
                confidence = max(confidence, 0.50)
        return {"direction": direction, "confidence": confidence, "com_net": com_net, "spec_net": spec_net, "strategy": self.name}
