from kronos.strategies.v3.base import BaseV3Strategy


class GammaFlipAcceleration(BaseV3Strategy):
    name = "gamma_flip_acceleration"
    description = "Gamma flip acceleration — velocity-based approach to zero-gamma level (not static distance)"
    applies_to = ("option",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        gamma_flip = context.get("gamma_flip_level", 0)
        underlying = context.get("underlying_price", 0)
        if gamma_flip <= 0 or underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        dist_pct = abs(underlying - gamma_flip) / underlying
        if dist_pct > 0.03:  # Too far from flip level
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        price_chg = context.get("price_change_1d", 0)
        daily_range = context.get("daily_range", 0)
        if daily_range <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Velocity: how fast price is moving (normalized by daily range)
        velocity = abs(price_chg) / (daily_range / underlying) if daily_range > 0 else 0
        moving_toward = (underlying > gamma_flip and price_chg < 0) or (underlying < gamma_flip and price_chg > 0)
        if not moving_toward:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Proximity + velocity = acceleration signal
        prox_score = max(0, (0.03 - dist_pct) / 0.03) * 0.40  # closer = higher score
        vel_score = min(velocity * 0.25, 0.35)                  # faster = more urgency
        delta_mag = abs(context.get("delta_positioning", {}).get("net_delta", 0))
        # Check OI source - apply 0.7x penalty for yfinance EOD OI
        oi_penalty = 1.0
        chain = context.get("option_chain", {})
        for side in (chain.get("calls", []), chain.get("puts", [])):
            for rec in side:
                if rec.get("oi_source", "") == "yfinance_eod":
                    oi_penalty = 0.70
                    break
            if oi_penalty < 1.0:
                break
        delta_score = min(delta_mag / 5000000, 0.15)            # larger delta = more hedging
        confidence = (prox_score + vel_score + delta_score) * oi_penalty
        confidence = min(confidence, 0.80)
        if confidence < 0.15:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Direction: speed toward flip causes hedging acceleration
        # Approaching from below (price < flip) -> dealers buy as gamma flips -> bullish
        # Approaching from above (price > flip) -> dealers sell as gamma flips -> bearish
        if underlying < gamma_flip:
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "flip_level": gamma_flip, "dist_pct": round(dist_pct, 4), "velocity": round(velocity, 2), "strategy": self.name}
        else:
            return {"direction": "short", "confidence": round(confidence, 4), "action": "buy", "flip_level": gamma_flip, "dist_pct": round(dist_pct, 4), "velocity": round(velocity, 2), "strategy": self.name}
