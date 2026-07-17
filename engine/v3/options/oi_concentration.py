import numpy as np
from engine.v3.base import BaseV3Strategy


class OIConcentration(BaseV3Strategy):
    name = "oi_concentration"
    description = "Open interest change concentration at specific strikes"
    applies_to = ("option",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        curr = context.get("chain_current", {})
        prev = context.get("chain_prev", {})
        total_dir = 0.0
        signals = []
        all_strikes = set()
        for opt_type in ["calls", "puts"]:
            for s in curr.get(opt_type, []):
                all_strikes.add(s.get("strike", 0) or 0)
        for strike in all_strikes:
            if not strike:
                continue
            for opt_type in ["calls", "puts"]:
                curr_oi = 0
                prev_oi = 0
                for s in curr.get(opt_type, []):
                    if s.get("strike") == strike:
                        curr_oi = s.get("openInterest", 0) or 0
                        break
                for s in prev.get(opt_type, []):
                    if s.get("strike") == strike:
                        prev_oi = s.get("openInterest", 0) or 0
                        break
                if prev_oi < 100:
                    continue
                chg_pct = (curr_oi - prev_oi) / prev_oi
                chg_abs = curr_oi - prev_oi
                if chg_pct > 0.5 and chg_abs > 1000:
                    total_dir += 1.0 if opt_type == "calls" else -1.0
                    signals.append({"strike": strike, "type": opt_type, "oi_change_pct": chg_pct, "oi_added": chg_abs})
        if not signals:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        net_score = total_dir / max(len(signals), 1)
        confidence = min(len(signals) / 10.0 * abs(net_score), 0.80)
        call_strikes = [s["strike"] for s in signals if s["type"] == "calls"]
        put_strikes = [s["strike"] for s in signals if s["type"] == "puts"]
        if call_strikes and put_strikes and np.mean(call_strikes) > np.mean(put_strikes):
            confidence = min(confidence * 1.2, 0.85)
        direction = "long" if net_score > 0 else "short"
        return {"direction": direction, "confidence": confidence, "action": "buy", "signals": signals, "net_score": net_score, "strategy": self.name}
