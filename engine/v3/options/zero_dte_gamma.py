from kronos.strategies.v3.base import BaseV3Strategy


class ZeroDTEGamma(BaseV3Strategy):
    name = "zero_dte_gamma"
    description = "0DTE gamma scalping — exploits extreme gamma acceleration on expiry day for intraday momentum trades"
    applies_to = ("option",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        dte = context.get("dte", 5)
        if dte != 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        if underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # Find ATM strike: closest to underlying price
        calls = chain.get("calls", [])
        if not calls:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        atm_call = min(calls, key=lambda c: abs((c.get("strike", 0) or 0) - underlying))
        atm_strike = atm_call.get("strike", 0) or 0
        atm_gamma = atm_call.get("gamma", 0) or 0
        atm_iv_pct = atm_call.get("impliedVolatility", context.get("iv", 30)) or 0
        if atm_gamma < 0.005:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        price_chg = context.get("price_change_1d", 0)
        vix = context.get("vix_spot", 0)
        daily_range = context.get("daily_range", 0)
        range_pct = (daily_range / underlying) if underlying > 0 else 0
        gamma_score = min(atm_gamma * 40, 0.35)
        iv_premium = min(atm_iv_pct / 60, 0.20)
        vix_env = min(vix / 25, 0.15)
        momentum_score = min(abs(price_chg) * 3, 0.20)
        range_score = min(range_pct / 0.02, 0.10)
        confidence = gamma_score + iv_premium + vix_env + momentum_score + range_score
        confidence = min(confidence, 0.85)
        if price_chg > 0.003:
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "gamma": round(atm_gamma, 6), "dte": 0, "strategy": self.name}
        elif price_chg < -0.003:
            return {"direction": "short", "confidence": round(confidence, 4), "action": "buy", "gamma": round(atm_gamma, 6), "dte": 0, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "gamma": round(atm_gamma, 6), "dte": 0, "strategy": self.name}
