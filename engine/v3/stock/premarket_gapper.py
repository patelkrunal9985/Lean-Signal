from engine.v3.base import BaseV3Strategy


class PremarketGapper(BaseV3Strategy):
    name = "premarket_gapper"
    description = "Gap fade/continuation based on pre-market volume and price action"
    applies_to = ("stock",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        prem = context.get("premarket", {})
        gap_pct = prem.get("gap_pct", 0)
        prem_vol = prem.get("premarket_volume", 0)
        avg_vol = prem.get("avg_premarket_volume", 1)
        vol_ratio = prem_vol / max(avg_vol, 1)
        if abs(gap_pct) < 0.005:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        # IBKR scanner confirmation: check if ticker appears in top gainers/losers
        scanner_boost = 0.0
        ticker_name = context.get("ticker", "")
        if ticker_name and gap_pct != 0:
            if gap_pct > 0:
                gainers = context.get("scanner_top_gainers", {})
                if isinstance(gainers, dict) and ticker_name in gainers:
                    scanner_boost = 0.10  # real-time scanner confirms the gap up
            else:
                losers = context.get("scanner_top_losers", {})
                if isinstance(losers, dict) and ticker_name in losers:
                    scanner_boost = 0.10  # real-time scanner confirms the gap down
        if abs(gap_pct) >= 0.015:
            if vol_ratio > 2.0:
                confidence = min(vol_ratio / 5.0 + scanner_boost, 0.95)
                return {"direction": "long" if gap_pct > 0 else "short", "confidence": confidence, "gap_pct": gap_pct, "strategy": self.name}
            else:
                confidence = min(abs(gap_pct) * 5 + scanner_boost, 0.70)
                return {"direction": "short" if gap_pct > 0 else "long", "confidence": confidence, "gap_pct": gap_pct, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
