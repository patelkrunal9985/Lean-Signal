from engine.v3.base import BaseV3Strategy


class IVRankPercentile(BaseV3Strategy):
    name = "iv_rank_percentile"
    description = "Compares current IV to its historical range — sell when IV is high/expensive, buy when IV is low/cheap"
    applies_to = ("option",)
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        hv_10 = context.get("hv_10", 0.5)

        if underlying <= 0 or not chain:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        calls = chain.get("calls", [])
        puts = chain.get("puts", [])
        all_contracts = calls + puts

        if not all_contracts:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        atm_call = min(calls, key=lambda c: abs((c.get("strike", 0) or 0) - underlying)) if calls else {}
        atm_put = min(puts, key=lambda p: abs((p.get("strike", 0) or 0) - underlying)) if puts else {}

        atm_iv = max(
            atm_call.get("impliedVolatility", context.get("iv", 0.2) * 100) or 0,
            atm_put.get("impliedVolatility", context.get("iv", 0.2) * 100) or 0,
        )

        if atm_iv > 200:
            atm_iv = 0

        ivs = [c.get("impliedVolatility", 0) or 0 for c in all_contracts if (c.get("impliedVolatility", 0) or 0) > 0]
        if not ivs:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        min_iv = min(ivs)
        max_iv = max(ivs)

        if max_iv <= min_iv:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        atm_iv_pct = atm_iv / 100.0
        min_iv_pct = min_iv / 100.0
        max_iv_pct = max_iv / 100.0

        iv_rank = (atm_iv_pct - min_iv_pct) / (max_iv_pct - min_iv_pct + 0.001)
        iv_rank = max(0.0, min(iv_rank, 1.0))

        expensive = iv_rank > 0.80
        cheap = iv_rank < 0.20

        if expensive:
            confidence = min((iv_rank - 0.80) * 3.0, 0.75)
            direction = "short"
        elif cheap:
            confidence = min((0.20 - iv_rank) * 3.0, 0.70)
            if hv_10 < 0.10:
                confidence *= 1.15
            direction = "long"
        else:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        confidence = min(confidence, 0.80)

        return {
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "action": "sell" if expensive else "buy",
            "iv_rank": round(iv_rank, 4),
            "atm_iv": round(atm_iv_pct, 4),
            "min_iv": round(min_iv_pct, 4),
            "max_iv": round(max_iv_pct, 4),
            "expensive": expensive,
            "cheap": cheap,
            "hv_10_confirmed": hv_10 < 0.10 if cheap else False,
        }
