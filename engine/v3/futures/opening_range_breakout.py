from engine.v3.base import BaseV3Strategy


class OpeningRangeBreakout(BaseV3Strategy):
    name = "opening_range_breakout"
    description = "First 15-min opening range breakout with IEX depth confirmation on ES/NQ"
    applies_to = ("future",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        candles_1m = context.get("candles_1m", [])
        if not candles_1m or len(candles_1m) < 15:
            return {
                "direction": "neutral", "confidence": 0.0,
                "or_high": 0, "or_low": 0, "breakout_pct": 0,
                "depth_direction": "neutral",
                "strategy": self.name,
            }

        time_profile = context.get("time_of_day_profile", {})
        session = time_profile.get("session", "rth") if time_profile else "rth"
        minutes_from_open = time_profile.get("minutes_from_open", 60) if time_profile else 60

        # Only trade during RTH and within first 30 minutes
        if session != "rth" or minutes_from_open >= 30:
            return {
                "direction": "neutral", "confidence": 0.0,
                "or_high": 0, "or_low": 0, "breakout_pct": 0,
                "depth_direction": "neutral",
                "strategy": self.name,
            }

        # Compute opening range from first 15 1m candles
        or_candles = candles_1m[:15]
        or_high = max(c["high"] for c in or_candles)
        or_low = min(c["low"] for c in or_candles)
        or_mid = (or_high + or_low) / 2.0

        # If we don't have a full 15-min range yet, store levels but no trade
        if minutes_from_open < 15:
            return {
                "direction": "prebuild", "confidence": 0.0,
                "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                "breakout_pct": 0, "depth_direction": "neutral",
                "strategy": self.name,
            }

        current_price = candles_1m[-1].get("close", or_mid)

        # IEX depth confirmation
        depth_direction = "neutral"
        ticker = context.get("ticker", "")
        if ticker:
            try:
                from engine.skills.ibkr_data_feed import get_market_depth
                depth = get_market_depth(ticker)
                if depth and depth.get("bids") and depth.get("asks"):
                    bids = depth["bids"]
                    asks = depth["asks"]
                    bid_vol = sum(b[1] for b in bids[:3])
                    ask_vol = sum(a[1] for a in asks[:3])
                    if (bid_vol + ask_vol) > 0:
                        imb = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                        if imb > 0.1:
                            depth_direction = "bullish"
                        elif imb < -0.1:
                            depth_direction = "bearish"
            except Exception:
                pass

        # Also try to get depth from context
        if depth_direction == "neutral":
            ob_imb = context.get("order_book_imbalance", {})
            if ob_imb:
                ob_dir = ob_imb.get("direction", "neutral")
                depth_direction = "bullish" if ob_dir in ("bullish", "long") else "bearish" if ob_dir in ("bearish", "short") else "neutral"

        # Buy breakout: price above OR high + depth confirms
        if current_price > or_high:
            breakout_pct = ((current_price - or_high) / or_high) * 100.0
            if depth_direction == "bullish":
                confidence = min(0.30 + breakout_pct * 0.05, 0.85)
            else:
                confidence = min(0.15 + breakout_pct * 0.03, 0.50)
            return {
                "direction": "long", "confidence": round(confidence, 4),
                "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                "breakout_pct": round(breakout_pct, 4),
                "depth_direction": depth_direction,
                "strategy": self.name,
            }

        # Sell breakdown: price below OR low + depth confirms
        if current_price < or_low:
            breakout_pct = ((or_low - current_price) / or_low) * 100.0
            if depth_direction == "bearish":
                confidence = min(0.30 + breakout_pct * 0.05, 0.85)
            else:
                confidence = min(0.15 + breakout_pct * 0.03, 0.50)
            return {
                "direction": "short", "confidence": round(confidence, 4),
                "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                "breakout_pct": round(breakout_pct, 4),
                "depth_direction": depth_direction,
                "strategy": self.name,
            }

        return {
            "direction": "neutral", "confidence": 0.0,
            "or_high": round(or_high, 2), "or_low": round(or_low, 2),
            "breakout_pct": 0, "depth_direction": depth_direction,
            "strategy": self.name,
        }
