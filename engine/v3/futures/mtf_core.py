from engine.v3.base import BaseV3Strategy


class MTFCore(BaseV3Strategy):
    name = "mtf_core"
    description = "Multi-timeframe alignment check: daily/15m/5m trend concurrency"
    applies_to = ("future", "stock", "option")

    def compute(self, context: dict) -> dict:
        ohlcv = context.get("ohlcv", [])
        ohlcv_1m = context.get("ohlcv_1m", [])
        current_price = context.get("current_price", 0)

        if len(ohlcv) < 25 or len(ohlcv_1m) < 80:
            return {
                "direction": "neutral",
                "confidence": 0.0,
                "strategy": self.name,
                "diagnostics": {
                    "daily_trend": "neutral",
                    "tf_15m_trend": "neutral",
                    "tf_5m_trend": "neutral",
                    "daily_strength": 0.0,
                    "tf_15m_strength": 0.0,
                    "tf_5m_strength": 0.0,
                    "all_aligned": False,
                },
            }

        daily_closes = [c["close"] for c in ohlcv]
        sma20_d = self._sma(daily_closes, 20)
        sma50_d = self._sma(daily_closes, 50)
        last_close = current_price if current_price > 0 else daily_closes[-1]

        daily_trend, daily_strength = self._trend_and_strength(
            last_close, sma20_d, sma50_d
        )

        candles_15m = self._resample_1m_to_n(ohlcv_1m, 15)
        candles_5m = self._resample_1m_to_n(ohlcv_1m, 5)

        tf_15m_trend, tf_15m_strength = self._tf_trend_and_strength(candles_15m)
        tf_5m_trend, tf_5m_strength = self._tf_trend_and_strength(candles_5m)

        all_aligned = daily_trend == tf_15m_trend == tf_5m_trend != "neutral"

        direction = "neutral"
        confidence = 0.0

        if all_aligned:
            strengths = [daily_strength, tf_15m_strength, tf_5m_strength]
            avg_pct = sum(strengths) / 3.0
            raw = avg_pct / 0.02
            confidence = max(0.30, min(raw, 0.80))
            direction = "long" if daily_trend == "up" else "short"

        return {
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "diagnostics": {
                "daily_trend": daily_trend,
                "tf_15m_trend": tf_15m_trend,
                "tf_5m_trend": tf_5m_trend,
                "daily_strength": round(daily_strength, 6),
                "tf_15m_strength": round(tf_15m_strength, 6),
                "tf_5m_strength": round(tf_5m_strength, 6),
                "all_aligned": all_aligned,
            },
        }

    def _sma(self, values: list, period: int) -> float:
        if len(values) < period:
            return 0.0
        return sum(values[-period:]) / period

    def _trend_and_strength(self, price: float, sma20: float, sma50: float):
        if sma20 <= 0 or sma50 <= 0:
            return "neutral", 0.0
        above_20 = price > sma20
        above_50 = price > sma50
        if above_20 and above_50:
            pct = (price - sma20) / sma20
            return "up", pct
        if not above_20 and not above_50:
            pct = (sma20 - price) / sma20
            return "down", pct
        return "neutral", 0.0

    def _resample_1m_to_n(self, candles_1m: list, n_minutes: int) -> list:
        result = []
        batch = []
        for c in candles_1m:
            batch.append(c)
            if len(batch) == n_minutes:
                high = max(x["high"] for x in batch)
                low = min(x["low"] for x in batch)
                close = batch[-1]["close"]
                result.append({"high": high, "low": low, "close": close})
                batch = []
        if batch:
            high = max(x["high"] for x in batch)
            low = min(x["low"] for x in batch)
            close = batch[-1]["close"]
            result.append({"high": high, "low": low, "close": close})
        return result

    def _tf_trend_and_strength(self, candles: list):
        if len(candles) < 20:
            return "neutral", 0.0
        closes = [c["close"] for c in candles]
        sma = self._sma(closes, 20)
        if sma <= 0:
            return "neutral", 0.0
        last = closes[-1]
        if last > sma:
            return "up", (last - sma) / sma
        return "down", (sma - last) / sma
