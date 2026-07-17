from engine.v3.base import BaseV3Strategy


class MomentumCross(BaseV3Strategy):
    name = "momentum_cross"
    description = "RSI(14)>55 + MACD bullish + close above VWAP for regime-confirmed momentum"
    applies_to = ("future",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        ohlcv = context.get("ohlcv", [])
        if len(ohlcv) < 20:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        closes = [c["close"] for c in ohlcv]
        current = closes[-1]
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 0
        avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 1
        rsi = 100 - (100 / (1 + avg_gain / max(avg_loss, 1e-10))) if avg_loss > 0 else 100
        macd_vals = []
        ema12, ema26 = 0.0, 0.0
        for i, c in enumerate(closes):
            if i == 0:
                ema12 = ema26 = float(c)
            else:
                ema12 = c * (2/13) + ema12 * (11/13)
                ema26 = c * (2/27) + ema26 * (25/27)
            macd_vals.append(ema12 - ema26)
        macd = macd_vals[-1]
        signal = sum(macd_vals[-9:]) / 9 if len(macd_vals) >= 9 else 0
        macd_bullish = macd > signal
        vwap = context.get("intraday_vwap", {}).get("vwap", 0)
        price_above_vwap = current > vwap if vwap > 0 else False
        long_conf = 0.0
        short_conf = 0.0
        if rsi > 55 and macd_bullish and price_above_vwap:
            strength = min((rsi - 55) / 30, 1.0)
            vol_ratio = _volume_ratio(ohlcv)
            if vol_ratio > 1.0:
                strength = min(strength * 1.15, 1.0)
            long_conf = strength * 0.70
        if rsi < 45 and not macd_bullish and not price_above_vwap:
            strength = min((45 - rsi) / 30, 1.0)
            short_conf = strength * 0.65
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "rsi": round(rsi, 1), "macd_bullish": macd_bullish, "price_above_vwap": price_above_vwap,
                    "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "rsi": round(rsi, 1), "macd_bullish": macd_bullish, "price_above_vwap": price_above_vwap,
                    "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "rsi": round(rsi, 1), "strategy": self.name}


def _volume_ratio(ohlcv: list) -> float:
    vols = [c.get("volume", 0) for c in ohlcv if c.get("volume", 0) > 0]
    if len(vols) < 5:
        return 1.0
    avg = sum(vols[:-1]) / max(len(vols) - 1, 1)
    return vols[-1] / max(avg, 1)
