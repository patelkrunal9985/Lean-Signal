import numpy as np
from kronos.strategies.v3.base import BaseV3Strategy


class InsiderFlow(BaseV3Strategy):
    name = "insider_flow"
    description = "Cluster insider buying patterns"
    applies_to = ("stock",)
    default_weight = 0.05

    def _days_ago(self, date_str: str) -> int:
        from datetime import datetime
        try:
            d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            return (datetime.now() - d).days
        except Exception:
            return 999

    def compute(self, context: dict) -> dict:
        trades = context.get("insider_trades", [])
        recent = [t for t in trades if self._days_ago(t.get("date", "")) <= 30]
        if len(recent) < 3:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        buys = [t for t in recent if t.get("transaction_type") == "Buy"]
        sells = [t for t in recent if t.get("transaction_type") == "Sell"]
        unique_buyers = set(t.get("name", "") for t in buys)
        insider_cluster = len(unique_buyers) >= 3
        total_buy_value = sum(t.get("shares", 0) * t.get("price", 0) for t in buys)
        csuite_buys = sum(1 for t in buys if t.get("position", "") in ("CEO", "CFO", "COO", "CTO", "President"))
        director_buys = sum(1 for t in buys if "Director" in t.get("position", ""))
        net_buy_ratio = (len(buys) - len(sells)) / max(len(buys) + len(sells), 1)
        score = 0.0
        if insider_cluster:
            score += 30
        if csuite_buys >= 1:
            score += 20
        if director_buys >= 2:
            score += 15
        if net_buy_ratio > 0.5:
            score += 20
        if total_buy_value > 100000:
            score += 15
        confidence = min(score / 100.0, 1.0)
        if confidence >= 0.40 and net_buy_ratio > 0:
            return {"direction": "long", "confidence": confidence, "net_buy_ratio": net_buy_ratio, "insider_cluster": insider_cluster, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
