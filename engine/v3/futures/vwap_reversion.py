from engine.v3.base import BaseV3Strategy


class VWAPReversion(BaseV3Strategy):
    name = "vwap_reversion"
    description = "Price vs VWAP with ATR bands — fade extended moves on ES/NQ/GC"
    applies_to = ("future",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        ohlcv = context.get("ohlcv", [])
        vwap_data = context.get("intraday_vwap", {})
        if len(ohlcv) < 5 or not vwap_data:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        vwap = vwap_data.get("vwap", 0)
        atr_val = _get_atr(ohlcv)
        if vwap <= 0 or atr_val <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        current = ohlcv[-1]["close"]
        deviation = (current - vwap) / vwap
        deviation_atr = abs(current - vwap) / max(atr_val, 0.01)
        long_conf = 0.0
        short_conf = 0.0
        if deviation < -0.003 and deviation_atr > 1.5:
            strength = min((abs(deviation) * 50), 1.0)
            atr_boost = min((deviation_atr - 1.5) / 3.0, 1.0)
            long_conf = strength * atr_boost * 0.55
        if deviation > 0.003 and deviation_atr > 1.5:
            strength = min((deviation * 50), 1.0)
            atr_boost = min((deviation_atr - 1.5) / 3.0, 1.0)
            short_conf = strength * atr_boost * 0.55
        vp = context.get("volume_profile_intraday", {})
        if vp:
            poc = vp.get("poc", vwap)
            if current < vp.get("val", 0):
                long_conf = long_conf * 1.15
            elif current > vp.get("vah", 0):
                short_conf = short_conf * 1.15
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "deviation": round(deviation, 6), "deviation_atr": round(deviation_atr, 4),
                    "vwap": round(vwap, 2), "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "deviation": round(deviation, 6), "deviation_atr": round(deviation_atr, 4),
                    "vwap": round(vwap, 2), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "deviation": round(deviation, 6),
                "deviation_atr": round(deviation_atr, 4), "strategy": self.name}


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
