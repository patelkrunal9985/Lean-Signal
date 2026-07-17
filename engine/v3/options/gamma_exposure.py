from datetime import datetime, date
import math
from engine.v3.base import BaseV3Strategy


def _black_scholes_gamma(S: float, K: float, t: float, sigma: float) -> float:
    if sigma <= 0 or t <= 0:
        return 0.0
    d1 = (math.log(S / K) + (0.5 * sigma ** 2) * t) / (sigma * math.sqrt(t))
    return math.exp(-d1 * d1 / 2) / (S * sigma * math.sqrt(2 * math.pi * t))


def _compute_max_pain(chain):
    strikes = {}
    for opt_type in ("calls", "puts"):
        for s in chain.get(opt_type, []):
            strike = s.get("strike", 0) or 0
            oi = s.get("openInterest", 0) or 0
            if strike > 0 and oi > 0:
                strikes[strike] = strikes.get(strike, 0) + oi
    if not strikes:
        return 0.0
    max_oi = max(strikes.values())
    if max_oi == 0:
        return 0.0
    return max(strike for strike, oi in strikes.items() if oi == max_oi)


class GammaExposure(BaseV3Strategy):
    name = "gamma_exposure"
    description = "Gamma exposure and dealer positioning analysis"
    applies_to = ("option",)
    default_weight = 0.12

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        expiry = context.get("expiry")
        if not expiry or underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        if isinstance(expiry, str):
            try:
                exp_clean = expiry.replace("-", "")
                exp_date = datetime.strptime(exp_clean, "%Y%m%d").date()
                dte = (exp_date - date.today()).days
            except Exception:
                dte = context.get("dte", 3)
        else:
            dte = context.get("dte", 3)
        if dte > 60 or dte < 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        t_years = dte / 365.0
        max_pain = _compute_max_pain(chain)
        call_gex = 0.0
        put_gex = 0.0
        total_oi = 0
        for strike_data in chain.get("calls", []):
            oi = strike_data.get("openInterest", 0) or 0
            if oi < 25:
                continue
            iv = (strike_data.get("impliedVolatility", 30) or 30) / 100.0
            stk = strike_data.get("strike", underlying) or underlying
            gamma = _black_scholes_gamma(underlying, stk, t_years, iv)
            call_gex += gamma * oi * underlying * 100
            total_oi += oi
        for strike_data in chain.get("puts", []):
            oi = strike_data.get("openInterest", 0) or 0
            if oi < 25:
                continue
            iv = (strike_data.get("impliedVolatility", 30) or 30) / 100.0
            stk = strike_data.get("strike", underlying) or underlying
            gamma = _black_scholes_gamma(underlying, stk, t_years, iv)
            put_gex += gamma * oi * underlying * 100
            total_oi += oi
        total_gamma_exposure = call_gex + put_gex
        net_gamma_imbalance = call_gex - put_gex
        confidence_base = min(total_gamma_exposure / 200000000, 0.75)
        if net_gamma_imbalance > 10000000:
            return {"direction": "short", "confidence": 0.40, "action": "sell", "total_gex": net_gamma_imbalance, "dte": dte, "strategy": self.name}
        elif net_gamma_imbalance < -10000000:
            direction = "long" if underlying > max_pain > 0 else "short"
            return {"direction": direction, "confidence": confidence_base, "action": "buy" if max_pain > 0 and underlying < max_pain else "sell", "total_gex": net_gamma_imbalance, "dte": dte, "max_pain": max_pain, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "total_gex": net_gamma_imbalance, "dte": dte, "strategy": self.name}
