from engine.v3.base import BaseV3Strategy


class VPINStrategy(BaseV3Strategy):
    name = "vpin"
    description = "Volume-synchronized Probability of Informed Trading from real IBKR tick tape — measures order flow toxicity"
    applies_to = ("future", "stock")
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        tick_stats = context.get("cumulative_delta", {})
        if not tick_stats or not isinstance(tick_stats, dict):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        vpin_val = tick_stats.get("vpin", 0) or 0
        trade_imb = tick_stats.get("trade_imbalance", 0) or 0
        vol_imb = tick_stats.get("volume_imbalance", 0) or 0
        total_vol = (tick_stats.get("total_buy_vol", 0) or 0) + (tick_stats.get("total_sell_vol", 0) or 0)
        buy_count = tick_stats.get("buy_count", 0) or 0
        sell_count = tick_stats.get("sell_count", 0) or 0

        if total_vol < 100:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        direction = "neutral"
        confidence = 0.0

        if vpin_val > 0.6 and abs(trade_imb) > 0.25:
            base = min((vpin_val - 0.5) * 2.5, 0.60)
            imb_bonus = abs(trade_imb) * 0.20
            confidence = min(base + imb_bonus, 0.80)
            if vol_imb > 0:
                direction = "short"
            else:
                direction = "long"

        elif vpin_val < 0.25:
            if abs(vol_imb) > 0.20:
                base = min((0.25 - vpin_val) * 1.5, 0.30)
                confidence = min(base + abs(vol_imb) * 0.15, 0.45)
                if vol_imb > 0:
                    direction = "long"
                else:
                    direction = "short"

        if vol_imb > 0.5 and trade_imb > 0.3 and vpin_val < 0.4:
            direction = "long"
            confidence = max(confidence, min(vol_imb * 0.6, 0.55))
        elif vol_imb < -0.5 and trade_imb < -0.3 and vpin_val < 0.4:
            direction = "short"
            confidence = max(confidence, min(abs(vol_imb) * 0.6, 0.55))

        return {"direction": direction, "confidence": round(confidence, 4),
                "vpin": round(vpin_val, 4), "trade_imb": round(trade_imb, 4),
                "vol_imb": round(vol_imb, 4),
                "buy_count": buy_count, "sell_count": sell_count,
                "total_vol": total_vol, "strategy": self.name}
