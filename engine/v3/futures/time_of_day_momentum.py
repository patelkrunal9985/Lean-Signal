from engine.v3.base import BaseV3Strategy


class TimeOfDayMomentum(BaseV3Strategy):
    name = "time_of_day_momentum"
    description = "Session-based momentum trading — Globex fade, opening breakout, midday reversion, power hour continuation"
    applies_to = ("future",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        session_data = context.get("time_of_day_profile", {})
        candles_1m = context.get("candles_1m", [])
        ohlcv = context.get("ohlcv", [])
        depth = context.get("order_book_imbalance", {})
        vwap = context.get("intraday_vwap", 0)
        current_price = context.get("current_price", 0)

        session = session_data.get("session", "rth")
        minutes_from_open = session_data.get("minutes_from_open", 0)

        if not candles_1m and not ohlcv:
            return {"direction": "neutral", "confidence": 0.0,
                    "session": session, "minutes_from_open": minutes_from_open,
                    "momentum_direction": "neutral", "candles_per_hour": 0,
                    "strategy": self.name}

        candles = candles_1m if len(candles_1m) >= 5 else ohlcv[-5:] if len(ohlcv) >= 5 else []
        if not candles:
            return {"direction": "neutral", "confidence": 0.0,
                    "session": session, "minutes_from_open": minutes_from_open,
                    "momentum_direction": "neutral", "candles_per_hour": 0,
                    "strategy": self.name}

        candles_per_hour = len(candles_1m) if candles_1m else 0
        recent_5 = candles[-5:]
        last_3 = candles[-3:] if len(candles) >= 3 else candles

        avg_range_5 = sum(c.get("high", 0) - c.get("low", 0) for c in recent_5) / max(len(recent_5), 1)

        long_conf = 0.0
        short_conf = 0.0
        momentum_direction = "neutral"

        if session == "globex":
            avg_globex_range = session_data.get("avg_globex_range", 0)
            if avg_globex_range > 0:
                stretch_ratio = avg_range_5 / avg_globex_range
                if stretch_ratio > 0.3:
                    globex_open = session_data.get("globex_open", 0)
                    if globex_open and current_price:
                        if current_price > globex_open:
                            short_conf = min((current_price - globex_open) / max(avg_globex_range, 0.01) * 0.5, 0.75)
                            momentum_direction = "bearish"
                        else:
                            long_conf = min((globex_open - current_price) / max(avg_globex_range, 0.01) * 0.5, 0.75)
                            momentum_direction = "bullish"

        elif session == "rth" and minutes_from_open < 30:
            open_price = session_data.get("rth_open", 0)
            if open_price and current_price:
                move_pct = (current_price - open_price) / max(open_price, 0.01)
                last_3_range = sum(c.get("high", 0) - c.get("low", 0) for c in last_3) / max(len(last_3), 1)
                if move_pct > 0.001 and last_3_range > 0:
                    strength = min((current_price - last_3[-1].get("open", current_price)) / last_3_range * 2, 1.0)
                    long_conf = strength * 0.65
                    momentum_direction = "bullish"
                elif move_pct < -0.001 and last_3_range > 0:
                    strength = min((last_3[-1].get("open", current_price) - current_price) / last_3_range * 2, 1.0)
                    short_conf = strength * 0.65
                    momentum_direction = "bearish"

        elif session == "rth" and 30 <= minutes_from_open <= 150:
            if current_price and vwap and len(recent_5) >= 3:
                vwap_pct = (current_price - vwap) / max(vwap, 0.01)
                if vwap_pct > 0.003:
                    last_wick = recent_5[-1].get("high", current_price) - max(recent_5[-1].get("close", current_price), recent_5[-1].get("open", current_price))
                    candle_range = recent_5[-1].get("high", 0) - recent_5[-1].get("low", 0)
                    if candle_range > 0 and last_wick / candle_range > 0.5:
                        short_conf = min(vwap_pct * 100, 0.60)
                        momentum_direction = "bearish"
                elif vwap_pct < -0.003:
                    last_wick = min(recent_5[-1].get("close", current_price), recent_5[-1].get("open", current_price)) - recent_5[-1].get("low", current_price)
                    candle_range = recent_5[-1].get("high", 0) - recent_5[-1].get("low", 0)
                    if candle_range > 0 and last_wick / candle_range > 0.5:
                        long_conf = min(abs(vwap_pct) * 100, 0.60)
                        momentum_direction = "bullish"

        elif session == "rth" and minutes_from_open > 330:
            if current_price and vwap:
                vwap_pct = (current_price - vwap) / max(vwap, 0.01)
                if vwap_pct > 0 and len(recent_5) >= 3:
                    rising = all(recent_5[i].get("close", 0) > recent_5[i-1].get("close", 0) for i in range(-2, 0))
                    if rising:
                        strength = min(vwap_pct * 100, 1.0)
                        long_conf = strength * 0.55
                        momentum_direction = "bullish"
                elif vwap_pct < 0 and len(recent_5) >= 3:
                    falling = all(recent_5[i].get("close", 0) < recent_5[i-1].get("close", 0) for i in range(-2, 0))
                    if falling:
                        strength = min(abs(vwap_pct) * 100, 1.0)
                        short_conf = strength * 0.55
                        momentum_direction = "bearish"

        if depth:
            depth_dir = depth.get("direction", "neutral")
            if depth_dir == "bullish" and long_conf > 0:
                long_conf = min(long_conf * 1.3, 0.90)
            elif depth_dir == "bearish" and short_conf > 0:
                short_conf = min(short_conf * 1.3, 0.90)

        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "session": session, "minutes_from_open": minutes_from_open,
                    "momentum_direction": momentum_direction,
                    "candles_per_hour": candles_per_hour, "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "session": session, "minutes_from_open": minutes_from_open,
                    "momentum_direction": momentum_direction,
                    "candles_per_hour": candles_per_hour, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "session": session, "minutes_from_open": minutes_from_open,
                "momentum_direction": momentum_direction,
                "candles_per_hour": candles_per_hour, "strategy": self.name}
