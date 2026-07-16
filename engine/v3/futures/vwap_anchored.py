from kronos.strategies.v3.base import BaseV3Strategy


class VWAPAnchored(BaseV3Strategy):
    name = "vwap_anchored"
    description = "Multi-anchored VWAP deviation with order book confirmation — fade to VWAP"
    applies_to = ("future",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        vwap_data = context.get("intraday_vwap", {})
        ohlcv = context.get("ohlcv", [])
        ticker = context.get("ticker", "")
        current_price = context.get("current_price", 0)
        if not vwap_data and len(ohlcv) < 5:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        vwap = vwap_data.get("vwap", 0)
        if vwap <= 0 and len(ohlcv) >= 5:
            vwap = self._compute_intraday_vwap(ohlcv)
        if vwap <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        if current_price <= 0 and ohlcv:
            current_price = ohlcv[-1]["close"]
        if current_price <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        deviation = (current_price - vwap) / vwap
        depth_dir = "neutral"
        depth_imb = 0.0
        if ticker:
            try:
                from kronos.skills.ibkr_data_feed import get_market_depth
                depth = get_market_depth(ticker)
                if depth:
                    bids = depth.get("bids", [])
                    asks = depth.get("asks", [])
                    if bids and asks:
                        bid_vol = sum(b[1] for b in bids[:5])
                        ask_vol = sum(a[1] for a in asks[:5])
                        total = bid_vol + ask_vol
                        if total > 0:
                            depth_imb = (bid_vol - ask_vol) / total
                        if depth_imb > 0.2:
                            depth_dir = "bullish"
                        elif depth_imb < -0.2:
                            depth_dir = "bearish"
            except Exception:
                depth_dir = context.get("order_book_imbalance", {}).get("direction", "neutral")
                depth_imb = context.get("order_book_imbalance", {}).get("imbalance_ratio", 0.0)
        cum_delta_sig = "neutral"
        cum_delta = context.get("cumulative_delta", {})
        if cum_delta:
            cdd = cum_delta.get("cumulative_delta", 0)
            if cdd > 500:
                cum_delta_sig = "bullish"
            elif cdd < -500:
                cum_delta_sig = "bearish"
        long_conf = 0.0
        short_conf = 0.0
        if deviation < -0.005 and depth_dir == "bullish":
            strength = min(abs(deviation) * 100, 1.0)
            if cum_delta_sig == "bullish":
                strength = min(strength * 1.25, 1.0)
            long_conf = strength * 0.55
        elif deviation < -0.005 and cum_delta_sig == "bullish":
            long_conf = min(abs(deviation) * 80, 1.0) * 0.40
        if deviation > 0.005 and depth_dir == "bearish":
            strength = min(deviation * 100, 1.0)
            if cum_delta_sig == "bearish":
                strength = min(strength * 1.25, 1.0)
            short_conf = strength * 0.55
        elif deviation > 0.005 and cum_delta_sig == "bearish":
            short_conf = min(deviation * 80, 1.0) * 0.40
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "vwap": round(vwap, 2), "deviation_pct": round(deviation * 100, 4),
                    "depth_direction": depth_dir, "cum_delta_signal": cum_delta_sig,
                    "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "vwap": round(vwap, 2), "deviation_pct": round(deviation * 100, 4),
                    "depth_direction": depth_dir, "cum_delta_signal": cum_delta_sig,
                    "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "vwap": round(vwap, 2), "deviation_pct": round(deviation * 100, 4),
                "depth_direction": depth_dir, "cum_delta_signal": cum_delta_sig,
                "strategy": self.name}

    @staticmethod
    def _compute_intraday_vwap(candles: list) -> float:
        if not candles:
            return 0.0
        cum_pv = 0.0
        cum_vol = 0.0
        for c in candles:
            high = c.get("high", 0)
            low = c.get("low", 0)
            close = c.get("close", 0)
            vol = c.get("volume", 0)
            if vol <= 0:
                continue
            typical = (high + low + close) / 3.0
            cum_pv += typical * vol
            cum_vol += vol
        return cum_pv / max(cum_vol, 1) if cum_vol > 0 else 0.0
