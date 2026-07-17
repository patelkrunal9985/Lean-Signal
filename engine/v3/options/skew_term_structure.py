from engine.v3.base import BaseV3Strategy


class SkewTermStructure(BaseV3Strategy):
    name = "skew_term_structure"
    description = "Skew term structure — front vs back month put/call IV skew"
    applies_to = ("option",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        skew = context.get("skew_term_1m", {})
        if not skew or not isinstance(skew, dict):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        front = skew.get("front_skew", 0)
        s2m = skew.get("skew_2m", 0)
        s3m = skew.get("skew_3m", 0)
        slope_2m = skew.get("term_slope_2m", 0)
        slope_3m = skew.get("term_slope_3m", 0)
        steep_front = skew.get("steep_front", False)
        # Front-month puts expensive but back-month cheap → fear now, mean reversion ahead
        if steep_front and slope_2m > 3.0:
            # Extreme front skew → buy back-month puts (cheap) or sell front-month skew
            confidence = min(slope_2m / 10.0, 0.75)
            return {"direction": "short", "confidence": round(confidence, 4), "action": "sell", "front_skew": front, "skew_2m": s2m, "slope_2m": slope_2m, "strategy": self.name}
        # Front-month calls expensive (negative skew flipped) but back-month normal
        if front < -2.0 and slope_2m < -2.0:
            confidence = min(abs(slope_2m) / 8.0, 0.65)
            return {"direction": "long", "confidence": round(confidence, 4), "action": "buy", "front_skew": front, "skew_2m": s2m, "slope_2m": slope_2m, "strategy": self.name}
        # Slope flattening: front-month fear is fading — direction follows front skew
        if steep_front and abs(slope_2m) < 1.0 and s2m != 0:
            confidence = 0.30
            return {"direction": "short" if front > 0 else "long", "confidence": round(confidence, 4), "action": "buy" if front < 0 else "sell", "front_skew": front, "skew_2m": s2m, "note": "flattening", "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
