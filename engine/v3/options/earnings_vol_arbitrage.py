from kronos.strategies.v3.base import BaseV3Strategy


class EarningsVolArbitrage(BaseV3Strategy):
    name = "earnings_vol_arbitrage"
    description = "Earnings vol edge — buy cheap vol, sell expensive vol around events"
    applies_to = ("option",)
    default_weight = 0.15

    def compute(self, context: dict) -> dict:
        earnings = context.get("earnings", {})
        if not earnings or not isinstance(earnings, dict):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        has_earnings_today = earnings.get("has_earnings_today", False)
        surprise_pct = earnings.get("surprise_pct", 0)
        straddle = context.get("atm_straddle_price", 0)
        underlying = context.get("underlying_price", 0)
        iv = context.get("iv", 0)
        hv = context.get("hv_10", 0)
        if underlying <= 0 or straddle <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        implied_move_pct = (straddle * 0.8) / underlying  # expected move %
        # Historical earnings move proxy: use HV * sqrt(1/252) as baseline
        hist_move_pct = (hv / 100.0) / 15.87 if hv > 0 else 0.01  # HV / sqrt(252)
        if not has_earnings_today:
            # Pre-earnings: check if vol is cheap or expensive relative to HV
            if iv > 0 and hv > 0:
                vol_ratio = iv / max(hv, 0.01)
                if vol_ratio > 1.5:
                    # Vol is too expensive → sell premium before event
                    confidence = min((vol_ratio - 1.5) * 0.5, 0.70)
                    return {"direction": "short", "confidence": round(confidence, 4), "action": "sell", "vol_ratio": round(vol_ratio, 2), "iv": iv, "hv": hv, "implied_move_pct": round(implied_move_pct, 4), "note": "pre_earnings_expensive", "strategy": self.name}
                elif vol_ratio < 0.7:
                    # Vol is cheap → buy premium before event
                    confidence = min((0.7 - vol_ratio) * 0.8, 0.60)
                    return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "vol_ratio": round(vol_ratio, 2), "iv": iv, "hv": hv, "implied_move_pct": round(implied_move_pct, 4), "note": "pre_earnings_cheap", "strategy": self.name}
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # === Post-earnings: vol crush signal ===
        # After earnings, IV should crush. If straddle still implies >2x HV, sell vol.
        if implied_move_pct > hist_move_pct * 2.0 and hist_move_pct > 0:
            confidence = min(implied_move_pct / hist_move_pct * 0.3, 0.75)
            return {"direction": "short", "confidence": round(confidence, 4), "action": "sell", "implied_move_pct": round(implied_move_pct, 4), "hist_move_pct": round(hist_move_pct, 4), "surprise_pct": surprise_pct, "note": "post_earnings_vol_crush", "strategy": self.name}
        # Surprise-based: big surprise → direction, small surprise → reversion
        if abs(surprise_pct) > 5.0:
            direction = "long" if surprise_pct > 0 else "short"
            confidence = min(abs(surprise_pct) * 0.05, 0.50)
            return {"direction": direction, "confidence": round(confidence, 4), "action": "buy" if surprise_pct > 0 else "sell", "surprise_pct": surprise_pct, "note": "earnings_surprise_direction", "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
