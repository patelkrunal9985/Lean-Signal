"""
VWAP Deviation Strategy — Day-trade mean reversion to VWAP.
Trades reversal when price is significantly displaced from anchored VWAP
with volume confirmation.
"""



class VWAPDeviationStrategy:
    """Day-trade strategy: fade extreme VWAP deviations with volume confirmation."""

    def __init__(self):
        self.name = "vwap_deviation"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])

        if not ohlcv or len(ohlcv) < 15:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0,
                    "strategy": self.name, "reasons": ["insufficient data"]}

        avwap = indicators.get("anchored_vwap") or {}
        avwap_dist = avwap.get("distance_pct", 0)
        close = ohlcv[-1]["close"]
        volume = ohlcv[-1]["volume"]
        avg_vol = (indicators.get("volume_profile") or {}).get("avg_volume", 1)
        vol_ratio = volume / max(avg_vol, 1)
        atr = indicators.get("atr_14", 0)
        atr_pct = atr / max(close, 0.01) if atr > 0 else 0.015
        # Order flow: IEX Depth of Book (primary) or OHLCV proxy fallback
        order_flow = indicators.get("order_flow_imbalance") or {}
        if ticker:
            try:
                from engine.skills.ibkr_data_feed import get_order_book_imbalance
                ob = get_order_book_imbalance(ticker)
                if ob and ob.get("pressure", 0) > 0:
                    order_flow = {
                        "direction": ob.get("direction", "neutral"),
                        "ratio": ob.get("imbalance_ratio", 0),
                        "source": "iex_depth",
                    }
            except Exception:
                pass
        flow_dir = order_flow.get("direction", "neutral")
        of_ratio = order_flow.get("ratio", 0.5)
        rsi = indicators.get("rsi_14", 50)
        val_area = indicators.get("value_area") or {}
        vah = val_area.get("vah", 0)
        val = val_area.get("val", 0)
        liq = indicators.get("liquidity_sweep") or {}
        liq_detected = liq.get("detected", False)
        liq_type = liq.get("type", "")

        direction = "neutral"
        confidence = 0.0
        reasons = []

        # Long setup: price below VWAP (bearish deviation) with buying pressure
        if avwap_dist < -atr_pct * 0.5 and vol_ratio > 1.0:
            deviation_strength = abs(avwap_dist) / max(atr_pct * 2.0, 0.001)
            base_conf = min(deviation_strength, 0.70)
            if flow_dir == "bullish" and of_ratio > 0.55:
                base_conf = min(base_conf + 0.12, 0.85)
                reasons.append("bullish_flow")
            if val > 0 and close <= val * 1.01:
                base_conf = min(base_conf + 0.10, 0.88)
                reasons.append("value_area_support")
            if rsi < 40:
                base_conf = min(base_conf + 0.08, 0.88)
                reasons.append(f"oversold_rsi={rsi:.0f}")
            if liq_detected and liq_type == "bullish_liquidity_sweep":
                base_conf = min(base_conf + 0.10, 0.90)
                reasons.append("liq_sweep")
            if base_conf >= 0.20:
                direction = "long"
                confidence = base_conf
                reasons.append(f"vwap_dev={avwap_dist:.4f}_atrpct={atr_pct:.4f}")

        # Short setup: price above VWAP (bullish deviation) with selling pressure
        elif avwap_dist > atr_pct * 0.5 and vol_ratio > 1.0:
            deviation_strength = abs(avwap_dist) / max(atr_pct * 2.0, 0.001)
            base_conf = min(deviation_strength, 0.70)
            if flow_dir == "bearish" and of_ratio < 0.45:
                base_conf = min(base_conf + 0.12, 0.85)
                reasons.append("bearish_flow")
            if vah > 0 and close >= vah * 0.99:
                base_conf = min(base_conf + 0.10, 0.88)
                reasons.append("value_area_resistance")
            if rsi > 60:
                base_conf = min(base_conf + 0.08, 0.88)
                reasons.append(f"overbought_rsi={rsi:.0f}")
            if liq_detected and liq_type == "bearish_liquidity_sweep":
                base_conf = min(base_conf + 0.10, 0.90)
                reasons.append("liq_sweep")
            if base_conf >= 0.20:
                direction = "short"
                confidence = base_conf
                reasons.append(f"vwap_dev={avwap_dist:.4f}_atrpct={atr_pct:.4f}")

        if not reasons:
            reasons.append(f"vwap_dist={avwap_dist:.4f}_no_setup")

        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
            "vwap_distance_pct": round(avwap_dist, 4),
        }
