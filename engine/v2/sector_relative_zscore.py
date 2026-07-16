"""
Sector-Relative Z-Score Strategy — statistical arbitrage.
Trades mean reversion of a ticker's price relative to its rolling mean.
Becomes sector-relative when sector ETF data is available.
"""
import numpy as np
from .base import BaseAdvancedStrategy


class SectorRelativeZscoreStrategy(BaseAdvancedStrategy):
    """
    Z-score mean reversion strategy.
    
    Long: z-score < -entry_threshold (ticker is cheap vs its rolling mean)
    Short: z-score > entry_threshold (ticker is expensive)
    Exit: z-score crosses 0
    
    Works best in ranging/low vol regimes.
    """
    
    def __init__(self):
        super().__init__("sector_relative_zscore")
        self.regime_compatibility = ["ranging"]
    
    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])
        
        if not ohlcv or len(ohlcv) < 25:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": "sector_relative_zscore", "reasons": ["insufficient data"]}
        
        closes = np.array([c["close"] for c in ohlcv])
        
        period = 20
        lookback = closes[-period:]
        mean = np.mean(lookback)
        std = np.std(lookback) + 1e-10
        z_score = (closes[-1] - mean) / std
        
        adx = indicators.get("adx", 0)
        if adx == 0:
            from kronos.utils.helpers import calculate_adx
            highs = np.array([c["high"] for c in ohlcv])
            lows = np.array([c["low"] for c in ohlcv])
            adx = calculate_adx(highs.tolist(), lows.tolist(), closes.tolist())
        
        prev_z = 0.0
        if len(closes) > period + 1:
            prev_lookback = closes[-(period+1):-1]
            prev_mean = np.mean(prev_lookback)
            prev_std = np.std(prev_lookback) + 1e-10
            prev_z = (closes[-2] - prev_mean) / prev_std
        
        direction = "neutral"
        confidence = 0.0
        reasons = []
        
        entry_threshold = 2.0
        
        if z_score < -entry_threshold:
            direction = "long"
            confidence = min(abs(z_score) / 3.0, 0.80)
            reasons.append(f"z_score={z_score:.2f}")
            
            if adx < 20:
                confidence = min(confidence + 0.10, 0.90)
                reasons.append("ranging_market_ideal")
            else:
                confidence *= 0.7
                reasons.append(f"trending_market(ADX={adx:.0f})_reducing")
                
        elif z_score > entry_threshold:
            direction = "short"
            confidence = min(abs(z_score) / 3.0, 0.80)
            reasons.append(f"z_score={z_score:.2f}")
            
            if adx < 20:
                confidence = min(confidence + 0.10, 0.90)
                reasons.append("ranging_market_ideal")
            else:
                confidence *= 0.7
                reasons.append(f"trending_market(ADX={adx:.0f})_reducing")
        else:
            reasons.append(f"z_score={z_score:.2f}_within_range")
        
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": "sector_relative_zscore",
            "reasons": reasons,
        }
