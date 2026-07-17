from engine.v3.base import BaseV3Strategy


class OpeningDrive(BaseV3Strategy):
    name = "opening_drive"
    description = "Opening drive / gap fill — first 30-min option flow direction after overnight gap"
    applies_to = ("option",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        price_chg = context.get("price_change_1d", 0)
        gap_pct = abs(price_chg)
        if gap_pct < 0.005:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        flow = context.get("option_volume_flow", [])
        if not flow:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        call_prem = sum(f.get("premium", 0) or 0 for f in flow if (f.get("type") or "") == "call")
        put_prem = sum(f.get("premium", 0) or 0 for f in flow if (f.get("type") or "") == "put")
        total_prem = call_prem + put_prem
        if total_prem < 50000:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        net_ratio = (call_prem - put_prem) / total_prem
        gap_score = min(gap_pct * 20, 0.30)
        flow_score = min(abs(net_ratio) * 0.5, 0.25)
        prem_score = min(total_prem / 500000, 0.25)
        confidence = gap_score + flow_score + prem_score
        confidence = min(confidence, 0.80)
        if price_chg > 0 and net_ratio < -0.2:
            return {"direction": "short", "confidence": round(min(confidence + 0.05, 0.85), 4), "action": "buy", "gap_pct": round(gap_pct, 4), "net_flow": round(net_ratio, 4), "signal_type": "gap_fade", "strategy": self.name}
        elif price_chg < 0 and net_ratio > 0.2:
            return {"direction": "long", "confidence": round(min(confidence + 0.05, 0.85), 4), "action": "buy", "gap_pct": round(gap_pct, 4), "net_flow": round(net_ratio, 4), "signal_type": "gap_fade", "strategy": self.name}
        elif price_chg > 0 and net_ratio > 0.3:
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "gap_pct": round(gap_pct, 4), "net_flow": round(net_ratio, 4), "signal_type": "continuation", "strategy": self.name}
        elif price_chg < 0 and net_ratio < -0.3:
            return {"direction": "short", "confidence": round(confidence, 4), "action": "buy", "gap_pct": round(gap_pct, 4), "net_flow": round(net_ratio, 4), "signal_type": "continuation", "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
