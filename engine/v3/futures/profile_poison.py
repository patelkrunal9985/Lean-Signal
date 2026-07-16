from kronos.strategies.v3.base import BaseV3Strategy


class ProfilePoison(BaseV3Strategy):
    name = "profile_poison"
    description = "Failed breakout at VAH/VAL — rejection from volume profile edges"
    applies_to = ("future",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        vp = context.get("volume_profile_intraday", {})
        ohlcv = context.get("ohlcv", [])
        if not vp or len(ohlcv) < 5:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        vah = vp.get("vah", 0)
        val = vp.get("val", 0)
        poc = vp.get("poc", 0)
        if not all([vah, val, poc]):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        current = ohlcv[-1]["close"]
        prev = ohlcv[-2]["close"] if len(ohlcv) > 1 else current
        current_high = ohlcv[-1]["high"]
        current_low = ohlcv[-1]["low"]
        long_conf = 0.0
        short_conf = 0.0
        moved_above_vah = prev <= vah * 1.001 and current_high > vah
        moved_below_val = prev >= val * 0.999 and current_low < val
        if moved_above_vah and current < vah:
            rejection_strength = abs(current - vah) / max(vah - poc, 0.01)
            short_conf = min(rejection_strength * 0.50, 0.65)
        if moved_below_val and current > val:
            rejection_strength = abs(current - val) / max(poc - val, 0.01)
            long_conf = min(rejection_strength * 0.50, 0.65)
        if not long_conf and not short_conf:
            close_above_vah = vp.get("close_above_vah", False)
            close_below_val = vp.get("close_below_val", False)
            close_above_vah_prev = (prev > vah) if vah else False
            close_below_val_prev = (prev < val) if val else False
            if close_above_vah and not close_above_vah_prev:
                short_conf = 0.30
            elif close_below_val and not close_below_val_prev:
                long_conf = 0.30
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "vah": round(vah, 2), "val": round(val, 2), "poc": round(poc, 2),
                    "moved_below_val": moved_below_val, "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "vah": round(vah, 2), "val": round(val, 2), "poc": round(poc, 2),
                    "moved_above_vah": moved_above_vah, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "vah": round(vah, 2), "val": round(val, 2), "poc": round(poc, 2),
                "strategy": self.name}
