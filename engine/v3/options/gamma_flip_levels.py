from engine.v3.base import BaseV3Strategy


class GammaFlipLevels(BaseV3Strategy):
    name = "gamma_flip_levels"
    description = "Zero gamma flip level and dealer gamma wall detection"
    applies_to = ("option",)
    default_weight = 0.12

    def compute(self, context: dict) -> dict:
        flip = context.get("gamma_flip_level", 0)
        underlying = context.get("underlying_price", 0)
        walls = context.get("gamma_walls", [])
        if underlying <= 0 or flip == 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        dist_pct = (underlying - flip) / underlying
        abs_dist = abs(dist_pct)
        # Gamma wall proximity bonus: if near a wall, confidence gets a boost
        wall_bonus = 0.0
        if walls:
            for w in walls:
                w_k = w.get("strike", 0) or 0
                if w_k > 0:
                    w_dist = abs(underlying - w_k) / underlying
                    if w_dist < 0.01:
                        wall_bonus = min(wall_bonus + 0.10, 0.30)
        if dist_pct < 0:
            # Price is below gamma flip — dealers are long gamma
            # They buy dips, sell rips → range-bound bullish below flip
            confidence = min(abs_dist * 20 + wall_bonus, 0.75)
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "gamma_flip": flip, "distance_pct": round(dist_pct, 4), "near_wall": wall_bonus > 0, "strategy": self.name}
        else:
            # Price is above gamma flip — dealers are short gamma
            # They chase price → momentum above, but vulnerable to snap-back
            confidence = min(abs_dist * 15 + wall_bonus, 0.65)
            return {"direction": "short", "confidence": round(confidence, 4), "action": "sell", "gamma_flip": flip, "distance_pct": round(dist_pct, 4), "near_wall": wall_bonus > 0, "strategy": self.name}
