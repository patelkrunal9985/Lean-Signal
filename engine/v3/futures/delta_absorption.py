from kronos.strategies.v3.base import BaseV3Strategy


class DeltaAbsorption(BaseV3Strategy):
    name = "delta_absorption"
    description = "Cumulative delta + depth imbalance divergence — absorption detection on ES/NQ"
    applies_to = ("future",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        cum_delta = context.get("cumulative_delta", {})
        depth = context.get("order_book_imbalance", {})
        if not cum_delta and not depth:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        if not cum_delta:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        cum_delta_val = cum_delta.get("cumulative_delta", 0)
        last_10 = cum_delta.get("last_10_delta", 0)
        avg_delta = cum_delta.get("avg_delta_per_bar", 0)
        price_change = cum_delta.get("price_change", 0)
        bullish_div = cum_delta.get("bullish_divergence", False)
        bearish_div = cum_delta.get("bearish_divergence", False)
        depth_dir = depth.get("direction", "neutral") if depth else "neutral"
        depth_imb = depth.get("imbalance_ratio", 0) if depth else 0
        ohlcv = context.get("ohlcv", [])
        atr_val = _get_atr(ohlcv)
        long_conf = 0.0
        short_conf = 0.0
        if bullish_div and depth_dir == "bullish":
            strength = min(abs(last_10) / max(abs(avg_delta), 1), 2.0) / 2.0
            long_conf = strength * 0.60
        elif bearish_div and depth_dir == "bearish":
            strength = min(abs(last_10) / max(abs(avg_delta), 1), 2.0) / 2.0
            short_conf = strength * 0.60
        if not long_conf and not short_conf:
            if cum_delta_val > 0 and price_change < 0 and depth_dir != "bullish":
                long_conf = 0.25
            elif cum_delta_val < 0 and price_change > 0 and depth_dir != "bearish":
                short_conf = 0.25
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "cumulative_delta": cum_delta_val, "last_10_delta": last_10,
                    "price_change": round(price_change, 2),
                    "depth_direction": depth_dir, "depth_imbalance": round(depth_imb, 4),
                    "bullish_divergence": bullish_div, "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "cumulative_delta": cum_delta_val, "last_10_delta": last_10,
                    "price_change": round(price_change, 2),
                    "depth_direction": depth_dir, "depth_imbalance": round(depth_imb, 4),
                    "bearish_divergence": bearish_div, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "cumulative_delta": cum_delta_val, "last_10_delta": last_10,
                "price_change": round(price_change, 2), "depth_direction": depth_dir,
                "strategy": self.name}


def _get_atr(ohlcv: list) -> float:
    if len(ohlcv) < 14:
        return 0.0
    trs = []
    for i in range(-13, 0):
        tr = max(ohlcv[i]["high"] - ohlcv[i]["low"],
                 abs(ohlcv[i]["high"] - ohlcv[i-1]["close"]),
                 abs(ohlcv[i]["low"] - ohlcv[i-1]["close"]))
        trs.append(tr)
    return sum(trs) / len(trs)
