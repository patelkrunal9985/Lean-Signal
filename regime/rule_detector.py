"""
Rule-based market regime detector. No training required — works from day one.
Classifies into: strong_uptrend, strong_downtrend, ranging, high_volatility
"""
import numpy as np
from typing import Any

class RuleBasedRegimeDetector:
    """
    Detects market regime using OHLCV-derived features.
    
    Regimes:
    - strong_uptrend: ADX > 25, EMA slope > 0, price > SMA50
    - strong_downtrend: ADX > 25, EMA slope < 0, price < SMA50
    - ranging: ADX < 20, BB squeeze, low ATR
    - high_volatility: ATR% > 90th percentile, chaotic price action
    """
    
    def __init__(self):
        self.lookback = 20
        self.atr_percentiles = []  # rolling buffer for ATR% percentile
    
    def detect(self, ohlcv: list[dict], indicators: dict) -> dict:
        """
        Detect current regime from OHLCV data and pre-computed indicators.
        
        Parameters
        ----------
        ohlcv : list of dicts with keys: open, high, low, close, volume
        indicators : dict with keys (from TechnicalIndicatorSubagent):
            adx (you must compute), sma_20, sma_50, ema_9, ema_21, 
            atr_14, bollinger_bands (with upper, middle, lower, bandwidth),
            rsi_14, volume_profile
        
        Returns
        -------
        dict:
            primary_regime: str
            confidence: float (0-1)
            scores: dict of per-regime scores
            details: dict with feature values used in detection
        """
        closes = np.array([c["close"] for c in ohlcv])
        highs = np.array([c["high"] for c in ohlcv])
        lows = np.array([c["low"] for c in ohlcv])
        volumes = np.array([c["volume"] for c in ohlcv])
        
        # Compute ADX if not in indicators
        adx = indicators.get("adx", self._compute_adx(highs, lows, closes))
        
        # Extract indicators
        sma20 = indicators.get("sma_20", closes[-1])
        sma50 = indicators.get("sma_50", closes[-1])
        ema9 = indicators.get("ema_9", closes[-1])
        ema21 = indicators.get("ema_21", closes[-1])
        atr = indicators.get("atr_14", 0)
        bb = indicators.get("bollinger_bands", {})
        rsi = indicators.get("rsi_14", indicators.get("rsi", 50))
        current_price = closes[-1]
        
        # Compute features — handle flat/dict bollinger_bands
        if not bb:
            bb_upper = indicators.get("bb_upper", current_price)
            bb_middle = indicators.get("bb_middle", current_price)
            bb_lower = indicators.get("bb_lower", current_price)
            if bb_upper != bb_middle or bb_middle != current_price:
                bb = {"upper": bb_upper, "middle": bb_middle, "lower": bb_lower, "bandwidth": (bb_upper - bb_lower) / max(bb_middle, 0.01)}
        bb_bandwidth = bb.get("bandwidth", (bb.get("upper", current_price) - bb.get("lower", current_price)) / max(bb.get("middle", current_price), 0.01))
        atr_pct = atr / max(current_price, 0.01)
        # Compute BB position (bb_pct) from Bollinger Bands if not already provided
        # bb_pct: 0 = at lower band, 1 = at upper band, <0 = below lower, >1 = above upper
        # Used by exhaustion detection in the RSI check below
        if "bb_pct" not in indicators:
            bb_upper = bb.get("upper", current_price)
            bb_lower = bb.get("lower", current_price)
            if bb_upper > bb_lower:
                indicators["bb_pct"] = (current_price - bb_lower) / (bb_upper - bb_lower)
            else:
                indicators["bb_pct"] = 0.5
        
        # Track ATR percentiles
        self.atr_percentiles.append(atr_pct)
        if len(self.atr_percentiles) > self.lookback:
            self.atr_percentiles.pop(0)
        atr_percentile = sum(1 for a in self.atr_percentiles if a <= atr_pct) / max(len(self.atr_percentiles), 1)
        
        # EMA slope — handle missing ema keys by computing inline
        if ema9 == closes[-1] and ema21 == closes[-1] and len(closes) >= 22:
            ema9 = sum(closes[-9:]) / 9
            ema21 = sum(closes[-21:]) / 21
        ema_slope = (ema9 - ema21) / max(ema21, 0.01)
        
        # Price vs SMA — handle sma50=0 (not computed) by using sma20 as proxy
        price_vs_sma50 = (current_price - sma50) / max(sma50, 0.01)
        if sma50 <= 0:
            price_vs_sma50 = (current_price - sma20) / max(sma20, 0.01)
        price_vs_sma20 = (current_price - sma20) / max(sma20, 0.01)
        
        # Consecutive direction count
        price_changes = np.diff(closes[-min(len(closes), 15):])
        consecutive_up = 0
        consecutive_down = 0
        for c in reversed(price_changes):
            if c > 0:
                consecutive_up += 1
                consecutive_down = 0
            elif c < 0:
                consecutive_down += 1
                consecutive_up = 0
            else:
                break
        
        # Volume ratio (needed early for volatility check below)
        current_vol = volumes[-1] if len(volumes) > 0 else 0
        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes) if len(volumes) > 0 else 1
        vol_ratio = current_vol / max(avg_vol, 0.01)
        
        # Score each regime
        scores = {
            "strong_uptrend": 0.0,
            "strong_downtrend": 0.0,
            "ranging": 0.0,
            "high_volatility": 0.0,
        }
        
        # High volatility check
        if atr_pct > 0.03 or atr_percentile > 0.85 or bb_bandwidth > 0.08:
            scores["high_volatility"] += 0.6
            if vol_ratio > 1.5:
                scores["high_volatility"] += 0.2
        elif atr_pct < 0.01 and bb_bandwidth < 0.03:
            scores["ranging"] += 0.3
            
        # Trend check
        if adx > 25:
            if ema_slope > 0.001 and price_vs_sma50 > 0.02:
                scores["strong_uptrend"] += 0.5 + min(adx / 100, 0.3)
            elif ema_slope < -0.001 and price_vs_sma50 < -0.02:
                scores["strong_downtrend"] += 0.5 + min(adx / 100, 0.3)
        elif adx < 20:
            scores["ranging"] += 0.4
        
        # Consecutive bar check
        if consecutive_up >= 5 and adx > 20:
            scores["strong_uptrend"] += 0.2
        elif consecutive_down >= 5 and adx > 20:
            scores["strong_downtrend"] += 0.2
        
        # Price vs SMA20 fallback (works even when ADX is low/zero)
        # Catches NQ=F uptrend where ADX=0 but price is +2% above SMA20
        if price_vs_sma20 > 0.02:
            scores["strong_uptrend"] += 0.3
            if consecutive_up >= 3 and adx >= 15:
                scores["strong_uptrend"] += 0.15
        elif price_vs_sma20 < -0.02:
            scores["strong_downtrend"] += 0.3
            if consecutive_down >= 3 and adx >= 15:
                scores["strong_downtrend"] += 0.15
        # Moderate SMA20 deviation + consec bars catches GC=F -3.8% below SMA20
        if price_vs_sma20 > 0.01 and consecutive_up >= 3 and adx >= 15:
            scores["strong_uptrend"] += 0.15
        elif price_vs_sma20 < -0.01 and consecutive_down >= 3 and adx >= 15:
            scores["strong_downtrend"] += 0.15
        
        # RSI check — with over-extension awareness
        # Extreme RSI values signal trend exhaustion, not trend strength
        bb_pct = indicators.get("bb_pct", indicators.get("bb_percent", 0.5))
        if 40 <= rsi <= 60:
            scores["ranging"] += 0.2
        elif rsi > 65 and adx > 20:
            scores["strong_uptrend"] += 0.15
            # Over-extension penalty: RSI > 75 + price above upper BB → exhaustion
            if rsi > 75 and bb_pct > 0.9:
                scores["strong_uptrend"] -= 0.4
                scores["high_volatility"] += 0.3
                scores["ranging"] += 0.15
        elif rsi < 35 and adx > 20:
            # Over-extension penalty: RSI < 25 + price below lower BB → exhaustion
            if rsi < 25 and bb_pct < 0.1:
                scores["strong_downtrend"] -= 0.4
                scores["high_volatility"] += 0.3
                scores["ranging"] += 0.15
            else:
                scores["strong_downtrend"] += 0.15
        
        # BB squeeze for ranging
        if bb_bandwidth < 0.03 and adx < 20:
            scores["ranging"] += 0.3
        
        # Volume confirmation
        if vol_ratio > 1.5 and scores["strong_uptrend"] > 0.3:
            scores["strong_uptrend"] += 0.1
        elif vol_ratio > 1.5 and scores["strong_downtrend"] > 0.3:
            scores["strong_downtrend"] += 0.1
            
        # Determine primary regime
        primary_regime = max(scores, key=scores.get)
        confidence = scores[primary_regime]
        
        # Fallback: if no regime scores above threshold, default to ranging
        if confidence < 0.3:
            primary_regime = "ranging"
            confidence = 0.3
        
        return {
            "primary_regime": primary_regime,
            "confidence": round(confidence, 4),
            "scores": {k: round(v, 4) for k, v in scores.items()},
            "details": {
                "adx": round(adx, 2),
                "atr_pct": round(atr_pct, 4),
                "bb_bandwidth": round(bb_bandwidth, 4),
                "ema_slope": round(ema_slope, 4),
                "price_vs_sma50": round(price_vs_sma50, 4),
                "consecutive_bars": consecutive_up if consecutive_up > consecutive_down else -consecutive_down,
                "volume_ratio": round(vol_ratio, 2),
                "rsi": round(rsi, 1) if rsi else 50,
                "atr_percentile": round(atr_percentile, 4),
            },
        }
    
    def _compute_adx(self, highs, lows, closes, period=14):
        """Compute Average Directional Index."""
        if len(closes) < period + 1:
            return 15.0  # neutral default
        
        # True Range
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            ),
        )
        atr = np.mean(tr[-period:])
        
        # Directional Movement
        up_move = highs[1:] - highs[:-1]
        down_move = lows[:-1] - lows[1:]
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        # Smoothed DM
        plus_di = 100 * np.mean(plus_dm[-period:]) / max(atr, 0.001)
        minus_di = 100 * np.mean(minus_dm[-period:]) / max(atr, 0.001)
        
        # DX = |DI+ - DI-| / (DI+ + DI-)
        dx = abs(plus_di - minus_di) / max(plus_di + minus_di, 0.001)
        
        # ADX = SMA of DX
        return float(dx * 100)  # simplified ADX
