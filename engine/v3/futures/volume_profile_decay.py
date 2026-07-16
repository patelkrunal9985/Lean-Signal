from kronos.strategies.v3.base import BaseV3Strategy


class VolumeProfileDecay(BaseV3Strategy):
    name = "volume_profile_decay"
    description = "POC drift + value area position — detect trend weakening and reversals from volume profile"
    applies_to = ("future",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        vp = context.get("volume_profile_intraday", {})
        ohlcv = context.get("ohlcv", [])
        candles_1m = context.get("candles_1m", [])
        current_price = context.get("current_price", 0)
        cum_delta = context.get("cumulative_delta", {})

        poc = 0.0
        vah = 0.0
        val = 0.0

        if vp and isinstance(vp, dict):
            poc = vp.get("poc", 0)
            vah = vp.get("vah", 0)
            val = vp.get("val", 0)

        price = current_price
        if not price and ohlcv:
            price = ohlcv[-1].get("close", 0)
        if not price and candles_1m:
            price = candles_1m[-1].get("close", 0)
        if not price:
            return {"direction": "neutral", "confidence": 0.0,
                    "poc": 0, "vah": 0, "val": 0,
                    "price_position_pct": 0, "poc_drift": 0,
                    "cum_delta_signal": "neutral", "strategy": self.name}

        long_conf = 0.0
        short_conf = 0.0
        poc_drift = 0.0
        cum_delta_signal = "neutral"

        if poc > 0 and vah > 0 and val > 0:
            va_range = vah - val
            if price > vah:
                price_position_pct = min((price - vah) / max(va_range, 0.01), 3.0)
            elif price < val:
                price_position_pct = -min((val - price) / max(va_range, 0.01), 3.0)
            else:
                price_position_pct = (price - poc) / max(va_range, 0.01)

            recent_candle_mid = 0
            if candles_1m:
                c = candles_1m[-1]
                recent_candle_mid = (c.get("high", 0) + c.get("low", 0)) / 2.0
            elif ohlcv:
                c = ohlcv[-1]
                recent_candle_mid = (c.get("high", 0) + c.get("low", 0)) / 2.0
            if recent_candle_mid > 0:
                poc_drift = (poc - recent_candle_mid) / max(poc, 0.01)

            cum_delta_val = cum_delta.get("cumulative_delta", 0) if cum_delta else 0

            if price < val:
                if cum_delta_val > 0:
                    cum_delta_signal = "positive_accumulation"
                    strength = min(abs(price_position_pct) * 0.3, 1.0)
                    long_conf = strength * 0.65
                else:
                    long_conf = 0.20
            elif price > vah:
                if cum_delta_val <= 0:
                    cum_delta_signal = "flat_exhaustion"
                    strength = min(abs(price_position_pct) * 0.3, 1.0)
                    short_conf = strength * 0.65
                else:
                    short_conf = 0.20
            else:
                dist_to_poc_pct = abs(price - poc) / max(poc, 0.01)
                if dist_to_poc_pct < 0.002:
                    return {"direction": "neutral", "confidence": 0.0,
                            "poc": round(poc, 2), "vah": round(vah, 2),
                            "val": round(val, 2),
                            "price_position_pct": round(price_position_pct, 4),
                            "poc_drift": round(poc_drift, 6),
                            "cum_delta_signal": cum_delta_signal,
                            "strategy": self.name}

        else:
            if len(ohlcv) < 20:
                return {"direction": "neutral", "confidence": 0.0,
                        "poc": 0, "vah": 0, "val": 0,
                        "price_position_pct": 0, "poc_drift": 0,
                        "cum_delta_signal": "neutral", "strategy": self.name}
            closes = [c["close"] for c in ohlcv[-20:] if c.get("close")]
            if not closes:
                return {"direction": "neutral", "confidence": 0.0,
                        "poc": 0, "vah": 0, "val": 0,
                        "price_position_pct": 0, "poc_drift": 0,
                        "cum_delta_signal": "neutral", "strategy": self.name}
            sorted_closes = sorted(closes)
            val_proxy = sorted_closes[1]
            vah_proxy = sorted_closes[-2]
            poc_proxy = sum(closes) / len(closes)
            va_range_proxy = vah_proxy - val_proxy
            if va_range_proxy > 0:
                if price > vah_proxy:
                    price_position_pct = min((price - vah_proxy) / va_range_proxy, 3.0)
                    short_conf = 0.25
                elif price < val_proxy:
                    price_position_pct = -min((val_proxy - price) / va_range_proxy, 3.0)
                    long_conf = 0.25
                else:
                    price_position_pct = (price - poc_proxy) / va_range_proxy
                    return {"direction": "neutral", "confidence": 0.0,
                            "poc": round(poc_proxy, 2), "vah": round(vah_proxy, 2),
                            "val": round(val_proxy, 2),
                            "price_position_pct": round(price_position_pct, 4),
                            "poc_drift": 0, "cum_delta_signal": "neutral",
                            "strategy": self.name}
            else:
                price_position_pct = 0.0

        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "poc": round(poc, 2), "vah": round(vah, 2),
                    "val": round(val, 2),
                    "price_position_pct": round(price_position_pct, 4),
                    "poc_drift": round(poc_drift, 6),
                    "cum_delta_signal": cum_delta_signal, "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "poc": round(poc, 2), "vah": round(vah, 2),
                    "val": round(val, 2),
                    "price_position_pct": round(price_position_pct, 4),
                    "poc_drift": round(poc_drift, 6),
                    "cum_delta_signal": cum_delta_signal, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "poc": round(poc, 2), "vah": round(vah, 2),
                "val": round(val, 2),
                "price_position_pct": round(price_position_pct, 4),
                "poc_drift": round(poc_drift, 6),
                "cum_delta_signal": cum_delta_signal, "strategy": self.name}
