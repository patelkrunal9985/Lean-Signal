from engine.v3.base import BaseV3Strategy


class SectorETFOptionRotation(BaseV3Strategy):
    name = "sector_etf_option_rotation"
    description = "Sector rotation option plays — strong directional moves with heavy option flow on sector ETFs or proxies"
    applies_to = ("option",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        price_chg = context.get("price_change_1d", 0)
        if abs(price_chg) < 0.01:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        flow = context.get("option_volume_flow", [])
        if not flow:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        call_prem = sum(f.get("premium", 0) or 0 for f in flow if (f.get("type") or "") == "call")
        put_prem = sum(f.get("premium", 0) or 0 for f in flow if (f.get("type") or "") == "put")
        total_prem = call_prem + put_prem
        if total_prem < 100000:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        net_ratio = (call_prem - put_prem) / total_prem
        iv = context.get("iv", 0)
        # Sector rotation signal: large price move + heavy option flow in same direction
        # Higher IV = more conviction needed (option premiums inflated)
        move_score = min(abs(price_chg) * 25, 0.35)
        flow_score = min(abs(net_ratio) * 0.6, 0.30)
        prem_score = min(total_prem / 750000, 0.20)
        # IV penalty: high IV means options are expensive, require more conviction
        iv_penalty = 1.0
        if iv > 80: iv_penalty = 0.7
        elif iv > 60: iv_penalty = 0.8
        elif iv > 40: iv_penalty = 0.9
        confidence = (move_score + flow_score + prem_score) * iv_penalty
        confidence = min(confidence, 0.80)
        if confidence < 0.20:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Direction: flow must align with price move for sector rotation signal
        if price_chg > 0 and net_ratio > 0.15:
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "price_chg": round(price_chg, 4), "net_flow": round(net_ratio, 4), "strategy": self.name}
        elif price_chg < 0 and net_ratio < -0.15:
            return {"direction": "short", "confidence": round(confidence, 4), "action": "buy", "price_chg": round(price_chg, 4), "net_flow": round(net_ratio, 4), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
