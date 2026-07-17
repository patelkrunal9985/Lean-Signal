from engine.v3.base import BaseV3Strategy


class StrikeVolumeSurge(BaseV3Strategy):
    name = "strike_volume_surge"
    description = "Single-strike volume surge — disproportionate volume at one strike vs chain average"
    applies_to = ("option",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        if underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Collect volume at each strike
        strike_volumes = {}
        for side_name in ("calls", "puts"):
            for rec in chain.get(side_name, []):
                strike = rec.get("strike", 0) or 0
                vol = rec.get("volume", 0) or 0
                if strike > 0 and vol > 10:
                    if strike not in strike_volumes:
                        strike_volumes[strike] = {"call_vol": 0, "put_vol": 0}
                    if side_name == "calls":
                        strike_volumes[strike]["call_vol"] += vol
                    else:
                        strike_volumes[strike]["put_vol"] += vol
        if not strike_volumes:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Compute average volume per strike
        total_call_vol = sum(sv["call_vol"] for sv in strike_volumes.values())
        total_put_vol = sum(sv["put_vol"] for sv in strike_volumes.values())
        avg_call_vol = total_call_vol / max(len(strike_volumes), 1)
        avg_put_vol = total_put_vol / max(len(strike_volumes), 1)
        # Find strikes with disproportionate volume (>3x average and >500 contracts)
        surges = []
        for strike, sv in strike_volumes.items():
            call_ratio = sv["call_vol"] / max(avg_call_vol, 1)
            put_ratio = sv["put_vol"] / max(avg_put_vol, 1)
            if sv["call_vol"] > 500 and call_ratio > 3:
                otm = strike > underlying
                surges.append({"strike": strike, "type": "call", "volume": sv["call_vol"], "ratio": call_ratio, "otm": otm})
            if sv["put_vol"] > 500 and put_ratio > 3:
                otm = strike < underlying
                surges.append({"strike": strike, "type": "put", "volume": sv["put_vol"], "ratio": put_ratio, "otm": otm})
        if not surges:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Score: OTM call surges = bullish, OTM put surges = bearish
        bullish_score = 0.0
        bearish_score = 0.0
        for s in surges:
            surge_conf = min(s["ratio"] / 10, 0.30)
            if s["type"] == "call" and s["otm"]:
                bullish_score += surge_conf * 1.5
            elif s["type"] == "call" and not s["otm"]:
                bullish_score += surge_conf * 0.6
            elif s["type"] == "put" and s["otm"]:
                bearish_score += surge_conf * 1.5
            else:
                bearish_score += surge_conf * 0.6
        confidence = max(bullish_score, bearish_score)
        confidence = min(confidence, 0.75)
        if bullish_score > bearish_score and confidence > 0.15:
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "surge_count": len(surges), "strategy": self.name}
        elif bearish_score > bullish_score and confidence > 0.15:
            return {"direction": "short", "confidence": round(confidence, 4), "action": "buy", "surge_count": len(surges), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
