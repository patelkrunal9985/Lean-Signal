from engine.v3.base import BaseV3Strategy


class VolumeSpike(BaseV3Strategy):
    name = "volume_spike"
    description = "Volume >1.8× avg + price in upper 40% of daily range"
    applies_to = ("future",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        ohlcv = context.get("ohlcv", [])
        if len(ohlcv) < 5:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        vols = [c.get("volume", 0) for c in ohlcv]
        if len(vols) < 5:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)
        last_vol = vols[-1]
        # If all volumes are zero (yfinance futures), treat avg_vol as 1.0
        # so the strategy can still fire based on price position alone.
        if avg_vol <= 0:
            avg_vol = 1.0
            last_vol = 1.0
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        vol_ratio = last_vol / avg_vol
        current = ohlcv[-1]["close"]
        daily_high = max(c["high"] for c in ohlcv)
        daily_low = min(c["low"] for c in ohlcv)
        daily_range = daily_high - daily_low
        if daily_range <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        position = (current - daily_low) / daily_range
        long_conf = 0.0
        short_conf = 0.0
        if vol_ratio > 1.8 and position > 0.60:
            strength = min((vol_ratio - 1.8) / 3.0, 1.0)
            long_conf = strength * 0.65
        elif vol_ratio > 1.8 and position < 0.40:
            strength = min((vol_ratio - 1.8) / 3.0, 1.0)
            short_conf = strength * 0.65
        conviction = (vol_ratio - 1.0) / max(vol_ratio, 1.0)
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "vol_ratio": round(vol_ratio, 2), "position_in_range": round(position, 4),
                    "conviction": round(conviction, 4), "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "vol_ratio": round(vol_ratio, 2), "position_in_range": round(position, 4),
                    "conviction": round(conviction, 4), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "vol_ratio": round(vol_ratio, 2),
                "position_in_range": round(position, 4), "strategy": self.name}
