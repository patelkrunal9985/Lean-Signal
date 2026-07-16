
class VolumeProfileStrategy:
    def __init__(self):
        self.name = "volume_profile"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])
        if not ohlcv or len(ohlcv) < 10:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["insufficient_data"]}
        close = ohlcv[-1]["close"]
        val_area = indicators.get("value_area") or {}
        vol_prof = indicators.get("volume_profile") or {}
        atr = indicators.get("atr_14", 0)
        atr_pct = atr / max(close, 0.01) if atr > 0 else 0.015
        poc = val_area.get("poc", 0)
        vah = val_area.get("vah", 0)
        val = val_area.get("val", 0)
        rsi = indicators.get("rsi_14", 50)
        avg_vol = vol_prof.get("avg_volume", 1)
        volume = ohlcv[-1]["volume"]
        vol_ratio = volume / max(avg_vol, 1)
        direction = "neutral"
        confidence = 0.0
        reasons = []
        if vah > 0 and val > 0:
            if close > vah * 1.01:
                if rsi > 60:
                    base = min(0.50 + (close / vah - 1) * 2, 0.75)
                    reasons.append(f"above_vah_rsi={rsi:.0f}")
                    if vol_ratio < 0.8:
                        base = min(base + 0.08, 0.80)
                        reasons.append("low_vol_ext")
                    direction = "short"
                    confidence = base
                    reasons.append(f"vp_short_vah={vah:.2f}")
            elif close < val * 0.99:
                if rsi < 40:
                    base = min(0.50 + (val / close - 1) * 2, 0.75)
                    reasons.append(f"below_val_rsi={rsi:.0f}")
                    if vol_ratio < 0.8:
                        base = min(base + 0.08, 0.80)
                        reasons.append("low_vol_ext")
                    direction = "long"
                    confidence = base
                    reasons.append(f"vp_long_val={val:.2f}")
            elif poc > 0:
                dist = (close - poc) / max(poc, 0.01)
                if abs(dist) < atr_pct * 0.3:
                    reasons.append(f"at_poc={poc:.2f}")
                elif dist < -atr_pct * 0.5 and rsi > 40:
                    base = min(0.35 + abs(dist) / atr_pct * 0.10, 0.60)
                    reasons.append(f"below_poc={dist:.4f}")
                    direction = "long"
                    confidence = base
                    reasons.append(f"vp_long_poc={poc:.2f}")
                elif dist > atr_pct * 0.5 and rsi < 60:
                    base = min(0.35 + abs(dist) / atr_pct * 0.10, 0.60)
                    reasons.append(f"above_poc={dist:.4f}")
                    direction = "short"
                    confidence = base
                    reasons.append(f"vp_short_poc={poc:.2f}")
            else:
                reasons.append("no_poc")
        else:
            reasons.append("no_value_area")
        if confidence == 0.0 and not reasons:
            reasons.append("vp_neutral")
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
        }
