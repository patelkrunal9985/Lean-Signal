from engine.v3.base import BaseV3Strategy


class GammaPin(BaseV3Strategy):
    name = "gamma_pin"
    description = "Gamma flip level magnet — ES price pulled toward zero-GEX cross"
    applies_to = ("future",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        ticker = context.get("ticker", "").upper()
        # Gamma flip levels only meaningful for ES/NQ/YM/RTY (SPX-derivatives)
        if not any(ticker.startswith(p) for p in ("ES", "NQ", "YM", "RTY")):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        gamma = context.get("es_gamma_levels", {})
        ohlcv = context.get("ohlcv", [])
        if not gamma or len(ohlcv) < 5:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        flip_levels = gamma.get("flip_levels", [])
        nearest_flip = gamma.get("nearest_flip")
        total_net_gex = gamma.get("total_net_gex", 0)
        if not flip_levels or not nearest_flip:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        spx_ratio = 10.0
        current = ohlcv[-1]["close"]
        es_to_spx = current * spx_ratio
        distance = nearest_flip - es_to_spx
        distance_pct = abs(distance) / max(es_to_spx, 1)
        distance_atr = _get_atr(ohlcv) * spx_ratio
        long_conf = 0.0
        short_conf = 0.0
        flip_is_below = distance > 0
        flip_is_above = distance < 0
        if distance_pct < 0.01 and distance_atr > 0:
            pin_strength = max(0, min(1.0 - (distance_pct / 0.01), 1.0))
            if total_net_gex < 0 and flip_is_below:
                short_conf = pin_strength * 0.50
            elif total_net_gex > 0 and flip_is_above:
                long_conf = pin_strength * 0.50
            else:
                long_conf = pin_strength * 0.30 if flip_is_above else 0
                short_conf = pin_strength * 0.30 if flip_is_below else 0
        if not long_conf and not short_conf:
            if distance_pct < 0.005:
                long_conf = 0.20
                short_conf = 0.20
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "nearest_flip": nearest_flip, "flip_distance_pct": round(distance_pct, 6),
                    "total_net_gex": round(total_net_gex, 0), "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "nearest_flip": nearest_flip, "flip_distance_pct": round(distance_pct, 6),
                    "total_net_gex": round(total_net_gex, 0), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "nearest_flip": nearest_flip, "flip_distance_pct": round(distance_pct, 6),
                "total_net_gex": round(total_net_gex, 0), "strategy": self.name}


def _get_atr(ohlcv: list) -> float:
    if len(ohlcv) < 14:
        return 0.0
    trs = []
    for i in range(-13, 0):
        tr = max(ohlcv[i]["high"] - ohlcv[i]["low"],
                 abs(ohlcv[i]["high"] - ohlcv[i-1]["close"]),
                 abs(ohlcv[i]["low"] - ohlcv[i-1]["close"]))
        trs.append(tr)
    return sum(trs) / len(trs)
