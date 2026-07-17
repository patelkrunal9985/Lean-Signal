
class PullbackStrategy:
    def __init__(self):
        self.name = "pullback"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])
        if not ohlcv or len(ohlcv) < 20:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["insufficient_data"]}
        closes = [c["close"] for c in ohlcv]
        close = closes[-1]
        atr = indicators.get("atr_14", 0)
        atr_pct = atr / max(close, 0.01) if atr > 0 else 0.015
        avwap = indicators.get("anchored_vwap") or {}
        vwap_price = avwap.get("avwap", 0) or avwap.get("vwap", 0) or avwap.get("price", 0)
        ema_21 = indicators.get("ema_21", 0)
        sma_20 = indicators.get("sma_20", 0)
        rsi = indicators.get("rsi_14", 50)
        macd = indicators.get("macd", {})
        macd_hist = macd.get("histogram", 0) if isinstance(macd, dict) else 0
        adx = indicators.get("adx", 0)
        if adx == 0 and len(closes) >= 15:
            from utils.helpers import calculate_adx
            highs_np = [c["high"] for c in ohlcv]
            lows_np = [c["low"] for c in ohlcv]
            adx = calculate_adx(highs_np, lows_np, closes)
        trend = indicators.get("trend", "neutral")
        avg_vol = (indicators.get("volume_profile") or {}).get("avg_volume", 1)
        volume = ohlcv[-1]["volume"]
        vol_ratio = volume / max(avg_vol, 1)
        qty = indicators.get("quote", {})
        live_price = qty.get("price", 0) if isinstance(qty, dict) else 0
        current = live_price if live_price > 0 else close
        direction = "neutral"
        confidence = 0.0
        reasons = []
        ref_price = vwap_price if vwap_price > 0 else ema_21 if ema_21 > 0 else sma_20
        if ref_price <= 0:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["no_ref_price"]}
        dist = (current - ref_price) / max(ref_price, 0.01)
        trend_up = trend == "bullish" or sma_20 > ema_21
        trend_down = trend == "bearish" or sma_20 < ema_21
        if trend_up and dist < 0 and abs(dist) < atr_pct * 1.5:
            base = max(0.40, 0.50 - abs(dist) / atr_pct * 0.20)
            if vol_ratio < 1.0:
                base = min(base + 0.08, 0.80)
                reasons.append(f"drying_vol={vol_ratio:.2f}x")
            if macd_hist > 0:
                base = min(base + 0.06, 0.82)
                reasons.append("macd_hist_turning")
            if rsi > 40:
                base = min(base + 0.05, 0.85)
                reasons.append(f"rsi_ok={rsi:.0f}")
            if adx > 20:
                base = min(base + 0.08, 0.85)
                reasons.append(f"trend_adx={adx:.0f}")
            if base >= 0.30:
                direction = "long"
                confidence = base
                reasons.append(f"pullback_vwap_dist={dist:.4f}")
        elif trend_down and dist > 0 and abs(dist) < atr_pct * 1.5:
            base = max(0.40, 0.50 - abs(dist) / atr_pct * 0.20)
            if vol_ratio < 1.0:
                base = min(base + 0.08, 0.80)
                reasons.append(f"drying_vol={vol_ratio:.2f}x")
            if macd_hist < 0:
                base = min(base + 0.06, 0.82)
                reasons.append("macd_hist_turning")
            if rsi < 60:
                base = min(base + 0.05, 0.85)
                reasons.append(f"rsi_ok={rsi:.0f}")
            if adx > 20:
                base = min(base + 0.08, 0.85)
                reasons.append(f"trend_adx={adx:.0f}")
            if base >= 0.30:
                direction = "short"
                confidence = base
                reasons.append(f"pullback_vwap_dist={dist:.4f}")
        if not reasons:
            reasons.append(f"no_pullback_trend={trend}_dist={dist:.4f}")

        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
        }
