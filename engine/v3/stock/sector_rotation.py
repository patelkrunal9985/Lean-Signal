from kronos.strategies.v3.base import BaseV3Strategy


class SectorRotation(BaseV3Strategy):
    name = "sector_rotation"
    description = "Relative strength vs sector ETF over multiple windows"
    applies_to = ("stock",)
    default_weight = 0.10

    def _percentile_rank(self, value: float, scores: list[float]) -> float:
        if not scores:
            return 0.5
        count = sum(1 for s in scores if s <= value)
        return count / len(scores)

    def compute(self, context: dict) -> dict:
        ticker_ret = context.get("ticker_returns", {})
        sector_ret = context.get("sector_returns", {})
        sector_scores = context.get("sector_rs_scores", [])
        rs = {}
        for tf in ["1h", "1d", "5d"]:
            rs[tf] = ticker_ret.get(tf, 0) - sector_ret.get(tf, 0)
        composite_rs = rs.get("1h", 0) * 0.5 + rs.get("1d", 0) * 0.3 + rs.get("5d", 0) * 0.2
        rs_scores_flat = [s.get("rs_raw", 0) for s in sector_scores if isinstance(s, dict)]
        percentile = self._percentile_rank(composite_rs, rs_scores_flat)
        if percentile > 0.90:
            direction = "long"
            confidence = min(0.50 + (percentile - 0.90) * 2.5, 0.90)
        elif percentile < 0.10:
            direction = "short"
            confidence = min(0.50 + (0.10 - percentile) * 2.5, 0.90)
        else:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        if direction == "long" and composite_rs < 0.02:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        if direction == "short" and composite_rs > -0.02:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        return {"direction": direction, "confidence": confidence, "percentile": percentile, "composite_rs": composite_rs, "strategy": self.name}
