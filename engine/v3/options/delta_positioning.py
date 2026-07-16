from kronos.strategies.v3.base import BaseV3Strategy


class DeltaPositioning(BaseV3Strategy):
    name = "delta_positioning"
    description = "Delta-weighted OI positioning — aggregated dealer hedging delta"
    applies_to = ("option",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        dp = context.get("delta_positioning", {})
        if not dp or not isinstance(dp, dict):
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        net_delta = dp.get("net_delta", 0)
        delta_ratio = dp.get("delta_ratio", 1.0)
        call_delta = dp.get("call_delta", 0)
        put_delta = dp.get("put_delta", 0)
        total = call_delta + abs(put_delta)
        # Check if OI data came from yfinance (EOD, stale) - apply 0.7x penalty
        oi_penalty = 1.0
        chain = context.get("option_chain", {})
        for side in (chain.get("calls", []), chain.get("puts", [])):
            for rec in side:
                if rec.get("oi_source", "") == "yfinance_eod":
                    oi_penalty = 0.70
                    break
            if oi_penalty < 1.0:
                break
        if total < 500000:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        if delta_ratio > 3.0 and net_delta > 1000000:
            confidence = min((delta_ratio - 3.0) / 5.0, 0.70) * oi_penalty
            return {"direction": "short", "confidence": confidence, "action": "sell", "delta_ratio": delta_ratio, "net_delta": net_delta, "strategy": self.name}
        elif delta_ratio < 0.5 and net_delta < -1000000:
            confidence = min((0.5 - delta_ratio) / 0.5, 0.80) * oi_penalty
            return {"direction": "long", "confidence": confidence, "action": "buy", "delta_ratio": delta_ratio, "net_delta": net_delta, "strategy": self.name}
        # Moderate imbalance: confidence scales with delta_ratio distance from 1.0
        dev = abs(delta_ratio - 1.0)
        if dev > 0.5 and total > 1000000:
            confidence = min(dev * 0.15, 0.40) * oi_penalty
            direction = "short" if delta_ratio > 1.0 else "long"
            return {"direction": direction, "confidence": confidence, "action": "sell" if direction == "short" else "buy", "delta_ratio": delta_ratio, "net_delta": net_delta, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
