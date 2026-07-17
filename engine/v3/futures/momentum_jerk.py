from kronos.strategies.v3.base import BaseV3Strategy
import math


class MomentumJerk(BaseV3Strategy):
    name = "momentum_jerk"
    description = "Momentum acceleration/deceleration — detects momentum exhaustion and reversals before price turns"
    applies_to = ("future", "stock")
    default_weight = 0.09

    def compute(self, context: dict) -> dict:
        ohlcv = context.get("ohlcv", [])
        if not ohlcv or len(ohlcv) < 15:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        closes = [c.get("close", 0) or 0 for c in ohlcv]
        volumes = [c.get("volume", 0) or 0 for c in ohlcv]
        highs = [c.get("high", 0) or 0 for c in ohlcv]
        lows = [c.get("low", 0) or 0 for c in ohlcv]

        def roc(series, period):
            if len(series) < period + 1:
                return 0
            return (series[-1] - series[-period - 1]) / max(series[-period - 1], 0.01)

        mom_3 = roc(closes, 3)
        mom_5 = roc(closes, 5)
        mom_10 = roc(closes, 10)

        mom_3_prev = roc(closes[:-1], 3) if len(closes) >= 5 else 0
        mom_5_prev = roc(closes[:-1], 5) if len(closes) >= 7 else 0

        jerk_3 = mom_3 - mom_3_prev
        jerk_5 = mom_5 - mom_5_prev

        vol_ratio = sum(volumes[-5:]) / max(sum(volumes[-10:-5]), 1)
        avg_vol_5 = sum(volumes[-5:]) / max(len(volumes[-5:]), 1)
        avg_vol_10 = sum(volumes[-10:]) / max(len(volumes[-10:]), 1)
        vol_trend = avg_vol_5 / max(avg_vol_10, 1)

        daily_high = max(highs[-5:])
        daily_low = min(lows[-5:])
        range_pct = (daily_high - daily_low) / max(daily_low, 0.01)

        close_pos = (closes[-1] - daily_low) / max(daily_high - daily_low, 0.01)

        direction = "neutral"
        confidence = 0.0
        reasons = []

        if jerk_3 < -0.01 and jerk_5 < -0.005 and mom_5 > 0.03:
            decel_strength = min(abs(jerk_3 + jerk_5) * 5, 0.40)
            vol_conf = min(max(vol_trend - 0.8, 0) * 2, 0.20)
            pos_conf = max(0.7 - close_pos, 0) * 0.15 if close_pos > 0.7 else 0
            confidence = min(decel_strength + vol_conf + pos_conf, 0.75)
            direction = "short"
            reasons.append(f"bullish_deceleration(j3={jerk_3:.4f})")

        elif jerk_3 > 0.01 and jerk_5 > 0.005 and mom_5 < -0.03:
            decel_strength = min(abs(jerk_3 + jerk_5) * 5, 0.40)
            vol_conf = min(max(vol_trend - 0.8, 0) * 2, 0.20)
            pos_conf = min(close_pos / 0.3, 1) * 0.15 if close_pos < 0.3 else 0
            confidence = min(decel_strength + vol_conf + pos_conf, 0.75)
            direction = "long"
            reasons.append(f"bearish_deceleration(j3={jerk_3:.4f})")

        elif jerk_3 > 0.02 and jerk_5 > 0.01 and vol_ratio > 1.5:
            confidence = min(0.35 + min(vol_ratio * 0.1, 0.15), 0.55)
            if mom_5 > 0:
                direction = "long"
                reasons.append(f"bullish_acceleration(j3={jerk_3:.4f})")
            elif mom_5 < 0:
                direction = "short"
                reasons.append(f"bearish_acceleration(j3={jerk_3:.4f})")

        return {"direction": direction, "confidence": round(confidence, 4),
                "jerk_3": round(jerk_3, 6), "jerk_5": round(jerk_5, 6),
                "mom_5": round(mom_5, 6), "vol_ratio": round(vol_ratio, 2),
                "vol_trend": round(vol_trend, 2), "close_pos": round(close_pos, 4),
                "reasoning": "; ".join(reasons), "strategy": self.name}
