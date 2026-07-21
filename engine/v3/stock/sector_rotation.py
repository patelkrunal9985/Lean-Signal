from engine.v3.base import BaseV3Strategy


class SectorRotation(BaseV3Strategy):
    name = "sector_rotation"
    description = "Relative strength vs SPY (or SMA for SPY itself) across 1h/1d/5d windows"
    applies_to = ("stock",)
    default_weight = 0.10

    def _returns(self, ohlcv: list, window: int) -> float:
        if len(ohlcv) < window + 1:
            return 0.0
        start = float(ohlcv[-(window + 1)].get("close", 0) or 0)
        end = float(ohlcv[-1].get("close", 0) or 0)
        if start <= 0:
            return 0.0
        return (end - start) / start

    def compute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        ohlcv = context.get("ohlcv", [])
        ohlcv_1m = context.get("ohlcv_1m", [])
        if len(ohlcv) < 5:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        ret_1d = self._returns(ohlcv, 1)
        ret_5d = self._returns(ohlcv, 5)
        ret_1h = 0.0
        if len(ohlcv_1m) > 1:
            start = float(ohlcv_1m[0].get("close", 0) or 0)
            end = float(ohlcv_1m[-1].get("close", 0) or 0)
            if start > 0:
                ret_1h = (end - start) / start

        benchmark_ret_1d = 0.0
        benchmark_ret_5d = 0.0

        if ticker.upper() != "SPY":
            try:
                from engine.ibkr_data_feed import fetch_historical_bars
                spy_bars = fetch_historical_bars("SPY", "2 W", "1 day")
                if spy_bars and len(spy_bars) >= 5:
                    benchmark_ret_1d = self._returns(spy_bars, 1)
                    benchmark_ret_5d = self._returns(spy_bars, 5)
            except Exception:
                pass

        if ticker.upper() == "SPY":
            closes = [float(c.get("close", 0) or 0) for c in ohlcv if c.get("close", 0)]
            if len(closes) >= 20:
                sma20 = sum(closes[-20:]) / 20
                sma50 = sum(closes[-min(50, len(closes)):]) / min(50, len(closes)) if len(closes) >= 5 else sma20
                current = closes[-1]
                above_sma = (current - sma50) / sma50 if sma50 > 0 else 0
                mom = (closes[-1] - closes[-min(5, len(closes))]) / max(closes[-min(5, len(closes))], 0.01)
                composite = above_sma * 0.6 + mom * 0.4
            else:
                composite = ret_1d * 0.5 + ret_5d * 0.3 + ret_1h * 0.2
        else:
            rs_1d = ret_1d - benchmark_ret_1d
            rs_5d = ret_5d - benchmark_ret_5d
            composite = rs_1d * 0.5 + rs_5d * 0.3 + ret_1h * 0.2

        if composite > 0.015:
            direction = "long"
            confidence = min(0.40 + composite * 5, 0.85)
        elif composite < -0.015:
            direction = "short"
            confidence = min(0.40 + abs(composite) * 5, 0.85)
        else:
            return {"direction": "neutral", "confidence": 0.0, "composite": round(composite, 6), "strategy": self.name}

        return {"direction": direction, "confidence": round(confidence, 4), "composite": round(composite, 6), "ret_1d": round(ret_1d, 6), "strategy": self.name}
