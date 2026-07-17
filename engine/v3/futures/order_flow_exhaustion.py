from engine.v3.base import BaseV3Strategy


class OrderFlowExhaustion(BaseV3Strategy):
    name = "order_flow_exhaustion"
    description = "Detects order flow exhaustion at key levels for high-probability reversals"
    applies_to = ("future",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        cum_delta = context.get("cumulative_delta", {})
        ohlcv = context.get("ohlcv", [])
        current_price = context.get("current_price", 0)
        profile = context.get("volume_profile_intraday", {})
        depth = context.get("order_book_imbalance", {})

        if not ohlcv or not cum_delta:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        if not current_price:
            current_price = ohlcv[-1].get("close", 0)

        vpin_val = cum_delta.get("vpin", 0) or 0
        cum_delta_val = cum_delta.get("cumulative_delta", 0)
        trade_imb = cum_delta.get("trade_imbalance", 0) or 0
        vol_imb = cum_delta.get("volume_imbalance", 0) or 0

        poc = profile.get("poc", 0)
        vah = profile.get("vah", 0)
        val = profile.get("val", 0)

        highs_20 = [c["high"] for c in ohlcv[-20:]]
        lows_20 = [c["low"] for c in ohlcv[-20:]]
        period_high = max(highs_20) if len(highs_20) >= 20 else 0
        period_low = min(lows_20) if len(lows_20) >= 20 else 0

        price_5ago = ohlcv[-6].get("close", 0) if len(ohlcv) >= 6 else ohlcv[0].get("close", 0)
        price_change = (current_price - price_5ago) / max(price_5ago, 0.01)
        cum_delta_raw = cum_delta.get("raw_bars", [])
        if cum_delta_raw and len(cum_delta_raw) >= 6:
            delta_5ago = cum_delta_raw[-6]
            delta_now = cum_delta_raw[-1]
            cum_delta_change = delta_now - delta_5ago
        else:
            cum_delta_change = cum_delta.get("last_10_delta", 0)

        high_level = vah if vah else period_high
        low_level = val if val else period_low

        at_high_level = high_level > 0 and current_price >= high_level * 0.995
        at_low_level = low_level > 0 and current_price <= low_level * 1.005
        at_key_level = at_high_level or at_low_level

        price_extreme_pct = 0.0
        if at_high_level and high_level > 0:
            price_extreme_pct = (current_price - high_level) / max(high_level, 0.01)
        elif at_low_level and low_level > 0:
            price_extreme_pct = (low_level - current_price) / max(low_level, 0.01)
        price_extreme_pct = max(price_extreme_pct, 0.0)

        delta_divergence_strength = 0.0
        is_bearish_div = False
        is_bullish_div = False
        if price_change > 0.001 and cum_delta_change <= 0:
            is_bearish_div = True
            delta_divergence_strength = min(abs(price_change) * 15.0, 0.35)
        elif price_change < -0.001 and cum_delta_change >= 0:
            is_bullish_div = True
            delta_divergence_strength = min(abs(price_change) * 15.0, 0.35)

        if not is_bearish_div and not is_bullish_div:
            if price_change > 0 and cum_delta_change < abs(price_change) * 0.3:
                is_bearish_div = True
                delta_divergence_strength = min(abs(price_change) * 8.0, 0.20)
            elif price_change < 0 and cum_delta_change > abs(price_change) * 0.3:
                is_bullish_div = True
                delta_divergence_strength = min(abs(price_change) * 8.0, 0.20)

        vpin_toxicity = max(min(vpin_val - 0.50, 0.20), 0.0)

        trade_imb_score = 0.0
        if is_bearish_div and trade_imb < -0.15:
            trade_imb_score = min(abs(trade_imb) * 0.30, 0.15)
        elif is_bullish_div and trade_imb > 0.15:
            trade_imb_score = min(abs(trade_imb) * 0.30, 0.15)
        elif vol_imb:
            if is_bearish_div and vol_imb < -0.15:
                trade_imb_score = min(abs(vol_imb) * 0.25, 0.12)
            elif is_bullish_div and vol_imb > 0.15:
                trade_imb_score = min(abs(vol_imb) * 0.25, 0.12)

        price_score = min(price_extreme_pct * 2.0, 0.30)

        vpin_confirms = vpin_val > 0.55

        raw_confidence = price_score + delta_divergence_strength + vpin_toxicity + trade_imb_score
        confidence = min(raw_confidence, 0.85)

        direction = "neutral"
        exhaustion_type = None
        if confidence >= 0.20 and vpin_confirms and at_key_level:
            if is_bullish_div:
                direction = "long"
                exhaustion_type = "bullish"
            elif is_bearish_div:
                direction = "short"
                exhaustion_type = "bearish"

        return {
            "direction": direction,
            "confidence": round(confidence, 4),
            "exhaustion_type": exhaustion_type,
            "price_extreme_pct": round(price_extreme_pct, 4),
            "delta_divergence_strength": round(delta_divergence_strength, 4),
            "vpin_toxicity": round(vpin_toxicity, 4),
            "at_key_level": at_key_level,
            "price_change": round(price_change, 6),
            "cum_delta_change": round(cum_delta_change, 4),
            "vpin": round(vpin_val, 4),
            "trade_imbalance": round(trade_imb, 4),
            "strategy": self.name,
        }
