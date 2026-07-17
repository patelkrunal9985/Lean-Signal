from engine.v3.base import BaseV3Strategy


class DarkPoolProxy(BaseV3Strategy):
    name = "dark_pool_proxy"
    description = "Institutional positioning via IEX Depth of Book (volume clustering fallback)"
    applies_to = ("stock",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        # ── Primary: IEX Depth of Book as institutional positioning proxy ──
        # Heavy one-sided order book imbalance indicates institutional
        # accumulation or distribution — the closest proxy to dark pool
        # activity available on public exchanges.
        # Populated once per cycle by _build_v3_context (no redundant IBKR calls).
        ob = context.get("order_book_imbalance", {})
        if ob and ob.get("pressure", 0) > 0.03:
            ob_ratio = ob.get("imbalance_ratio", 0)
            ob_pressure = ob.get("pressure", 0)
            # Scale pressure to confidence: 0.05→0.15, 0.15→0.45, 0.30→0.85
            confidence = min(ob_pressure * 3.0, 0.85)
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

        # ── Fallback: volume clustering + tick analysis (no IBKR depth) ──
        candles = context.get("candles_1m", [])
        tod_profile = context.get("time_of_day_profile", {})
        if not candles:
            return {
                "direction": "neutral", "confidence": 0.0,
                "source": "volume_clustering", "strategy": self.name,
            }

        last = candles[-1]
        dt = last.get("datetime", last.get("time", 0))
        if hasattr(dt, "hour"):
            minute = dt.hour * 60 + dt.minute
        else:
            minute = 0
        profile = tod_profile.get(minute, {"avg_vol": 1, "avg_spread": 0.01})
        vol = last.get("volume", 0)
        avg_vol = max(profile.get("avg_vol", 1), 1)
        vol_ratio = vol / avg_vol
        high = last.get("high", 0)
        low = last.get("low", 0)
        close = last.get("close", 0)
        spread = (high - low) / max(close, 0.01) if close > 0 else 0
        avg_spread = max(profile.get("avg_spread", 0.0001), 0.0001)
        spread_ratio = spread / avg_spread
        dp_score = vol_ratio / max(spread_ratio, 0.1)
        close_pos = (close - low) / max(high - low, 0.001) if high > low else 0.5
        direction = "long" if close_pos > 0.6 else "short" if close_pos < 0.4 else "neutral"

        block_signal = False
        if len(candles) >= 3:
            recent = candles[-3:]
            all_high_vol = all(
                c.get("volume", 0) > profile["avg_vol"] * 1.5
                for c in recent
            )
            same_dir = all(
                (c.get("close", 0) > c.get("open", 0))
                == (recent[0].get("close", 0) > recent[0].get("open", 0))
                for c in recent
            )
            block_signal = all_high_vol and same_dir

        # Tick-by-tick data: confirm block trades with real trade sizes
        ticks = context.get("ticks", [])
        tick_block_count = 0
        if ticks:
            for t in ticks[-50:]:
                if t.get("size", 0) >= 500:
                    tick_block_count += 1
        if tick_block_count >= 3:
            block_signal = True

        if dp_score > 2.0 and block_signal:
            return {
                "direction": direction,
                "confidence": min(dp_score / 5.0, 0.85),
                "dp_score": dp_score, "source": "volume_clustering",
                "strategy": self.name,
            }
        elif dp_score > 1.5:
            return {
                "direction": direction,
                "confidence": min(dp_score / 4.0, 0.70),
                "dp_score": dp_score, "source": "volume_clustering",
                "strategy": self.name,
            }
        return {
            "direction": "neutral", "confidence": 0.0,
            "dp_score": dp_score, "source": "volume_clustering",
            "strategy": self.name,
        }
