from kronos.strategies.v3.base import BaseV3Strategy


class DeltaDivergence(BaseV3Strategy):
    name = "delta_divergence"
    description = "Price vs cumulative delta divergence — classic reversal detection on ES/NQ"
    applies_to = ("future",)
    default_weight = 0.09

    def compute(self, context: dict) -> dict:
        cum_delta = context.get("cumulative_delta", {})
        ohlcv = context.get("ohlcv", [])
        depth = context.get("order_book_imbalance", {})

        if not cum_delta and len(ohlcv) < 6:
            return {
                "direction": "neutral", "confidence": 0.0,
                "price_change": 0, "cum_delta": 0,
                "divergence_type": "none", "depth_direction": "neutral",
                "strategy": self.name,
            }

        cum_delta_val = cum_delta.get("cumulative_delta", 0) if cum_delta else 0
        last_10 = cum_delta.get("last_10_delta", 0) if cum_delta else 0
        depth_dir = depth.get("direction", "neutral") if depth else "neutral"

        # Compute price change over last 5 bars from OHLCV
        price_5ago = ohlcv[-6].get("close", 0) if len(ohlcv) >= 6 else 0
        price_now = ohlcv[-1].get("close", 0) if ohlcv else 0
        price_change = ((price_now - price_5ago) / price_5ago) if price_5ago > 0 else 0

        # Compute cumulative delta change stats from raw context if available
        # WARNING FIX: read raw_bars from cum_delta dict (populated by compute_cumulative_delta)
        # instead of the missing root-level context key. Cleaner nested access.
        cum_delta_raw = cum_delta.get("raw_bars", []) if cum_delta else []
        if cum_delta_raw and ohlcv:
            old_price = ohlcv[-6].get("close", 0) if len(ohlcv) >= 6 else 0
            new_price = ohlcv[-1].get("close", 0) if ohlcv else 0
            price_change = ((new_price - old_price) / old_price) if old_price > 0 else 0
            delta_5ago = cum_delta_raw[-6] if len(cum_delta_raw) >= 6 else cum_delta_raw[0]
            delta_now = cum_delta_raw[-1] if cum_delta_raw else 0
            cum_delta_change = delta_now - delta_5ago
        else:
            cum_delta_change = last_10

        # Check for tick clusters confirmation
        tick_clusters = context.get("tick_clusters", [])
        cluster_confirms = 0
        if tick_clusters and len(tick_clusters) >= 2:
            cluster_confirms = sum(
                1 for tc in tick_clusters[-2:]
                if isinstance(tc, dict) and tc.get("size", 0) > 0
            )

        # Bearish divergence: price made new high but cum_delta flat/negative
        if price_change > 0.002 and cum_delta_change <= 0:
            confidence = min(abs(price_change) * 20.0, 0.80)
            if cluster_confirms:
                confidence = min(confidence + 0.10, 0.90)
            return {
                "direction": "short", "confidence": round(confidence, 4),
                "price_change": round(price_change, 4),
                "cum_delta": round(cum_delta_change, 4),
                "divergence_type": "bearish",
                "depth_direction": depth_dir,
                "strategy": self.name,
            }

        # Bullish divergence: price made new low but cum_delta rising
        if price_change < -0.002 and cum_delta_change >= 0:
            confidence = min(abs(price_change) * 20.0, 0.80)
            if cluster_confirms:
                confidence = min(confidence + 0.10, 0.90)
            return {
                "direction": "long", "confidence": round(confidence, 4),
                "price_change": round(price_change, 4),
                "cum_delta": round(cum_delta_change, 4),
                "divergence_type": "bullish",
                "depth_direction": depth_dir,
                "strategy": self.name,
            }

        # Weaker signal: price moved but cum_delta not confirming
        if abs(price_change) > 0.005 and abs(cum_delta_change) < abs(price_change) * 0.3:
            weak_conf = min(abs(price_change) * 10.0, 0.40)
            if price_change > 0:
                return {
                    "direction": "short", "confidence": round(weak_conf, 4),
                    "price_change": round(price_change, 4),
                    "cum_delta": round(cum_delta_change, 4),
                    "divergence_type": "weak_bearish",
                    "depth_direction": depth_dir,
                    "strategy": self.name,
                }
            return {
                "direction": "long", "confidence": round(weak_conf, 4),
                "price_change": round(price_change, 4),
                "cum_delta": round(cum_delta_change, 4),
                "divergence_type": "weak_bullish",
                "depth_direction": depth_dir,
                "strategy": self.name,
            }

        return {
            "direction": "neutral", "confidence": 0.0,
            "price_change": round(price_change, 4),
            "cum_delta": round(cum_delta_change, 4),
            "divergence_type": "none",
            "depth_direction": depth_dir,
            "strategy": self.name,
        }
