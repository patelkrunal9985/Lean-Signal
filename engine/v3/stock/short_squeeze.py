from engine.v3.base import BaseV3Strategy


class ShortSqueeze(BaseV3Strategy):
    name = "short_squeeze"
    description = "Squeeze-like price/volume pattern detection (volume surge + gap up + consecutive gains)"
    applies_to = ("stock",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        ohlcv = context.get("ohlcv", [])
        if len(ohlcv) < 10:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        closes = [float(c.get("close", 0) or 0) for c in ohlcv]
        highs = [float(c.get("high", 0) or 0) for c in ohlcv]
        lows = [float(c.get("low", 0) or 0) for c in ohlcv]
        volumes = [float(c.get("volume", 0) or 0) for c in ohlcv]
        opens = [float(c.get("open", 0) or 0) for c in ohlcv]

        no_volume = all(v == 0 for v in volumes)
        current = closes[-1]

        # 1. Volume surge
        avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1) if len(volumes) > 1 else 0
        last_vol = volumes[-1]
        if no_volume or avg_vol <= 0:
            vol_ratio = 1.0
            no_volume = True
        else:
            vol_ratio = last_vol / avg_vol

        # 2. Price surge (1d % change)
        pct_1d = (closes[-1] - closes[-2]) / max(closes[-2], 0.01) if len(closes) >= 2 else 0

        # 3. Gap up
        gap_pct = (opens[-1] - closes[-2]) / max(closes[-2], 0.01) if len(closes) >= 2 else 0

        # 4. Consecutive up days
        up_days = 0
        for i in range(min(len(closes) - 1, 5)):
            if closes[-(i + 1)] > closes[-(i + 2)]:
                up_days += 1
            else:
                break

        # 5. Intraday strength (close near high)
        daily_range = highs[-1] - lows[-1]
        close_pos = (closes[-1] - lows[-1]) / max(daily_range, 0.01) if daily_range > 0 else 0.5

        # 6. RSI proxy (simple momentum)
        gains, losses = 0.0, 0.0
        for i in range(1, min(15, len(closes))):
            diff = closes[-i] - closes[-i - 1]
            if diff > 0:
                gains += diff
            else:
                losses += abs(diff)
        avg_g = gains / min(14, len(closes) - 1) if len(closes) > 1 else 0
        avg_l = losses / min(14, len(closes) - 1) if len(closes) > 1 else 1
        rsi = 100 - (100 / (1 + avg_g / max(avg_l, 1e-10))) if avg_l > 0 else 50

        score = 0.0

        if pct_1d > 0.03 or (no_volume and pct_1d > 0.02):
            score += min((pct_1d * 100) / 10.0, 1.0) * 30
        if gap_pct > 0.01:
            score += min((gap_pct * 100) / 5.0, 1.0) * 20
        if up_days >= 2:
            score += min(up_days / 5.0, 1.0) * 15
        if close_pos > 0.70:
            score += (close_pos - 0.70) / 0.30 * 15
        if rsi < 35:
            score += (35 - rsi) / 35 * 10
        if vol_ratio > 2.0:
            score += min((vol_ratio - 2.0) / 3.0, 1.0) * 10

        confidence = min(score / 100.0, 1.0)
        if confidence >= 0.35 and pct_1d > 0:
            return {"direction": "long", "confidence": round(confidence, 4), "score": round(score, 1), "pct_1d": round(pct_1d, 6), "vol_ratio": round(vol_ratio, 2), "gap_pct": round(gap_pct, 6), "up_days": up_days, "strategy": self.name}

        return {"direction": "neutral", "confidence": 0.0, "score": round(score, 1), "pct_1d": round(pct_1d, 6), "vol_ratio": round(vol_ratio, 2), "strategy": self.name}
