from kronos.strategies.v3.base import BaseV3Strategy


class ZeroDTEGamma(BaseV3Strategy):
    name = "zero_dte_gamma"
    description = "0DTE gamma scalping — exploits extreme gamma acceleration on expiry day with cumulative delta confirmation"
    applies_to = ("option",)
    default_weight = 0.12

    def compute(self, context: dict) -> dict:
        dte = context.get("dte", 5)
        if dte != 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        if underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        calls = chain.get("calls", [])
        puts = chain.get("puts", [])
        if not calls:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        atm_call = min(calls, key=lambda c: abs((c.get("strike", 0) or 0) - underlying))
        atm_strike = atm_call.get("strike", 0) or 0
        atm_gamma = atm_call.get("gamma", 0) or 0
        atm_iv_pct = atm_call.get("impliedVolatility", context.get("iv", 30)) or 0

        if atm_iv_pct > 200:
            atm_iv_pct = context.get("iv", 30)

        if atm_gamma < 0.005:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        price_chg = context.get("price_change_1d", 0) or 0
        vix = context.get("vix_spot", 0) or 0
        daily_range = context.get("daily_range", 0) or 0
        range_pct = (daily_range / underlying) if underlying > 0 else 0

        # Cumulative delta confirmation from real IBKR tick tape (Lee-Ready signed)
        cum_delta_data = context.get("cumulative_delta", {})
        cd_val = cum_delta_data.get("cumulative_delta", 0) if cum_delta_data else 0
        delta_60s = cum_delta_data.get("delta_60s", 0) if cum_delta_data else 0
        trade_imb = cum_delta_data.get("trade_imbalance", 0) if cum_delta_data else 0
        vol_imb = cum_delta_data.get("volume_imbalance", 0) if cum_delta_data else 0
        vpin_val = cum_delta_data.get("vpin", 0) if cum_delta_data else 0
        total_buy = cum_delta_data.get("total_buy_vol", 0) if cum_delta_data else 0
        total_sell = cum_delta_data.get("total_sell_vol", 0) if cum_delta_data else 0

        gamma_score = min(atm_gamma * 50, 0.40)
        iv_premium = min(atm_iv_pct / 50, 0.25)
        vix_env = min(vix / 20, 0.15)
        momentum_score = min(abs(price_chg) * 4, 0.25)
        range_score = min(range_pct / 0.015, 0.12)

        delta_bonus = 0.0
        total_vol = total_buy + total_sell
        if total_vol > 0:
            delta_strength = abs(cd_val) / max(total_vol, 1)
            # Cumulative delta agreeing with price direction = strong confirmation
            if price_chg > 0 and cd_val > 0 and delta_60s > 0:
                delta_bonus = min(delta_strength * 0.10, 0.20)
            elif price_chg < 0 and cd_val < 0 and delta_60s < 0:
                delta_bonus = min(delta_strength * 0.10, 0.20)
            # Divergence = reversal signal (price up, delta down)
            elif price_chg > 0 and cd_val < 0 and delta_strength > 0.3:
                delta_bonus = min(delta_strength * 0.15, 0.15)
                price_chg = -abs(price_chg)
            elif price_chg < 0 and cd_val > 0 and delta_strength > 0.3:
                delta_bonus = min(delta_strength * 0.15, 0.15)
                price_chg = abs(price_chg)
        # VPIN toxicity bonus — if order flow sense agrees with gamma-straddle direction
        if vpin_val > 0.6 and abs(trade_imb) > 0.3:
            if (trade_imb > 0 and cd_val < 0 and price_chg < 0) or (trade_imb < 0 and cd_val > 0 and price_chg > 0):
                delta_bonus = min(delta_bonus + (vpin_val - 0.5) * 0.20, 0.25)

        confidence = gamma_score + iv_premium + vix_env + momentum_score + range_score + delta_bonus
        confidence = min(confidence, 0.90)

        if price_chg > 0.002:
            return {"direction": "long", "confidence": round(confidence, 4),
                    "action": "buy", "gamma": round(atm_gamma, 6),
                    "delta_confirmed": bool(delta_bonus > 0),
                    "dte": 0, "strategy": self.name}
        elif price_chg < -0.002:
            return {"direction": "short", "confidence": round(confidence, 4),
                    "action": "buy", "gamma": round(atm_gamma, 6),
                    "delta_confirmed": bool(delta_bonus > 0),
                    "dte": 0, "strategy": self.name}

        return {"direction": "neutral", "confidence": 0.0,
                "gamma": round(atm_gamma, 6), "dte": 0, "strategy": self.name}
