from engine.v3.base import BaseV3Strategy


class VIXSPXConvexityArbitrage(BaseV3Strategy):
    name = "vix_spx_convexity"
    description = "VIX/SPX convexity arbitrage — VIX futures vs SPX/SPY option pricing discrepancy"
    applies_to = ("option",)
    default_weight = 0.05

    def compute(self, context: dict) -> dict:
        vix_spot = context.get("vix_spot", 0)
        vix_1m = context.get("vix_1m", 0)
        vix_2m = context.get("vix_2m", 0)
        atm_iv = context.get("iv", 0)
        if vix_spot <= 0 or vix_1m <= 0 or atm_iv <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        underlying = context.get("underlying_price", 0)
        if underlying <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # On SPY: compare SPY ATM IV vs VIX futures to detect convexity mispricing
        # VIX futures reflect expected 30-day SPX volatility
        # SPY ATM IV should approximately match VIX front-month
        iv_vix_spread = atm_iv - vix_1m
        contango = vix_2m - vix_1m if vix_2m > 0 else 0
        # Strong contango + IV below VIX = options cheap relative to expected vol -> buy
        # Backwardation + IV above VIX = options expensive -> sell premium
        if abs(iv_vix_spread) < 3:  # No significant spread
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        spread_score = min(abs(iv_vix_spread) / 15, 0.40)
        term_score = min(abs(contango) / 10, 0.25)
        price_chg = context.get("price_change_1d", 0)
        momentum_align = (price_chg > 0 and iv_vix_spread > 0) or (price_chg < 0 and iv_vix_spread < 0)
        confidence = spread_score + term_score
        confidence = min(confidence, 0.70)
        if confidence < 0.15:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # IV below VIX + strong contango = buy vol (buy options, expect IV to catch up)
        if iv_vix_spread < -3 and contango > 3:
            direction = "long" if price_chg > 0 else "short"
            return {"direction": direction, "confidence": round(confidence, 4), "action": "buy", "iv_vix_spread": round(iv_vix_spread, 1), "contango": round(contango, 1), "strategy": self.name}
        # IV above VIX + backwardation = sell vol (sell premium) = bearish
        if iv_vix_spread > 5 and contango < 2:
            return {"direction": "short", "confidence": round(confidence, 4), "action": "sell", "iv_vix_spread": round(iv_vix_spread, 1), "contango": round(contango, 1), "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
