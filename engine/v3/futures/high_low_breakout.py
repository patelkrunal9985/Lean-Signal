from kronos.strategies.v3.base import BaseV3Strategy


class HighLowBreakout(BaseV3Strategy):
    name = "high_low_breakout"
    description = "20-day high/low breaks with volume confirmation on ES/NQ"
    applies_to = ("future",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        ohlcv = context.get("ohlcv", [])
        if len(ohlcv) < 22:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        closes = [c["close"] for c in ohlcv]
        highs = [c["high"] for c in ohlcv[-22:]]
        lows = [c["low"] for c in ohlcv[-22:]]
        current = closes[-1]
        current_high = ohlcv[-1]["high"]
        current_low = ohlcv[-1]["low"]
        twenty_day_high = max(highs)
        twenty_day_low = min(lows)
        vols = [c.get("volume", 0) for c in ohlcv[-22:]]
        avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1) if len(vols) > 1 else 0
        last_vol = ohlcv[-1].get("volume", 0)
        # If all volumes are zero (yfinance futures), treat as vol_ratio=1.0
        # so the strategy can fire on price breakouts alone.
        if avg_vol <= 0:
            avg_vol = 1.0
            last_vol = 1.0
        vol_ratio = last_vol / max(avg_vol, 1)
        up_streak = 1
        down_streak = 1
        for i in range(2, min(len(closes), 6)):
            if closes[-i] >= closes[-i-1]:
                up_streak += 1
            else:
                break
        for i in range(2, min(len(closes), 6)):
            if closes[-i] <= closes[-i-1]:
                down_streak += 1
            else:
                break
        long_conf = 0.0
        short_conf = 0.0
        no_volume_data = all(v == 0 for v in vols)
        if current_high >= twenty_day_high * 0.999:
            if vol_ratio > 1.3 or no_volume_data:
                strength = min((vol_ratio - 1.0) / 2.0, 1.0) if vol_ratio > 1.0 else 0.40
                if up_streak >= 2:
                    strength = min(strength * 1.2, 1.0)
                long_conf = strength * 0.60
        if current_low <= twenty_day_low * 1.001:
            if vol_ratio > 1.3 or no_volume_data:
                strength = min((vol_ratio - 1.0) / 2.0, 1.0) if vol_ratio > 1.0 else 0.40
                if down_streak >= 2:
                    strength = min(strength * 1.2, 1.0)
                short_conf = strength * 0.60
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "twenty_day_high": round(twenty_day_high, 2),
                    "twenty_day_low": round(twenty_day_low, 2),
                    "vol_ratio": round(vol_ratio, 2), "up_streak": up_streak, "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "twenty_day_high": round(twenty_day_high, 2),
                    "twenty_day_low": round(twenty_day_low, 2),
                    "vol_ratio": round(vol_ratio, 2), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "twenty_day_high": round(twenty_day_high, 2),
                "twenty_day_low": round(twenty_day_low, 2),
                "vol_ratio": round(vol_ratio, 2), "strategy": self.name}
