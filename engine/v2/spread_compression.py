
class SpreadCompressionStrategy:
    def __init__(self):
        self.name = "spread_compression"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])
        if not ohlcv or len(ohlcv) < 10:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["insufficient_data"]}
        bid = indicators.get("bid", 0)
        ask = indicators.get("ask", 0)
        if bid <= 0 or ask <= 0 or ask <= bid:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["no_iex_spread"]}
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid
        closes = [c["close"] for c in ohlcv]
        close = closes[-1]
        atr = indicators.get("atr_14", 0)
        atr_pct = atr / max(close, 0.01) if atr > 0 else 0.015
        rsi = indicators.get("rsi_14", 50)
        candle_ranges = [(c["high"] - c["low"]) / max(c["close"], 0.01) for c in ohlcv[-20:] if c.get("high", 0) > 0]
        if len(candle_ranges) < 5:
            candle_ranges = [0.015] * 5
        min_cr = min(candle_ranges)
        max_cr = max(candle_ranges)
        range_cr = max(max_cr - min_cr, 0.0001)
        current_cr = candle_ranges[-1] if candle_ranges else 0.015
        norm_cr = (current_cr - min_cr) / range_cr
        spread_norm = (spread_pct / (current_cr + 0.0001)) * 10
        compressed = spread_norm < 0.5 and norm_cr < 0.3
        expanding = spread_norm > 1.5 or norm_cr > 0.75
        direction = "neutral"
        confidence = 0.0
        reasons = []
        if compressed and atr_pct > 0.005:
            macd = indicators.get("macd", {})
            rsi_div = rsi - 50
            macd_hist = macd.get("histogram", 0) if isinstance(macd, dict) else 0
            base = 0.40
            reasons.append(f"spread_compressed_sn={spread_norm:.2f}_cr={norm_cr:.2f}")
            if rsi_div > 5 and macd_hist > 0:
                base = min(base + 0.15, 0.65)
                direction = "long"
                reasons.append(f"bullish_div_rsi={rsi:.0f}")
            elif rsi_div < -5 and macd_hist < 0:
                base = min(base + 0.15, 0.65)
                direction = "short"
                reasons.append(f"bearish_div_rsi={rsi:.0f}")
            else:
                reasons.append("no_direction_div")
            confidence = base
        elif expanding:
            reasons.append(f"spread_expanding_sn={spread_norm:.2f}_cr={norm_cr:.2f}")
        else:
            reasons.append(f"spread_normal_sn={spread_norm:.2f}_cr={norm_cr:.2f}")
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
        }
