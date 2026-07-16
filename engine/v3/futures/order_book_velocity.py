from kronos.strategies.v3.base import BaseV3Strategy


class OrderBookVelocity(BaseV3Strategy):
    name = "order_book_velocity"
    description = "Order book momentum — bid volume acceleration vs price position (IEX depth primary, OHLCV proxy fallback)"
    applies_to = ("future",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")

        # BUG #4 fix: use depth from context (centralized in modeling_agent._build_v3_context)
        # instead of re-importing get_market_depth(). The same IBKR call was made to populate
        # ctx["order_book_depth"]; using context avoids a redundant (even if cached) call.
        # Falls back to direct call only if context data missing (e.g., from _debug endpoint).
        if ticker:
            depth = context.get("order_book_depth") or {}
            if not depth.get("bids") or not depth.get("asks"):
                # Fallback: direct call only when context didn't pre-populate
                try:
                    from kronos.skills.ibkr_data_feed import get_market_depth
                    depth = get_market_depth(ticker)
                except Exception:
                    depth = {}
            if depth and depth.get("bids") and depth.get("asks"):
                    bids = depth["bids"]
                    asks = depth["asks"]
                    bid_vol = sum(b[1] for b in bids[:5])
                    ask_vol = sum(a[1] for a in asks[:5])
                    total = bid_vol + ask_vol
                    if total > 0:
                        pressure = (bid_vol - ask_vol) / total
                        velocity = 0.0
                        mid = (bids[0][0] + asks[0][0]) / 2.0
                        ohlcv = context.get("ohlcv", [])
                        price_position = 0.5
                        if ohlcv:
                            recent = ohlcv[-10:]
                            lo = min(c["low"] for c in recent)
                            hi = max(c["high"] for c in recent)
                            rng = hi - lo
                            if rng > 0:
                                price_position = (mid - lo) / rng
                        if pressure > 0.05 and price_position < 0.5:
                            velocity = pressure * (1.0 - price_position)
                            confidence = min(velocity * 2.0, 0.85)
                            return {
                                "direction": "long", "confidence": round(confidence, 4),
                                "pressure": round(pressure, 4),
                                "velocity": round(velocity, 4),
                                "price_position": round(price_position, 4),
                                "source": "iex_depth",
                                "strategy": self.name,
                            }
                        elif pressure < -0.05 and price_position > 0.5:
                            velocity = abs(pressure) * price_position
                            confidence = min(velocity * 2.0, 0.85)
                            return {
                                "direction": "short", "confidence": round(confidence, 4),
                                "pressure": round(pressure, 4),
                                "velocity": round(velocity, 4),
                                "price_position": round(price_position, 4),
                                "source": "iex_depth",
                                "strategy": self.name,
                            }
                        return {
                            "direction": "neutral", "confidence": 0.0,
                            "pressure": round(pressure, 4),
                            "velocity": 0.0,
                            "price_position": round(price_position, 4),
                            "source": "iex_depth",
                            "strategy": self.name,
                        }

        # ── Fallback: OHLCV-based proxy (close position in 10-bar range + volume) ──
        candles = context.get("ohlcv", [])
        if len(candles) < 11:
            return {
                "direction": "neutral", "confidence": 0.0,
                "pressure": 0, "velocity": 0, "price_position": 0.5,
                "strategy": self.name,
            }
        recent = candles[-11:-1]
        curr = candles[-1]
        lo = min(c["low"] for c in recent)
        hi = max(c["high"] for c in recent)
        rng = hi - lo
        close_pos = ((curr.get("close", 0) - lo) / rng) if rng > 0 else 0.5
        vol = curr.get("volume", 0)
        avg_vol = sum(c.get("volume", 0) for c in recent) / 10
        vol_conf = min(vol / max(avg_vol, 1), 2.0) / 2.0
        if close_pos > 0.75 and vol_conf > 0.6:
            return {
                "direction": "long", "confidence": round(vol_conf * 0.60, 4),
                "pressure": round(close_pos - 0.5, 4),
                "velocity": round(vol_conf, 4),
                "price_position": round(close_pos, 4),
                "source": "ohlcv_proxy",
                "strategy": self.name,
            }
        elif close_pos < 0.25 and vol_conf > 0.6:
            return {
                "direction": "short", "confidence": round(vol_conf * 0.60, 4),
                "pressure": round(close_pos - 0.5, 4),
                "velocity": round(vol_conf, 4),
                "price_position": round(close_pos, 4),
                "source": "ohlcv_proxy",
                "strategy": self.name,
            }
        return {
            "direction": "neutral", "confidence": 0.0,
            "pressure": round(close_pos - 0.5, 4),
            "velocity": 0.0,
            "price_position": round(close_pos, 4),
            "source": "ohlcv_proxy",
            "strategy": self.name,
        }
