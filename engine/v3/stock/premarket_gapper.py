from engine.v3.base import BaseV3Strategy


class PremarketGapper(BaseV3Strategy):
    name = "premarket_gapper"
    description = "Gap fade/continuation from OHLCV open vs prev close + 1m volume"
    applies_to = ("stock",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        ohlcv = context.get("ohlcv", [])
        ohlcv_1m = context.get("ohlcv_1m", [])
        if len(ohlcv) < 2:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        prev_close = float(ohlcv[-2].get("close", 0) or 0)
        today_open = float(ohlcv[-1].get("open", 0) or 0)
        if prev_close <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        gap_pct = (today_open - prev_close) / prev_close
        if abs(gap_pct) < 0.005:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        first_30m_vol = sum(float(c.get("volume", 0) or 0) for c in ohlcv_1m[:30]) if ohlcv_1m else 0
        daily_vols = [float(c.get("volume", 0) or 0) for c in ohlcv[:-1]]
        avg_daily_vol = sum(daily_vols) / max(len(daily_vols), 1) if daily_vols else 0
        vol_ratio = first_30m_vol / max(avg_daily_vol / 13, 1) if avg_daily_vol > 0 else 0
        no_volume = avg_daily_vol <= 0

        if abs(gap_pct) >= 0.015:
            if vol_ratio > 2.0 or no_volume:
                strength = min((max(vol_ratio, 1.0) - 1.0) / 2.0, 1.0) if not no_volume else 0.5
                confidence = min(strength * 0.65 + abs(gap_pct) * 5, 0.90)
                return {"direction": "long" if gap_pct > 0 else "short", "confidence": round(confidence, 4), "gap_pct": round(gap_pct, 6), "vol_ratio": round(vol_ratio, 2), "strategy": self.name}
            else:
                confidence = min(abs(gap_pct) * 5, 0.65)
                return {"direction": "short" if gap_pct > 0 else "long", "confidence": round(confidence, 4), "gap_pct": round(gap_pct, 6), "vol_ratio": round(vol_ratio, 2), "strategy": self.name}

        gap_strength = abs(gap_pct) * 10
        if gap_strength > 0.10:
            confidence = min(gap_strength * 0.5, 0.40)
            return {"direction": "short" if gap_pct > 0 else "long", "confidence": round(confidence, 4), "gap_pct": round(gap_pct, 6), "strategy": self.name}

        return {"direction": "neutral", "confidence": 0.0, "gap_pct": round(gap_pct, 6), "strategy": self.name}
