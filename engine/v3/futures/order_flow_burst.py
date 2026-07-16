from kronos.strategies.v3.base import BaseV3Strategy


class OrderFlowBurst(BaseV3Strategy):
    name = "order_flow_burst"
    description = "Tick accelerations and iceberg clusters — micro-structure flow bursts"
    applies_to = ("future",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        tick_clusters = context.get("tick_clusters", {})
        if not tick_clusters:
            # ── Fallback: use 1-min candles as proxy for tick burst detection ──
            return self._compute_from_candles(context)
        accel = tick_clusters.get("acceleration", {})
        large_prints = tick_clusters.get("large_prints", [])
        icebergs = tick_clusters.get("icebergs", [])
        acceleration = accel.get("acceleration", 0)
        tick_count = tick_clusters.get("tick_count", 0)
        if tick_count < 20:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        long_conf = 0.0
        short_conf = 0.0
        total_large_buy = sum(p.get("size", 0) for pl in large_prints
                              for p in (pl.get("large_buy_prints", [])))
        total_large_sell = sum(p.get("size", 0) for pl in large_prints
                               for p in (pl.get("large_sell_prints", [])))
        if acceleration > 0.05 and accel.get("accelerating_up"):
            strength = min(acceleration / 0.2, 1.0)
            if total_large_buy > total_large_sell:
                strength = min(strength * 1.2, 1.0)
            long_conf = strength * 0.55
        if acceleration < -0.05 and accel.get("accelerating_down"):
            strength = min(abs(acceleration) / 0.2, 1.0)
            if total_large_sell > total_large_buy:
                strength = min(strength * 1.2, 1.0)
            short_conf = strength * 0.55
        buy_icebergs = sum(1 for ib in icebergs if ib.get("is_buying"))
        sell_icebergs = sum(1 for ib in icebergs if not ib.get("is_buying"))
        if buy_icebergs > sell_icebergs and long_conf == 0:
            long_conf = min(buy_icebergs * 0.08, 0.35)
        elif sell_icebergs > buy_icebergs and short_conf == 0:
            short_conf = min(sell_icebergs * 0.08, 0.35)
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "acceleration": round(acceleration, 4),
                    "large_buy": total_large_buy, "large_sell": total_large_sell,
                    "buy_icebergs": buy_icebergs, "sell_icebergs": sell_icebergs,
                    "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "acceleration": round(acceleration, 4),
                    "large_buy": total_large_buy, "large_sell": total_large_sell,
                    "buy_icebergs": buy_icebergs, "sell_icebergs": sell_icebergs,
                    "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "acceleration": round(acceleration, 4), "strategy": self.name}

    def _compute_from_candles(self, context: dict) -> dict:
        """Fallback: use 1-min candles as proxy for tick burst detection
        when tick_clusters data is unavailable (IBKR disconnected)."""
        candles_1m = context.get("candles_1m", [])
        ohlcv = context.get("ohlcv", [])
        if len(candles_1m) < 10:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Compute price acceleration from 1-min candles
        prices = [c["close"] for c in candles_1m]
        halflen = len(prices) // 2
        first_half = prices[:halflen]
        second_half = prices[halflen:]
        first_change = (first_half[-1] - first_half[0]) / max(first_half[0], 0.01) if first_half else 0
        second_change = (second_half[-1] - second_half[0]) / max(second_half[0], 0.01) if second_half else 0
        acceleration = second_change - first_change
        # Volume burst detection from candles
        vols = [c.get("volume", 0) for c in candles_1m[-10:]]
        avg_vol = sum(vols) / max(len(vols), 1) if vols else 0
        last_vols = [c.get("volume", 0) for c in candles_1m[-3:]]
        vol_burst = max(last_vols) > avg_vol * 2.0 if avg_vol > 0 else False
        long_conf = 0.0
        short_conf = 0.0
        # Acceleration-based signals
        if acceleration > 0.002 and second_change > 0:
            strength = min(abs(acceleration) / 0.01, 1.0)
            if vol_burst:
                strength = min(strength * 1.2, 1.0)
            long_conf = strength * 0.45
        if acceleration < -0.002 and second_change < 0:
            strength = min(abs(acceleration) / 0.01, 1.0)
            if vol_burst:
                strength = min(strength * 1.2, 1.0)
            short_conf = strength * 0.45
        # Price surge backup: rapid move in last few candles
        if not long_conf and not short_conf and len(ohlcv) >= 5:
            last_close = ohlcv[-1]["close"]
            prev_close = ohlcv[-3]["close"]
            chg_pct = (last_close - prev_close) / max(prev_close, 0.01)
            if chg_pct > 0.005:
                long_conf = min(abs(chg_pct) * 10, 0.30)
            elif chg_pct < -0.005:
                short_conf = min(abs(chg_pct) * 10, 0.30)
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "source": "candles_fallback", "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "source": "candles_fallback", "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "source": "candles_fallback", "strategy": self.name}
