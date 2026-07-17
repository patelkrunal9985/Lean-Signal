from engine.v3.base import BaseV3Strategy


class EarningsMomentum(BaseV3Strategy):
    name = "earnings_momentum"
    description = "Post-earnings announcement drift"
    applies_to = ("stock",)
    default_weight = 0.09

    def compute(self, context: dict) -> dict:
        e = context.get("earnings", {})
        eps_surprise = e.get("eps_surprise_pct", 0)
        rev_surprise = e.get("revenue_surprise_pct", 0)
        days_since = e.get("days_since_earnings", 100)
        gap_up = e.get("gap_up_pct", 0)
        earnings_vol = e.get("volume_ratio_earnings_day", 1.0)
        if eps_surprise < 5.0 or days_since > 30:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        time_decay = max(0.0, 1.0 - days_since / 30.0)
        score = 0.0
        score += min(eps_surprise / 20.0, 1.0) * 30
        if rev_surprise >= 2.0:
            score += min(rev_surprise / 10.0, 1.0) * 20
        score += time_decay * 25
        if gap_up >= 1.0:
            score += min(gap_up / 5.0, 1.0) * 15
        if earnings_vol >= 1.5:
            score += min(earnings_vol / 5.0, 1.0) * 10
        confidence = min(score / 100.0, 1.0)
        if confidence >= 0.40 and eps_surprise > 0:
            return {"direction": "long", "confidence": confidence, "eps_surprise": eps_surprise, "days_since": days_since, "strategy": self.name}
        elif confidence >= 0.40:
            return {"direction": "short", "confidence": confidence * 0.8, "eps_surprise": eps_surprise, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
