from kronos.strategies.v3.base import BaseV3Strategy


class CallPutWallBreakout(BaseV3Strategy):
    name = "call_put_wall_breakout"
    description = "OI wall breakout — price breaking through large gamma/oi walls as support/resistance"
    applies_to = ("option",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        gamma_walls = context.get("gamma_walls", [])
        if not gamma_walls:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        underlying = context.get("underlying_price", 0)
        if underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        price_chg = context.get("price_change_1d", 0)
        # Find walls near current price (within 2%)
        nearby_walls = [w for w in gamma_walls if abs((w.get("strike", 0) or 0) - underlying) / underlying < 0.02]
        if not nearby_walls:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Check if OI data came from yfinance (EOD, stale) - apply 0.7x penalty
        oi_penalty = 1.0
        chain = context.get("option_chain", {})
        for side in (chain.get("calls", []), chain.get("puts", [])):
            for rec in side:
                if rec.get("oi_source", "") == "yfinance_eod":
                    oi_penalty = 0.70
                    break
            if oi_penalty < 1.0:
                break
        # Find nearest wall above and below
        wall_above = min((w for w in nearby_walls if (w.get("strike", 0) or 0) > underlying), key=lambda w: w.get("strike", 0) or 0, default=None)
        wall_below = max((w for w in nearby_walls if (w.get("strike", 0) or 0) < underlying), key=lambda w: w.get("strike", 0) or 0, default=None)
        # Breaking above a call wall (gamma resistance) = bullish breakout
        if wall_above and price_chg > 0.005:
            dist_pct = (underlying - wall_above["strike"]) / underlying
            if -0.005 < dist_pct < 0.01:  # just crossed or about to cross
                gex_magnitude = wall_above.get("gex_per_1pct", 0)
                confidence = min(gex_magnitude / 200000, 0.70) * oi_penalty
                if confidence > 0.15:
                    return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "wall_strike": wall_above["strike"], "wall_type": "call", "strategy": self.name}
        # Breaking below a put wall (gamma support) = bearish breakdown
        if wall_below and price_chg < -0.005:
            dist_pct = (wall_below["strike"] - underlying) / underlying
            if -0.005 < dist_pct < 0.01:
                gex_magnitude = wall_below.get("gex_per_1pct", 0)
                confidence = min(gex_magnitude / 200000, 0.70) * oi_penalty
                if confidence > 0.15:
                    return {"direction": "short", "confidence": round(confidence, 4), "action": "buy", "wall_strike": wall_below["strike"], "wall_type": "put", "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
