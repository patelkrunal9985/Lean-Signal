"""
VW Momentum Strategy — Volume-weighted momentum.
Replaces TrendMomentum with a signal that weights price changes by volume.
"""
import numpy as np
from .base import BaseAdvancedStrategy


class VWMomentumStrategy(BaseAdvancedStrategy):
    """
    Volume-weighted momentum strategy.
    
    Long: signal > threshold AND ADX > min_adx AND volume confirms
    Short: signal < -threshold AND ADX > min_adx AND volume confirms
    """
    
    def __init__(self):
        super().__init__("vw_momentum")
        self.regime_compatibility = ["strong_uptrend", "strong_downtrend", "high_volatility"]
    
    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])
        
        if not ohlcv or len(ohlcv) < 20:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": "vw_momentum", "reasons": ["insufficient data"]}
        
        closes = np.array([c["close"] for c in ohlcv])
        volumes = np.array([c["volume"] for c in ohlcv], dtype=float)
        
        roc_10 = (closes[-1] - closes[-11]) / max(closes[-11], 0.01) if len(closes) >= 11 else 0
        
        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        vol_ratio = volumes[-1] / max(avg_vol, 0.01)
        
        atr = indicators.get("atr_14", 0)
        atr_pct = atr / max(closes[-1], 0.01) if atr > 0 else 0.01
        
        signal = (roc_10 * vol_ratio) / max(atr_pct, 0.001)
        
        adx = indicators.get("adx", 0)
        if adx == 0 and len(closes) >= 15:
            from kronos.utils.helpers import calculate_adx
            highs = np.array([c["high"] for c in ohlcv])
            lows = np.array([c["low"] for c in ohlcv])
            adx = calculate_adx(highs.tolist(), lows.tolist(), closes.tolist())
        
        bb = indicators.get("bollinger_bands", {})
        bb_bandwidth = bb.get("bandwidth", 0.05)
        
        threshold = 0.08
        direction = "neutral"
        confidence = 0.0
        reasons = []
        
        if signal > threshold and adx > 20 and vol_ratio > 1.2:
            direction = "long"
            confidence = min(abs(signal) / 0.15, 0.85)
            reasons.append(f"VW_Mom={signal:.3f}")
            if adx > 30:
                confidence = min(confidence + 0.08, 0.90)
                reasons.append(f"strong_trend(ADX={adx:.0f})")
            if bb_bandwidth > 0.05:
                confidence = min(confidence + 0.05, 0.95)
                reasons.append("wide_bb_confirm")
                
        elif signal < -threshold and adx > 20 and vol_ratio > 1.2:
            direction = "short"
            confidence = min(abs(signal) / 0.15, 0.85)
            reasons.append(f"VW_Mom={signal:.3f}")
            if adx > 30:
                confidence = min(confidence + 0.08, 0.90)
                reasons.append(f"strong_trend(ADX={adx:.0f})")
            if bb_bandwidth > 0.05:
                confidence = min(confidence + 0.05, 0.95)
                reasons.append("wide_bb_confirm")
        else:
            reasons.append(f"signal={signal:.3f}_below_threshold")
        
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": "vw_momentum",
            "reasons": reasons,
        }
