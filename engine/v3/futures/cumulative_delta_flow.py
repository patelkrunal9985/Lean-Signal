from kronos.strategies.v3.base import BaseV3Strategy


class CumulativeDeltaFlow(BaseV3Strategy):
    name = "cumulative_delta_flow"
    description = "Cumulative delta order flow from real IBKR tick tape — Lee-Ready signed trades for day-trade direction on ES/NQ"
    applies_to = ("future",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        cd = context.get("cumulative_delta", {})
        if not cd or not isinstance(cd, dict):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        cum_delta = cd.get("cumulative_delta", 0) or 0
        delta_60s = cd.get("delta_60s", 0) or 0
        total_buy = cd.get("total_buy_vol", 0) or 0
        total_sell = cd.get("total_sell_vol", 0) or 0
        buy_count = cd.get("buy_count", 0) or 0
        sell_count = cd.get("sell_count", 0) or 0
        avg_trade_size = cd.get("avg_trade_size", 0) or 0
        trade_imb = cd.get("trade_imbalance", 0) or 0
        vol_imb = cd.get("volume_imbalance", 0) or 0
        vpin_val = cd.get("vpin", 0) or 0
        last_price = cd.get("last_price", 0) or 0
        last_bid = cd.get("last_bid", 0) or 0
        last_ask = cd.get("last_ask", 0) or 0

        total_vol = total_buy + total_sell
        if total_vol < 100:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        delta_strength = abs(cum_delta) / max(total_vol, 1)
        velo_60s = abs(delta_60s) / max(total_vol, 1)
        quote_spread = (last_ask - last_bid) / max(last_price, 0.01) if last_ask > last_bid > 0 else 0

        long_conf = 0.0
        short_conf = 0.0

        base_conf = min(delta_strength * 2.0, 0.30)
        velo_bonus = min(velo_60s * 3.0, 0.15)
        trade_imb_bonus = abs(trade_imb) * 0.15
        vpin_penalty = vpin_val * 0.15

        if cum_delta > 0 and vol_imb > 0.05:
            long_conf = min(base_conf + velo_bonus + trade_imb_bonus - vpin_penalty, 0.75)
            if delta_60s > 0:
                long_conf = min(long_conf + 0.10, 0.80)

        if cum_delta < 0 and vol_imb < -0.05:
            short_conf = min(base_conf + velo_bonus + trade_imb_bonus - vpin_penalty, 0.75)
            if delta_60s < 0:
                short_conf = min(short_conf + 0.10, 0.80)

        if vpin_val > 0.6 and abs(trade_imb) > 0.3:
            if trade_imb > 0:
                short_conf = min(short_conf + (vpin_val - 0.5) * 0.5, 0.85)
            else:
                long_conf = min(long_conf + (vpin_val - 0.5) * 0.5, 0.85)

        quote_bonus = 0.0
        if quote_spread > 0.001 and quote_spread < 0.005:
            quote_bonus = 0.05

        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf + quote_bonus, 4),
                    "cum_delta": cum_delta, "delta_60s": delta_60s,
                    "delta_strength": round(delta_strength, 4),
                    "trade_imb": round(trade_imb, 4),
                    "vol_imb": round(vol_imb, 4),
                    "vpin": round(vpin_val, 4), "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf + quote_bonus, 4),
                    "cum_delta": cum_delta, "delta_60s": delta_60s,
                    "delta_strength": round(delta_strength, 4),
                    "trade_imb": round(trade_imb, 4),
                    "vol_imb": round(vol_imb, 4),
                    "vpin": round(vpin_val, 4), "strategy": self.name}

        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
