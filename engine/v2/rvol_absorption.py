"""
RVOL Absorption Strategy — detects institutional accumulation/distribution
by identifying high-volume bars with unusually small price movement.
"""
import numpy as np
from .base import BaseAdvancedStrategy


class RVOLAbsorptionStrategy(BaseAdvancedStrategy):
    """
    Absorption detection: high RVOL + small price change = smart money accumulating.
    
    AbsorptionScore = RVOL / (1 + PriceChange * 100)
    
    High score + location near value area support/resistance = high probability trade.
    """
    
    def __init__(self):
        super().__init__("rvol_absorption")
        self.regime_compatibility = ["ranging", "high_volatility"]
    
    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])
        
        if not ohlcv or len(ohlcv) < 25:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": "rvol_absorption", "reasons": ["insufficient data"]}
        
        closes = np.array([c["close"] for c in ohlcv])
        volumes = np.array([c["volume"] for c in ohlcv], dtype=float)
        opens = np.array([c["open"] for c in ohlcv])
        
        current_close = closes[-1]
        current_open = opens[-1]
        current_vol = volumes[-1]
        
        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        rvol = current_vol / max(avg_vol, 0.01)
        
        price_change = abs(current_close - current_open) / max(current_open, 0.01)
        
        absorption_score = rvol / max(1 + price_change * 100, 0.01)
        
        value_area = indicators.get("value_area", {})
        poc = value_area.get("poc", current_close)
        vah = value_area.get("vah", current_close * 1.02)
        val = value_area.get("val", current_close * 0.98)
        
        bb = indicators.get("bollinger_bands", {})
        bb_bandwidth = bb.get("bandwidth", 0.04)
        
        direction = "neutral"
        confidence = 0.0
        reasons = []
        
        is_green = current_close > current_open
        
        # IEX depth order flow confirmation
        of = indicators.get("order_flow_imbalance", {})
        iex_dir = of.get("direction", "neutral")
        iex_confirmed = (
            (is_green and iex_dir == "bullish") or
            (not is_green and iex_dir == "bearish")
        )
        iex_conflicting = (
            (is_green and iex_dir == "bearish") or
            (not is_green and iex_dir == "bullish")
        )
        
        if absorption_score > 2.0 and rvol > 1.5:
            if is_green and current_close <= val * 1.01:
                direction = "long"
                confidence = min(absorption_score / 3.0, 0.60)
                reasons.append(f"absorption_at_VA_support(score={absorption_score:.2f})")
            elif not is_green and current_close >= vah * 0.99:
                direction = "short"
                confidence = min(absorption_score / 3.0, 0.60)
                reasons.append(f"absorption_at_VA_resistance(score={absorption_score:.2f})")
        
        if direction != "neutral":
            if rvol > 2.0:
                confidence += 0.10
                reasons.append("very_high_rvol")
            if bb_bandwidth < 0.04:
                confidence += 0.08
                reasons.append("bb_squeeze")
            if iex_confirmed:
                confidence += 0.08
                reasons.append("iex_flow_confirm")
            elif iex_conflicting:
                confidence = max(confidence - 0.12, 0.0)
                reasons.append("iex_flow_conflict")
            confidence = min(confidence, 0.85)
        else:
            reasons.append(f"absorption_score={absorption_score:.2f}_below_threshold")
        
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": "rvol_absorption",
            "reasons": reasons,
        }
