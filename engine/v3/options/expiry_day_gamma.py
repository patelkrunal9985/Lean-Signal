from engine.v3.base import BaseV3Strategy


class ExpiryDayGamma(BaseV3Strategy):
    name = "expiry_day_gamma"
    description = "0DTE and 1DTE specific — trades gamma pin, max pain attraction, and volatility collapse"
    applies_to = ("option",)
    default_weight = 0.12

    def compute(self, context: dict) -> dict:
        dte = context.get("dte", 5)
        if dte > 1:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        max_pain = context.get("max_pain_strike", 0)
        gamma_flip = context.get("gamma_flip_level", 0)
        gamma_walls = context.get("gamma_walls", [])
        price_change = context.get("price_change_1d", 0) or 0
        daily_range = context.get("daily_range", 0) or 0
        vix = context.get("vix_spot", 0) or 0
        breadth = context.get("market_breadth", {})

        if underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        vix_implied_range = (vix / 12.0) * underlying / 100.0 if vix > 0 else daily_range * 2
        daily_range_pct = daily_range / underlying if underlying > 0 else 0
        range_vs_vix = daily_range / max(vix_implied_range, 0.01)

        sub_signals = []
        max_conf = 0.0
        best_dir = "neutral"

        if max_pain > 0:
            dist_to_mp = abs(underlying - max_pain) / underlying
            if dist_to_mp < 0.005:
                mp_conf = (1.0 - dist_to_mp / 0.005) * 0.40
                mp_dir = "long" if underlying < max_pain else "short"
                sub_signals.append("max_pain_pin")
                if mp_conf > max_conf:
                    max_conf = mp_conf
                    best_dir = mp_dir

        if gamma_walls:
            nearest_wall = None
            nearest_wall_dist = float("inf")
            for w in gamma_walls:
                wk = w.get("strike", 0) or 0
                if wk > 0:
                    w_dist = abs(underlying - wk) / underlying
                    if w_dist < nearest_wall_dist:
                        nearest_wall_dist = w_dist
                        nearest_wall = wk
            if nearest_wall is not None and nearest_wall_dist < 0.003:
                wall_dir = "long" if underlying < nearest_wall else "short"
                sub_signals.append("gamma_wall_attraction")
                wall_conf = 0.35
                if wall_conf > max_conf:
                    max_conf = wall_conf
                    best_dir = wall_dir

        comp = breadth.get("composite", 0) if isinstance(breadth, dict) else 0
        comp_score = comp.get("composite_score", 0) if isinstance(comp, dict) else comp
        if daily_range_pct < 0.006 and range_vs_vix < 0.60 and -0.3 <= comp_score <= 0.3:
            sub_signals.append("range_bound_pin")
            pin_conf = 0.25
            dir_for_pin = "neutral"
            if max_conf < pin_conf:
                max_conf = pin_conf
                best_dir = dir_for_pin

        if len(sub_signals) >= 3 and best_dir != "neutral":
            max_conf *= 1.3

        max_conf = min(max_conf, 0.85)
        if best_dir != "neutral":
            max_conf = max(max_conf, 0.15)

        if max_conf < 0.15:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        return {
            "direction": best_dir,
            "confidence": round(max_conf, 4),
            "strategy": self.name,
            "action": "buy" if best_dir != "neutral" else "hold",
            "dte": dte,
            "max_pain_distance": round(abs(underlying - max_pain) / underlying, 6) if max_pain > 0 else None,
            "nearest_wall": nearest_wall if gamma_walls else None,
            "nearest_wall_dist": round(nearest_wall_dist, 6) if gamma_walls and nearest_wall is not None else None,
            "pinning_sub_signals": sub_signals,
            "daily_range_vs_vix": round(range_vs_vix, 4),
        }
