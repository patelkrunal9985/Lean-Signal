from kronos.strategies.v3.base import BaseV3Strategy


class UnusualWhaleFlow(BaseV3Strategy):
    name = "unusual_whale_flow"
    description = "Unusual whale block flow - single large option block trades detected via premium threshold"
    applies_to = ("option",)
    default_weight = 0.09

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        if underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        whales = []
        for side_name, side_data in [("calls", chain.get("calls", [])), ("puts", chain.get("puts", []))]:
            for rec in side_data:
                vol = rec.get("volume", 0) or 0
                ask = rec.get("ask", 0) or rec.get("lastPrice", 0) or 0
                strike = rec.get("strike", 0) or 0
                premium = vol * 100 * ask
                if premium > 500000 and vol > 100 and strike > 0:
                    side_type = "call" if side_name == "calls" else "put"
                    whales.append({
                        "type": side_type,
                        "strike": strike,
                        "premium": premium,
                        "volume": vol,
                        "ask": ask,
                    })
        if not whales:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        call_bullish = 0.0
        put_bearish = 0.0
        for w in whales:
            otm = (w["type"] == "call" and w["strike"] > underlying) or (w["type"] == "put" and w["strike"] < underlying)
            if w["type"] == "call":
                call_bullish += w["premium"] * (1.5 if otm else 0.7)
            else:
                put_bearish += w["premium"] * (1.5 if otm else 0.7)
        total_whale = sum(w["premium"] for w in whales)
        if total_whale < 500000:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        net_bias = (call_bullish - put_bearish) / total_whale
        whale_count = len(whales)
        count_bonus = min(whale_count * 0.03, 0.15)
        confidence = min(abs(net_bias) * 0.7 + count_bonus + min(total_whale / 5000000, 0.15), 0.85)
        if net_bias > 0.3:
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "whale_count": whale_count, "total_premium": round(total_whale, 0), "strategy": self.name}
        elif net_bias < -0.3:
            return {"direction": "short", "confidence": round(confidence, 4), "action": "buy", "whale_count": whale_count, "total_premium": round(total_whale, 0), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
