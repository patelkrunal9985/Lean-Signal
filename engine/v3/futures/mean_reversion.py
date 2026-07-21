from engine.v3.base import BaseV3Strategy


class MeanReversion(BaseV3Strategy):
    name = "mean_reversion"
    description = "RSI <30/>70 with BB touch — fade extremes on ES/NQ/YM/RTY"
    applies_to = ("future", "stock")
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        ohlcv = context.get("ohlcv", [])
        candles_1m = context.get("candles_1m", [])
        if len(ohlcv) < 20:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        closes = [c["close"] for c in ohlcv]
        high = [c["high"] for c in ohlcv]
        low = [c["low"] for c in ohlcv]
        current = closes[-1]
        sma20 = sum(closes[-20:]) / 20
        vol20 = (sum((c - sma20)**2 for c in closes[-20:]) / 20) ** 0.5
        bb_upper = sma20 + 2 * vol20
        bb_lower = sma20 - 2 * vol20
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 0
        avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 1
        rsi = 100 - (100 / (1 + avg_gain / max(avg_loss, 1e-10))) if avg_loss > 0 else 100
        long_conf = 0.0
        short_conf = 0.0
        if rsi < 30 and current <= bb_lower * 1.005:
            reversion_strength = min((30 - rsi) / 15, 1.0)
            if candles_1m and len(candles_1m) > 1:
                last_1m = candles_1m[-1]["close"]
                first_1m = candles_1m[0]["close"]
                intraday_change = (last_1m - first_1m) / first_1m if first_1m else 0
                if intraday_change < -0.005:
                    reversion_strength = min(reversion_strength * 1.2, 1.0)
            long_conf = reversion_strength * 0.50
        if rsi > 70 and current >= bb_upper * 0.995:
            reversion_strength = min((rsi - 70) / 15, 1.0)
            short_conf = reversion_strength * 0.50
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "rsi": round(rsi, 1), "bb_lower": round(bb_lower, 2), "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "rsi": round(rsi, 1), "bb_upper": round(bb_upper, 2), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "rsi": round(rsi, 1), "strategy": self.name}


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
