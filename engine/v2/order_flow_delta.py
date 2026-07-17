"""
Order Flow Delta Proxy Strategy — detects institutional accumulation/distribution
from OHLCV data without requiring Level 2 order book.
"""
import numpy as np
from .base import BaseAdvancedStrategy


class OrderFlowDeltaStrategy(BaseAdvancedStrategy):
    """
    Uses OHLCV-derived delta to detect buying/selling pressure.
    
    Delta = volume * ((close - open) / (high - low))
    Cumulative delta divergence = price making new highs/lows but delta disagrees.
    
    Long: bullish divergence (price making lows, delta rising) or rejection candle
    Short: bearish divergence (price making highs, delta falling) or rejection candle
    """
    
    def __init__(self):
        super().__init__("order_flow_delta")
        self.regime_compatibility = ["high_volatility", "ranging", "strong_uptrend", "strong_downtrend"]
    
    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])

        # ── Primary: IEX Depth of Book as real order flow signal ──
        if ticker:
            try:
                from engine.skills.ibkr_data_feed import get_order_book_imbalance
                ob = get_order_book_imbalance(ticker)
                if ob and ob.get("pressure", 0) > 0.03:
                    ob_ratio = ob.get("imbalance_ratio", 0)
                    ob_pressure = ob.get("pressure", 0)
                    confidence = min(ob_pressure * 3.0, 0.85)
                    if ob_ratio > 0.1:
                        return {
                            "ticker": ticker,
                            "direction": "long",
                            "confidence": round(confidence, 4),
                            "strategy": "order_flow_delta",
                            "reasons": [f"iex_depth(ratio={ob_ratio:.4f},pressure={ob_pressure:.4f})"],
                            "imbalance_ratio": ob_ratio,
                            "bid_volume": ob.get("bid_volume", 0),
                            "ask_volume": ob.get("ask_volume", 0),
                            "source": "iex_depth",
                        }
                    elif ob_ratio < -0.1:
                        return {
                            "ticker": ticker,
                            "direction": "short",
                            "confidence": round(confidence, 4),
                            "strategy": "order_flow_delta",
                            "reasons": [f"iex_depth(ratio={ob_ratio:.4f},pressure={ob_pressure:.4f})"],
                            "imbalance_ratio": ob_ratio,
                            "bid_volume": ob.get("bid_volume", 0),
                            "ask_volume": ob.get("ask_volume", 0),
                            "source": "iex_depth",
                        }
                    return {
                        "ticker": ticker,
                        "direction": "neutral",
                        "confidence": 0.0,
                        "strategy": "order_flow_delta",
                        "reasons": [f"iex_depth_balanced(ratio={ob_ratio:.4f})"],
                        "source": "iex_depth",
                    }
            except Exception:
                pass

        # ── Fallback: OHLCV-derived delta proxy ──
        if not ohlcv or len(ohlcv) < 15:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": "order_flow_delta", "reasons": ["insufficient data"]}
        
        deltas = []
        for c in ohlcv[-30:]:
            o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
            vol = c["volume"]
            candle_range = max(h - l, 0.001)
            delta = vol * ((cl - o) / candle_range)
            deltas.append(delta)
        
        deltas = np.array(deltas)
        cumulative_delta = np.sum(deltas[-10:])
        
        closes = np.array([c["close"] for c in ohlcv[-30:]])
        price_change_5 = closes[-1] - closes[-6] if len(closes) >= 6 else 0
        
        bullish_div = price_change_5 < 0 and cumulative_delta > 0
        bearish_div = price_change_5 > 0 and cumulative_delta < 0
        
        last_3 = ohlcv[-3:]
        large_rejection = False
        rejection_direction = "neutral"
        
        for c in last_3:
            body = abs(c["close"] - c["open"])
            upper_wick = c["high"] - max(c["close"], c["open"])
            lower_wick = min(c["close"], c["open"]) - c["low"]
            candle_range = max(c["high"] - c["low"], 0.001)
            
            if body / candle_range < 0.3:
                continue
            
            if lower_wick > body * 2 and upper_wick < body * 0.3:
                large_rejection = True
                rejection_direction = "long"
            elif upper_wick > body * 2 and lower_wick < body * 0.3:
                large_rejection = True
                rejection_direction = "short"
        
        volumes = np.array([c["volume"] for c in ohlcv[-30:]])
        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        vol_ratio = volumes[-1] / max(avg_vol, 0.01)
        
        liq_sweep = indicators.get("liquidity_sweep", {})
        sweep_detected = liq_sweep.get("detected", False)
        sweep_type = liq_sweep.get("type", "none")
        sweep_dir = "bullish" if "bullish" in sweep_type else "bearish" if "bearish" in sweep_type else "neutral"
        
        direction = "neutral"
        confidence = 0.0
        reasons = []
        
        if bullish_div:
            direction = "long"
            confidence = min(abs(cumulative_delta) / (avg_vol * 0.3), 0.80)
            reasons.append(f"bullish_divergence(cum_delta={cumulative_delta:.0f})")
            
            if vol_ratio > 1.5:
                confidence = min(confidence + 0.10, 0.90)
                reasons.append("high_vol_confirmation")
            if sweep_detected and sweep_dir == "bullish":
                confidence = min(confidence + 0.12, 0.95)
                reasons.append("liq_sweep_confirmed")
                
        elif large_rejection and rejection_direction == "long":
            direction = "long"
            confidence = 0.60
            reasons.append(f"rejection_candle({rejection_direction})")
            if vol_ratio > 1.5:
                confidence = min(confidence + 0.10, 0.85)
                reasons.append("high_vol")
        
        elif bearish_div:
            direction = "short"
            confidence = min(abs(cumulative_delta) / (avg_vol * 0.3), 0.80)
            reasons.append(f"bearish_divergence(cum_delta={cumulative_delta:.0f})")
            
            if vol_ratio > 1.5:
                confidence = min(confidence + 0.10, 0.90)
                reasons.append("high_vol_confirmation")
            if sweep_detected and sweep_dir == "bearish":
                confidence = min(confidence + 0.12, 0.95)
                reasons.append("liq_sweep_confirmed")
                
        elif large_rejection and rejection_direction == "short":
            direction = "short"
            confidence = 0.60
            reasons.append(f"rejection_candle({rejection_direction})")
            if vol_ratio > 1.5:
                confidence = min(confidence + 0.10, 0.85)
                reasons.append("high_vol")
        
        if direction == "neutral" and not reasons:
            reasons.append(f"cum_delta={cumulative_delta:.0f}_neutral")
        
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": "order_flow_delta",
            "reasons": reasons,
        }
