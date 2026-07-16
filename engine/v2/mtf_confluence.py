"""
Multi-Timeframe Confluence Strategy — requires direction alignment across
multiple resampled timeframes to generate signals. Eliminates false signals
that only appear on a single timeframe.
"""
import numpy as np
from .base import BaseAdvancedStrategy


class MTFConfluenceStrategy(BaseAdvancedStrategy):
    """
    Resamples OHLCV at 3 group sizes and requires 2/3 to agree on direction.
    
    Timeframe 1: 1 bar (fast)
    Timeframe 2: 5 bars (medium)  
    Timeframe 3: 21 bars (slow)
    
    Computes VW Momentum + ADX on each timeframe.
    Signal = direction if 2+ timeframes agree.
    """
    
    def __init__(self):
        super().__init__("mtf_confluence")
        self.regime_compatibility = ["strong_uptrend", "strong_downtrend"]
    
    def _resample_ohlcv(self, ohlcv: list[dict], group_size: int) -> list[dict]:
        if group_size <= 1:
            return ohlcv
        
        result = []
        for i in range(0, len(ohlcv), group_size):
            chunk = ohlcv[i:i+group_size]
            if not chunk:
                continue
            result.append({
                "open": chunk[0]["open"],
                "high": max(c["high"] for c in chunk),
                "low": min(c["low"] for c in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(c["volume"] for c in chunk),
            })
        return result
    
    def _compute_vw_momentum(self, ohlcv: list[dict]) -> tuple:
        if len(ohlcv) < 10:
            return 0.0, 0.0, 0.0
        
        closes = np.array([c["close"] for c in ohlcv])
        volumes = np.array([c["volume"] for c in ohlcv], dtype=float)
        highs = np.array([c["high"] for c in ohlcv])
        lows = np.array([c["low"] for c in ohlcv])
        
        roc = (closes[-1] - closes[-11]) / max(closes[-11], 0.01) if len(closes) >= 11 else 0
        
        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        vol_ratio = volumes[-1] / max(avg_vol, 0.01)
        
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])),
        )
        atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr) if len(tr) > 0 else 0.01
        atr_pct = atr / max(closes[-1], 0.01)
        
        signal = (roc * vol_ratio) / max(atr_pct, 0.001)
        
        up_move = highs[1:] - highs[:-1]
        down_move = lows[:-1] - lows[1:]
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_di = 100 * np.mean(plus_dm[-14:]) / max(atr, 0.001)
        minus_di = 100 * np.mean(minus_dm[-14:]) / max(atr, 0.001)
        dx = abs(plus_di - minus_di) / max(plus_di + minus_di, 0.001)
        adx_val = dx * 100
        
        return signal, adx_val, vol_ratio
    
    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        ohlcv = context.get("ohlcv", [])
        
        if not ohlcv or len(ohlcv) < 30:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": "mtf_confluence", "reasons": ["insufficient data"]}
        
        group_sizes = [1, 2, 3]
        tf_results = []
        
        for gs in group_sizes:
            resampled = self._resample_ohlcv(ohlcv, gs)
            signal, adx_val, vol_ratio = self._compute_vw_momentum(resampled)
            
            if adx_val > 20 and vol_ratio > 1.0:
                if signal > 0.05:
                    tf_results.append("long")
                elif signal < -0.05:
                    tf_results.append("short")
                else:
                    tf_results.append("neutral")
            else:
                tf_results.append("neutral")
        
        long_count = tf_results.count("long")
        short_count = tf_results.count("short")
        
        direction = "neutral"
        confidence = 0.0
        reasons = [f"TF_results={tf_results}"]
        
        if long_count >= 2:
            direction = "long"
            if long_count == 3:
                confidence = 0.80
                reasons.append("all_3_TF_agree_long")
            else:
                confidence = 0.55
                reasons.append(f"{long_count}/3_TF_long")
        elif short_count >= 2:
            direction = "short"
            if short_count == 3:
                confidence = 0.80
                reasons.append("all_3_TF_agree_short")
            else:
                confidence = 0.55
                reasons.append(f"{short_count}/3_TF_short")
        else:
            reasons.append("mixed_TF_signals")
        
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": "mtf_confluence",
            "reasons": reasons,
        }
