from engine.v3.base import BaseV3Strategy


class GammaFlip(BaseV3Strategy):
    name = "gamma_flip"
    description = "Detect gamma regime change — positive-to-negative gamma flip for directional acceleration"
    applies_to = ("future",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        gamma_levels = context.get("es_gamma_levels", {})
        current_price = context.get("current_price", 0)
        ohlcv = context.get("ohlcv", [])

        if not gamma_levels or not current_price:
            if ohlcv:
                current_price = ohlcv[-1].get("close", 0)
            if not current_price:
                return {"direction": "neutral", "confidence": 0.0,
                        "nearest_gamma_level": 0, "distance_pct": 0,
                        "gamma_regime": "unknown", "zero_gamma_price": 0,
                        "strategy": self.name}

        gamma_levels_list = gamma_levels.get("levels", []) if isinstance(gamma_levels, dict) else []
        zero_gamma = gamma_levels.get("zero_gamma", 0) if isinstance(gamma_levels, dict) else 0

        nearest_level = 0
        min_dist = float("inf")
        for level in gamma_levels_list:
            dist = abs(level - current_price)
            if dist < min_dist:
                min_dist = dist
                nearest_level = level

        if nearest_level > 0:
            distance_pct = min_dist / nearest_level
        else:
            distance_pct = 1.0

        zero_gamma_price = zero_gamma
        long_conf = 0.0
        short_conf = 0.0
        gamma_regime = "neutral"

        if zero_gamma > 0 and current_price > 0:
            prev_price = gamma_levels.get("prev_price", current_price)
            was_above = prev_price > zero_gamma
            is_above = current_price > zero_gamma
            if was_above and not is_above:
                gamma_regime = "negative_flip_bearish"
                short_conf = 0.65
            elif not was_above and is_above:
                gamma_regime = "positive_flip_bullish"
                long_conf = 0.65
            elif is_above:
                gamma_regime = "positive_gamma"
            else:
                gamma_regime = "negative_gamma"

        if gamma_regime in ("neutral", "positive_gamma", "negative_gamma") and nearest_level > 0:
            ticker = context.get("ticker", "ES")
            threshold = 0.003 if "ES" in ticker.upper() else 0.005
            if distance_pct < threshold:
                gamma_regime = "gamma_pin"
                if current_price < nearest_level:
                    long_conf = 0.40
                else:
                    short_conf = 0.40
            elif distance_pct > threshold * 3:
                gamma_regime = "no_pin_trend"

        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "nearest_gamma_level": round(nearest_level, 2),
                    "distance_pct": round(distance_pct, 6),
                    "gamma_regime": gamma_regime,
                    "zero_gamma_price": round(zero_gamma_price, 2),
                    "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "nearest_gamma_level": round(nearest_level, 2),
                    "distance_pct": round(distance_pct, 6),
                    "gamma_regime": gamma_regime,
                    "zero_gamma_price": round(zero_gamma_price, 2),
                    "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "nearest_gamma_level": round(nearest_level, 2),
                "distance_pct": round(distance_pct, 6),
                "gamma_regime": gamma_regime,
                "zero_gamma_price": round(zero_gamma_price, 2),
                "strategy": self.name}
