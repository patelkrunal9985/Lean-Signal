

class SRLevelsStrategy:
    def __init__(self):
        self.name = "sr_levels"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])
        if not ohlcv or len(ohlcv) < 3:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["insufficient data"]}
        closes = [c["close"] for c in ohlcv]
        highs = [c["high"] for c in ohlcv]
        lows = [c["low"] for c in ohlcv]
        close = closes[-1]
        atr = indicators.get("atr_14", 0)
        atr_pct = atr / max(close, 0.01) if atr > 0 else 0.015
        prev_high = highs[-2] if len(highs) >= 2 else highs[-1]
        prev_low = lows[-2] if len(lows) >= 2 else lows[-1]
        week_high = max(highs[-5:]) if len(highs) >= 5 else prev_high
        week_low = min(lows[-5:]) if len(lows) >= 5 else prev_low
        pivot = (prev_high + prev_low + closes[-2]) / 3.0 if len(closes) >= 2 else close
        r1 = 2 * pivot - prev_low
        s1 = 2 * pivot - prev_high
        levels = [
            ("weekly_high", week_high, 3),
            ("weekly_low", week_low, 3),
            ("prev_high", prev_high, 2),
            ("prev_low", prev_low, 2),
            ("pivot_r1", r1, 1),
            ("pivot_s1", s1, 1),
        ]
        direction = "neutral"
        confidence = 0.0
        reasons = []
        rsi = indicators.get("rsi_14", 50)
        for lname, lprice, lstrength in levels:
            if lprice <= 0:
                continue
            dist = (close - lprice) / max(lprice, 0.01)
            if abs(dist) > atr_pct * 2:
                continue
            basis = min(lstrength * 0.20, 0.75)
            if dist < 0 and close > lprice * 0.995:
                base = basis
                if rsi < 40:
                    base = min(base + 0.12, 0.85)
                    reasons.append(f"{lname}_support_rsi={rsi:.0f}")
                elif rsi < 50:
                    base = min(base + 0.06, 0.80)
                    reasons.append(f"{lname}_support")
                if base >= 0.20 and base > confidence:
                    direction = "long"
                    confidence = base
                    reasons.append(f"sr_long_conf={confidence:.3f}")
            elif dist > 0 and close < lprice * 1.005:
                base = basis
                if rsi > 60:
                    base = min(base + 0.12, 0.85)
                    reasons.append(f"{lname}_resistance_rsi={rsi:.0f}")
                elif rsi > 50:
                    base = min(base + 0.06, 0.80)
                    reasons.append(f"{lname}_resistance")
                if base >= 0.20 and base > confidence:
                    direction = "short"
                    confidence = base
                    reasons.append(f"sr_short_conf={confidence:.3f}")
        if not reasons:
            reasons.append("no_sr_proximity")

        # Track nearest support (highest level below price) and resistance (lowest above)
        nearest_support = 0.0
        nearest_resistance = float("inf")
        for lname, lprice, lstrength in levels:
            if lprice <= 0:
                continue
            if lprice < close and lprice > nearest_support:
                nearest_support = lprice
            if lprice > close and lprice < nearest_resistance:
                nearest_resistance = lprice
        if nearest_resistance == float("inf"):
            nearest_resistance = None

        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
            "nearest_support": round(nearest_support, 2),
            "nearest_resistance": round(nearest_resistance, 2) if nearest_resistance is not None else None,
        }
