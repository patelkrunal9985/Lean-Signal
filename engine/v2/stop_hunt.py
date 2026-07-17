
class StopHuntStrategy:
    def __init__(self):
        self.name = "stop_hunt"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])
        if not ohlcv or len(ohlcv) < 10:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["insufficient_data"]}
        liq = indicators.get("liquidity_sweep") or {}
        if not liq.get("detected", False):
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0, "strategy": self.name, "reasons": ["no_sweep_detected"]}
        liq_type = liq.get("type", "")
        liq_conf = liq.get("confidence", 0)
        swept_level = liq.get("swept_level", 0)
        base_conf = 0.50 + liq_conf * 0.40
        reasons = [f"sweep_{liq_type}_conf={liq_conf:.3f}"]
        if swept_level > 0:
            reasons.append(f"swept={swept_level}")
        iex_depth = None
        try:
            from engine.skills.ibkr_data_feed import get_market_depth
            iex_depth = get_market_depth(ticker)
        except Exception:
            pass
        if iex_depth:
            bids = iex_depth.get("bids", [])
            asks = iex_depth.get("asks", [])
            if bids and asks:
                bid_vol = sum(v for _, v in bids[:3])
                ask_vol = sum(v for _, v in asks[:3])
                total = bid_vol + ask_vol
                if total > 0:
                    bid_ratio = bid_vol / total
                    if "bullish" in liq_type and bid_ratio > 0.55:
                        base_conf = min(base_conf + 0.10, 0.92)
                        reasons.append(f"iex_bid_support={bid_ratio:.2f}")
                    elif "bearish" in liq_type and bid_ratio < 0.45:
                        base_conf = min(base_conf + 0.10, 0.92)
                        reasons.append(f"iex_ask_pressure={1-bid_ratio:.2f}")
                    else:
                        base_conf = max(base_conf - 0.05, 0.30)
                        reasons.append("iex_no_confirm")
        else:
            base_conf = max(base_conf - 0.08, 0.30)
            reasons.append("no_iex")
        direction = "long" if "bullish" in liq_type else "short" if "bearish" in liq_type else "neutral"
        confidence = base_conf if direction != "neutral" else 0.0
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
        }
