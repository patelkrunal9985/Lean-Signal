from kronos.strategies.v3.base import BaseV3Strategy


class LargeOptionFlow(BaseV3Strategy):
    name = "large_option_flow"
    description = "Large option flow / block trade detection via OI change"
    applies_to = ("option",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        curr = context.get("chain_current", {})
        prev = context.get("chain_prev", {})
        threshold = context.get("premium_threshold", 1000000.0)
        flow = []
        all_strikes = set()
        for opt_type in ["calls", "puts"]:
            for s in curr.get(opt_type, []):
                all_strikes.add(s.get("strike", 0) or 0)
        for strike in all_strikes:
            if not strike:
                continue
            for opt_type in ["calls", "puts"]:
                curr_data = None
                prev_data = None
                for s in curr.get(opt_type, []):
                    if s.get("strike") == strike:
                        curr_data = s
                        break
                for s in prev.get(opt_type, []):
                    if s.get("strike") == strike:
                        prev_data = s
                        break
                if not curr_data:
                    continue
                oi_curr = curr_data.get("openInterest", 0) or 0
                oi_prev = (prev_data.get("openInterest", 0) or 0) if prev_data else 0
                oi_chg = oi_curr - oi_prev
                mid = ((curr_data.get("bid", 0) or 0) + (curr_data.get("ask", 0) or 0)) / 2.0
                if oi_chg > 500 and mid > 0:
                    premium = oi_chg * 100 * mid
                    if premium > threshold:
                        flow.append({"strike": strike, "type": opt_type, "oi_change": oi_chg, "premium": premium})
        if not flow:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        bullish_prem = sum(f["premium"] for f in flow if f["type"] == "calls")
        bearish_prem = sum(f["premium"] for f in flow if f["type"] == "puts")
        total = bullish_prem + bearish_prem
        if total == 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        net_ratio = (bullish_prem - bearish_prem) / total
        confidence = min(abs(net_ratio), 1.0) * min(total / 5000000, 1.0)
        # OI source penalty: yfinance OI is end-of-day (15-min delayed) and
        # unreliable for intraday large option flow detection. Penalize
        # confidence by 30% when OI comes from yfinance backfill.
        oi_source = curr.get("oi_source", "yfinance_eod") if isinstance(curr, dict) else "yfinance_eod"
        oi_penalty = 0.7 if oi_source == "yfinance_eod" else 1.0
        if net_ratio > 0.3:
            return {"direction": "long", "confidence": confidence * oi_penalty, "action": "buy", "flow": flow, "strategy": self.name}
        elif net_ratio < -0.3:
            return {"direction": "short", "confidence": confidence * oi_penalty, "action": "sell", "flow": flow, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
