from engine.v3.base import BaseV3Strategy


class SessionRegime(BaseV3Strategy):
    name = "session_regime"
    description = "Intraday momentum regime detection via 5m slope + ATR ratio — confidence modulator"
    applies_to = ("future",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        candles_1m = context.get("candles_1m", [])
        ohlcv = context.get("ohlcv", [])
        if len(candles_1m) < 10:
            return {"direction": "neutral", "confidence": 0.5, "strategy": self.name}
        five_min_bars = self._resample_5m(candles_1m)
        if len(five_min_bars) < 2:
            return {"direction": "neutral", "confidence": 0.5, "strategy": self.name}
        first_price = five_min_bars[0]["close"]
        last_price = five_min_bars[-1]["close"]
        roc = (last_price - first_price) / max(first_price, 0.01)
        total_5m = len(five_min_bars)
        trending = abs(roc) > 0.0015
        atr_val = _get_atr(ohlcv) if len(ohlcv) >= 14 else 0.0
        atr_ratio = 1.0
        if atr_val > 0 and len(ohlcv) >= 14:
            median_tr = _median_true_range(ohlcv)
            atr_ratio = atr_val / max(median_tr, 0.001)
        trending_5m = sum(1 for bar in five_min_bars if abs(
            (bar["close"] - bar["open"]) / max(bar["open"], 0.01)) > 0.001)
        trends_per_hour = round(trending_5m / max(total_5m, 1) * 12, 2)
        if trending and atr_ratio > 1.2:
            regime = "directional"
        elif trending and atr_ratio < 0.8:
            regime = "grinding"
        elif not trending and atr_ratio > 1.2:
            regime = "choppy"
        else:
            regime = "quiet"
        modifier = self._regime_modifier(regime)
        return {"direction": "neutral", "confidence": round(modifier, 4),
                "regime": regime, "trends_per_hour": trends_per_hour,
                "atr_ratio": round(atr_ratio, 4), "roc_5m": round(roc, 6),
                "strategy": self.name}

    @staticmethod
    def _resample_5m(candles_1m: list) -> list:
        if not candles_1m:
            return []
        five_min_bars = []
        group = []
        for c in candles_1m:
            group.append(c)
            if len(group) == 5:
                bar = {"open": group[0]["open"], "high": max(g["high"] for g in group),
                       "low": min(g["low"] for g in group), "close": group[-1]["close"]}
                five_min_bars.append(bar)
                group = []
        if len(group) >= 2:
            bar = {"open": group[0]["open"], "high": max(g["high"] for g in group),
                   "low": min(g["low"] for g in group), "close": group[-1]["close"]}
            five_min_bars.append(bar)
        return five_min_bars

    @staticmethod
    def _regime_modifier(regime: str) -> float:
        if regime == "directional":
            return 1.0
        if regime == "grinding":
            return 0.85
        if regime == "choppy":
            return 0.50
        if regime == "quiet":
            return 0.70
        return 0.60


def _get_atr(ohlcv: list) -> float:
    if len(ohlcv) < 14:
        return 0.0
    trs = []
    for i in range(-13, 0):
        tr = max(ohlcv[i]["high"] - ohlcv[i]["low"],
                 abs(ohlcv[i]["high"] - ohlcv[i - 1]["close"]),
                 abs(ohlcv[i]["low"] - ohlcv[i - 1]["close"]))
        trs.append(tr)
    return sum(trs) / len(trs)


def _median_true_range(ohlcv: list) -> float:
    if len(ohlcv) < 14:
        return 0.001
    trs = []
    for i in range(-13, 0):
        tr = max(ohlcv[i]["high"] - ohlcv[i]["low"],
                 abs(ohlcv[i]["high"] - ohlcv[i - 1]["close"]),
                 abs(ohlcv[i]["low"] - ohlcv[i - 1]["close"]))
        trs.append(tr)
    trs.sort()
    return trs[len(trs) // 2]
