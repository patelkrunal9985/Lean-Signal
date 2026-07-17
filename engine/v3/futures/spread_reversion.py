from engine.v3.base import BaseV3Strategy


class SpreadReversion(BaseV3Strategy):
    name = "spread_reversion"
    description = "ES/NQ spread mean reversion — fade extreme daily moves when ATR is above median"
    applies_to = ("future",)
    default_weight = 0.05

    def compute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        if ticker not in ("ES=F", "NQ=F"):
            return {
                "direction": "neutral", "confidence": 0.0,
                "returns_1d": 0, "returns_5d_avg": 0,
                "atr_ratio": 0, "strategy": self.name,
            }

        ohlcv = context.get("ohlcv", [])
        if len(ohlcv) < 6:
            return {
                "direction": "neutral", "confidence": 0.0,
                "returns_1d": 0, "returns_5d_avg": 0,
                "atr_ratio": 0, "strategy": self.name,
            }

        # Daily returns
        close_now = ohlcv[-1].get("close", 0)
        close_1d_ago = ohlcv[-2].get("close", 0) if len(ohlcv) >= 2 else 0
        close_5d_ago = ohlcv[-6].get("close", 0) if len(ohlcv) >= 6 else 0

        if close_1d_ago <= 0:
            return {
                "direction": "neutral", "confidence": 0.0,
                "returns_1d": 0, "returns_5d_avg": 0,
                "atr_ratio": 0, "strategy": self.name,
            }

        returns_1d = (close_now - close_1d_ago) / close_1d_ago
        returns_5d = ((close_now - close_5d_ago) / close_5d_ago) / 5.0 if close_5d_ago > 0 else 0

        # Compute ATR ratio (current ATR vs median ATR over 14 periods)
        atr_val = _get_atr(ohlcv)
        if atr_val == 0:
            return {
                "direction": "neutral", "confidence": 0.0,
                "returns_1d": round(returns_1d, 4),
                "returns_5d_avg": round(returns_5d, 4),
                "atr_ratio": 0, "strategy": self.name,
            }

        atr_14 = _get_atr(ohlcv, 14)
        atr_median = _get_median_atr(ohlcv)
        atr_ratio = atr_14 / atr_median if atr_median > 0 else 1.0

        # Only trade when ATR is above median (volatile enough for mean reversion)
        if atr_ratio < 1.0:
            return {
                "direction": "neutral", "confidence": 0.0,
                "returns_1d": round(returns_1d, 4),
                "returns_5d_avg": round(returns_5d, 4),
                "atr_ratio": round(atr_ratio, 4),
                "strategy": self.name,
            }

        # Fade extreme moves: daily return > 1.5x the 5-day avg return
        if returns_5d != 0:
            ratio_to_avg = abs(returns_1d) / abs(returns_5d)
        else:
            ratio_to_avg = 2.0 if abs(returns_1d) > 0.005 else 0.5

        if ratio_to_avg > 1.5 and abs(returns_1d) > 0.002:
            confidence = min((ratio_to_avg - 1.5) * 0.15, 0.60)
            if returns_1d > 0:
                return {
                    "direction": "short", "confidence": round(confidence, 4),
                    "returns_1d": round(returns_1d, 4),
                    "returns_5d_avg": round(returns_5d, 4),
                    "atr_ratio": round(atr_ratio, 4),
                    "strategy": self.name,
                }
            return {
                "direction": "long", "confidence": round(confidence, 4),
                "returns_1d": round(returns_1d, 4),
                "returns_5d_avg": round(returns_5d, 4),
                "atr_ratio": round(atr_ratio, 4),
                "strategy": self.name,
            }

        return {
            "direction": "neutral", "confidence": 0.0,
            "returns_1d": round(returns_1d, 4),
            "returns_5d_avg": round(returns_5d, 4),
            "atr_ratio": round(atr_ratio, 4),
            "strategy": self.name,
        }


def _get_atr(ohlcv: list, period: int = 14) -> float:
    if len(ohlcv) < period + 1:
        return 0.0
    trs = []
    for i in range(-period, 0):
        tr = max(ohlcv[i]["high"] - ohlcv[i]["low"],
                 abs(ohlcv[i]["high"] - ohlcv[i - 1]["close"]),
                 abs(ohlcv[i]["low"] - ohlcv[i - 1]["close"]))
        trs.append(tr)
    return sum(trs) / len(trs)


def _get_median_atr(ohlcv: list, period: int = 14) -> float:
    if len(ohlcv) < period * 3:
        return sum(abs(c["high"] - c["low"]) for c in ohlcv[-(period * 2) :]) / (period * 2)
    trs = []
    for i in range(-(period * 3), 0):
        tr = max(ohlcv[i]["high"] - ohlcv[i]["low"],
                 abs(ohlcv[i]["high"] - ohlcv[i - 1]["close"]),
                 abs(ohlcv[i]["low"] - ohlcv[i - 1]["close"]))
        trs.append(tr)
    if not trs:
        return 1.0
    sorted_trs = sorted(trs)
    return sorted_trs[len(sorted_trs) // 2]
