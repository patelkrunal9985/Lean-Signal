from engine.v3.base import BaseV3Strategy


class SessionContinuation(BaseV3Strategy):
    name = "session_continuation"
    description = "Globex range + overnight gap — futures session continuation/breakout"
    applies_to = ("future",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        globex = context.get("globex_range", {})
        ohlcv = context.get("ohlcv", [])
        if not globex or len(ohlcv) < 5:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        globex_high = globex.get("globex_high", 0)
        globex_low = globex.get("globex_low", 0)
        rth_open = globex.get("rth_open", 0)
        rth_high = globex.get("rth_high", 0)
        rth_low = globex.get("rth_low", 0)
        gap_pct = globex.get("gap_pct", 0)
        gap_up = globex.get("gap_up", False)
        gap_down = globex.get("gap_down", False)
        if not all([globex_high, globex_low, rth_open]):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        current = ohlcv[-1]["close"]
        globex_range = globex.get("globex_range", globex_high - globex_low)
        long_conf = 0.0
        short_conf = 0.0
        if gap_up and globex_range > 0:
            gap_strength = min(abs(gap_pct) * 50, 1.0)
            if current > globex_high:
                if current >= rth_high:
                    strength = min((current - globex_high) / globex_range * 2, 1.0)
                    long_conf = strength * gap_strength * 0.55
                else:
                    long_conf = gap_strength * 0.40
        if gap_down and globex_range > 0:
            gap_strength = min(abs(gap_pct) * 50, 1.0)
            if current < globex_low:
                if current <= rth_low:
                    strength = min((globex_low - current) / globex_range * 2, 1.0)
                    short_conf = strength * gap_strength * 0.55
                else:
                    short_conf = gap_strength * 0.40
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "gap_up": gap_up, "gap_pct": round(gap_pct, 4),
                    "globex_high": round(globex_high, 2),
                    "globex_low": round(globex_low, 2),
                    "rth_open": round(rth_open, 2), "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "gap_down": gap_down, "gap_pct": round(gap_pct, 4),
                    "globex_high": round(globex_high, 2),
                    "globex_low": round(globex_low, 2),
                    "rth_open": round(rth_open, 2), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "gap_up": gap_up, "gap_down": gap_down, "gap_pct": round(gap_pct, 4),
                "globex_range": round(globex_range, 2), "strategy": self.name}
