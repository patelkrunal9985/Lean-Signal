from engine.v3.base import BaseV3Strategy


class OrderImbalance(BaseV3Strategy):
    name = "order_imbalance"
    description = "L2 order book imbalance via IEX Depth of Book (OHLCV proxy fallback)"
    applies_to = ("future", "stock")
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")

        # ── Primary: read order_book_imbalance from context (already populated by _build_v3_context) ──
        # _build_v3_context fetches IEX depth ONCE per cycle and stores it in ctx["order_book_imbalance"].
        # Reading from context avoids redundant IBKR calls and respects the Semaphore(2) rate limit.
        ob = context.get("order_book_imbalance", {})
        if ob and ob.get("pressure", 0) > 0:
            ob_ratio = ob.get("imbalance_ratio", 0)
            ob_pressure = ob.get("pressure", 0)
            # Scale pressure to confidence: 0.05→0.15, 0.10→0.30, 0.30→0.90
            confidence = min(ob_pressure * 3.0, 0.90)
            if ob_ratio > 0.1:
                return {
                    "direction": "long", "confidence": round(confidence, 4),
                    "imbalance_ratio": ob_ratio, "source": "iex_depth",
                    "bid_volume": ob.get("bid_volume", 0),
                    "ask_volume": ob.get("ask_volume", 0),
                    "strategy": self.name,
                }
            elif ob_ratio < -0.1:
                return {
                    "direction": "short", "confidence": round(confidence, 4),
                    "imbalance_ratio": ob_ratio, "source": "iex_depth",
                    "bid_volume": ob.get("bid_volume", 0),
                    "ask_volume": ob.get("ask_volume", 0),
                    "strategy": self.name,
                }
            return {
                "direction": "neutral", "confidence": 0.0,
                "imbalance_ratio": ob_ratio, "source": "iex_depth",
                "strategy": self.name,
            }

        # ── Fallback: OHLCV-based proxy (when IBKR depth unavailable) ──
        candles = context.get("candles_1m", [])
        window = context.get("window", 10)
        imbalances = []
        for c in candles[-window:]:
            rng = c.get("high", 0) - c.get("low", 0)
            if rng == 0:
                continue
            close_pos = (c.get("close", 0) - c.get("low", 0)) / rng
            if close_pos > 0.75:
                imbalances.append(1)
            elif close_pos < 0.25:
                imbalances.append(-1)
            else:
                imbalances.append(0)
        net = sum(imbalances)
        total_agg = sum(abs(i) for i in imbalances)
        if total_agg == 0:
            return {
                "direction": "neutral", "confidence": 0.0,
                "imbalance_ratio": 0, "strategy": self.name,
            }
        ratio = net / total_agg
        confidence = abs(ratio)
        if ratio > 0.4:
            return {
                "direction": "long", "confidence": min(confidence, 0.75),
                "imbalance_ratio": ratio, "source": "ohlcv_proxy",
                "strategy": self.name,
            }
        elif ratio < -0.4:
            return {
                "direction": "short", "confidence": min(confidence, 0.75),
                "imbalance_ratio": ratio, "source": "ohlcv_proxy",
                "strategy": self.name,
            }
        return {
            "direction": "neutral", "confidence": 0.0,
            "imbalance_ratio": ratio, "source": "ohlcv_proxy",
            "strategy": self.name,
        }
