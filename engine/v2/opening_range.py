
class OpeningRangeStrategy:
    def __init__(self):
        self.name = "opening_range"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])
        if not ohlcv or len(ohlcv) < 5:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["insufficient_data"]}
        close = ohlcv[-1]["close"]
        volume = ohlcv[-1]["volume"]
        atr = indicators.get("atr_14", 0)
        atr_pct = atr / max(close, 0.01) if atr > 0 else 0.015
        avg_vol = (indicators.get("volume_profile") or {}).get("avg_volume", 1)
        vol_ratio = volume / max(avg_vol, 1)
        intraday_bars = None
        try:
            from engine.skills.ibkr_data_feed import fetch_historical_bars
            intraday_bars = fetch_historical_bars(ticker, duration="1 D", bar_size="1 min")
        except Exception:
            pass
        prev_high = max(c["high"] for c in ohlcv[-5:])
        prev_low = min(c["low"] for c in ohlcv[-5:])
        if not intraday_bars or len(intraday_bars) < 5:
            range_high = prev_high
            range_low = prev_low
        else:
            session_bars = [b for b in intraday_bars if b.get("volume", 0) > 0]
            n = min(30, max(5, len(session_bars) // 4))
            range_bars = session_bars[:n]
            if range_bars:
                range_high = max(b["high"] for b in range_bars)
                range_low = min(b["low"] for b in range_bars)
            else:
                range_high = prev_high
                range_low = prev_low
        direction = "neutral"
        confidence = 0.0
        reasons = []
        qty = indicators.get("quote", {})
        live_price = qty.get("price", 0) if isinstance(qty, dict) else 0
        current = live_price if live_price > 0 else close
        dist_high = (current - range_high) / max(range_high, 0.01)
        dist_low = (range_low - current) / max(range_low, 0.01) if range_low > 0 else 0
        if dist_high > atr_pct * 0.3 and vol_ratio > 1.2:
            base = min(dist_high / atr_pct * 0.70, 0.70)
            if vol_ratio > 1.5:
                base = min(base + 0.10, 0.80)
                reasons.append(f"high_vol={vol_ratio:.1f}x")
            macd = indicators.get("macd", {})
            if isinstance(macd, dict) and macd.get("macd", 0) > macd.get("signal", 0):
                base = min(base + 0.08, 0.85)
                reasons.append("macd_confirm")
            if base >= 0.20:
                direction = "long"
                confidence = base
                reasons.append(f"range_breakout_high={dist_high:.4f}")
        elif dist_low > atr_pct * 0.3 and vol_ratio > 1.2:
            base = min(dist_low / atr_pct * 0.70, 0.70)
            if vol_ratio > 1.5:
                base = min(base + 0.10, 0.80)
                reasons.append(f"high_vol={vol_ratio:.1f}x")
            macd = indicators.get("macd", {})
            if isinstance(macd, dict) and macd.get("macd", 0) < macd.get("signal", 0):
                base = min(base + 0.08, 0.85)
                reasons.append("macd_confirm")
            if base >= 0.20:
                direction = "short"
                confidence = base
                reasons.append(f"range_breakdown_low={dist_low:.4f}")
        if not reasons:
            reasons.append(f"range_high_d={dist_high:.4f}_low_d={dist_low:.4f}")
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
        }
