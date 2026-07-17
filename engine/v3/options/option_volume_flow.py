from engine.v3.base import BaseV3Strategy


class OptionVolumeFlow(BaseV3Strategy):
    name = "option_volume_flow"
    description = "Real-time option volume flow — block trades, premium at ask"
    applies_to = ("option",)
    default_weight = 0.12

    def compute(self, context: dict) -> dict:
        flow = context.get("option_volume_flow", [])
        if not flow or not isinstance(flow, list):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        call_prem = sum(f.get("premium", 0) or 0 for f in flow if (f.get("type") or "") == "call")
        put_prem = sum(f.get("premium", 0) or 0 for f in flow if (f.get("type") or "") == "put")
        total_prem = call_prem + put_prem
        if total_prem < 100000:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        net_ratio = (call_prem - put_prem) / total_prem
        call_block_count = len([f for f in flow if (f.get("type") or "") == "call" and (f.get("volume", 0) or 0) > 500])
        put_block_count = len([f for f in flow if (f.get("type") or "") == "put" and (f.get("volume", 0) or 0) > 500])
        block_bonus = min((call_block_count + put_block_count) * 0.05, 0.15)
        if net_ratio > 0.3 and call_prem > put_prem * 1.5:
            confidence = min(abs(net_ratio) * 0.8 + block_bonus, 0.85)
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "net_ratio": round(net_ratio, 4), "call_premium": round(call_prem, 0), "put_premium": round(put_prem, 0), "strategy": self.name}
        elif net_ratio < -0.3 and put_prem > call_prem * 1.5:
            confidence = min(abs(net_ratio) * 0.8 + block_bonus, 0.85)
            return {"direction": "short", "confidence": round(confidence, 4), "action": "sell", "net_ratio": round(net_ratio, 4), "call_premium": round(call_prem, 0), "put_premium": round(put_prem, 0), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
