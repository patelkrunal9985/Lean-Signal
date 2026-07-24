from engine.v3.base import BaseV3Strategy


class GammaFlipLevels(BaseV3Strategy):
    name = "gamma_flip_levels"
    description = "Zero gamma flip level, dealer gamma wall detection, and GEX price magnet projection"
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

        # ── GEX Price Magnet: the strike with highest gamma×OI concentration ──
        gex_magnet = self._compute_gex_magnet(walls, underlying)

        if dist_pct < 0:
            confidence = min(abs_dist * 20 + wall_bonus, 0.75)
            return {
                "direction": "long", "confidence": round(confidence, 4),
                "action": "buy", "gamma_flip": flip,
                "distance_pct": round(dist_pct, 4), "near_wall": wall_bonus > 0,
                "gex_magnet": gex_magnet, "strategy": self.name,
            }
        else:
            confidence = min(abs_dist * 15 + wall_bonus, 0.65)
            return {
                "direction": "short", "confidence": round(confidence, 4),
                "action": "sell", "gamma_flip": flip,
                "distance_pct": round(dist_pct, 4), "near_wall": wall_bonus > 0,
                "gex_magnet": gex_magnet, "strategy": self.name,
            }

    def _compute_gex_magnet(self, walls: list, underlying: float) -> dict | None:
        """Compute the GEX price magnet — the strike with the highest gamma×OI.

        This is the level that dealer positioning is most likely to pull price toward.

        Returns {"strike": float, "direction": "up"|"down", "strength": float, "distance_pct": float}
        or None if no walls available.
        """
        if not walls or underlying <= 0:
            return None

        above_walls = []
        below_walls = []
        for w in walls:
            k = w.get("strike", 0) or 0
            if k <= 0:
                continue
            gamma = float(w.get("gamma", w.get("net_gamma", 0)) or 0)
            oi = float(w.get("openInterest", w.get("oi", 0)) or 0)
            gamma_oi = abs(gamma) * oi
            if gamma_oi <= 0:
                continue
            if k > underlying:
                above_walls.append((k, gamma_oi))
            else:
                below_walls.append((k, gamma_oi))

        # Find strongest wall above and below
        strongest_above = max(above_walls, key=lambda x: x[1]) if above_walls else None
        strongest_below = max(below_walls, key=lambda x: x[1]) if below_walls else None

        if strongest_above and strongest_below:
            if strongest_above[1] >= strongest_below[1]:
                direction = "up"
                strike = strongest_above[0]
                strength = strongest_above[1]
            else:
                direction = "down"
                strike = strongest_below[0]
                strength = strongest_below[1]
        elif strongest_above:
            direction = "up"
            strike, strength = strongest_above
        elif strongest_below:
            direction = "down"
            strike, strength = strongest_below
        else:
            return None

        dist_pct = abs(underlying - strike) / underlying
        return {
            "strike": round(strike, 2),
            "direction": direction,
            "strength": round(strength, 2),
            "distance_pct": round(dist_pct * 100, 2),
        }
