from engine.v3.base import BaseV3Strategy


class VWAPOptionFlow(BaseV3Strategy):
    name = "vwap_option_flow"
    description = "VWAP-anchored option flow — combines IEX depth imbalance with option premium direction"
    applies_to = ("option",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        flow = context.get("option_volume_flow", [])
        iex_imbalance = context.get("iex_imbalance", {})
        if not flow or not iex_imbalance:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        call_prem = sum(f.get("premium", 0) or 0 for f in flow if (f.get("type") or "") == "call")
        put_prem = sum(f.get("premium", 0) or 0 for f in flow if (f.get("type") or "") == "put")
        total_prem = call_prem + put_prem
        if total_prem < 75000:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        net_ratio = (call_prem - put_prem) / total_prem
        # IEX depth imbalance: bid_vol vs ask_vol ratio
        iex_dir = iex_imbalance.get("direction", "neutral")
        iex_pressure = iex_imbalance.get("pressure", 0)
        iex_ratio = iex_imbalance.get("imbalance_ratio", 0)
        if iex_dir == "neutral" or iex_pressure < 0.05:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Signal: IEX depth + option flow must agree
        iex_bullish = iex_dir == "bullish"
        flow_bullish = net_ratio > 0.15
        flow_bearish = net_ratio < -0.15
        if (iex_bullish and not flow_bullish) or (not iex_bullish and not flow_bearish):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        iex_score = min(abs(iex_ratio) * 1.5, 0.30)
        flow_score = min(abs(net_ratio) * 0.7, 0.30)
        prem_score = min(total_prem / 500000, 0.20)
        confidence = iex_score + flow_score + prem_score
        confidence = min(confidence, 0.80)
        direction = "long" if iex_bullish else "short"
        return {"direction": direction, "confidence": round(confidence, 4), "action": "buy", "iex_imbalance": round(iex_ratio, 4), "net_flow": round(net_ratio, 4), "strategy": self.name}
