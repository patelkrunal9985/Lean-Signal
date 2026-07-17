from engine.v3.base import BaseV3Strategy


class IronCondorDetector(BaseV3Strategy):
    name = "iron_condor_detector"
    description = "Detects iron condor opportunities in range-bound regimes — symmetrical OTM credit on both sides"
    applies_to = ("option",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        dte = context.get("dte", 30)
        iv = context.get("iv", 0)
        regime = context.get("regime", "unknown")
        breadth = context.get("market_breadth", {})

        if underlying <= 0 or not chain:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        if regime != "ranging":
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        comp_score = breadth.get("composite", 0) if isinstance(breadth, dict) else 0
        if comp_score < -0.3 or comp_score > 0.3:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        if dte < 1 or dte > 14:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        iv_pct = iv / 100.0 if iv > 1 else iv
        if iv_pct <= 0.15:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        calls = chain.get("calls", [])
        puts = chain.get("puts", [])

        if not calls or not puts:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        sorted_calls = sorted(calls, key=lambda c: c.get("strike", 0) or 0)
        sorted_puts = sorted(puts, key=lambda p: p.get("strike", 0) or 0, reverse=True)

        otm_puts = [p for p in sorted_puts if 0.80 * underlying <= (p.get("strike", 0) or 0) <= 0.90 * underlying]
        otm_calls = [c for c in sorted_calls if 1.10 * underlying <= (c.get("strike", 0) or 0) <= 1.20 * underlying]

        if not otm_puts or not otm_calls:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        def _find_long_put(short_strike, put_list):
            for p in put_list:
                k = p.get("strike", 0) or 0
                if 0 < k < short_strike:
                    return p
            return {}

        def _find_long_call(short_strike, call_list):
            for c in call_list:
                k = c.get("strike", 0) or 0
                if k > short_strike:
                    return c
            return {}

        def _compute_put_spread(short_put, long_put):
            sp_bid = short_put.get("bid", 0) or 0
            lp_ask = long_put.get("ask", 999) or 999
            sp_k = short_put.get("strike", 0) or 0
            lp_k = long_put.get("strike", 0) or 0
            credit = sp_bid - lp_ask
            if credit <= 0 or lp_k <= 0 or sp_k <= lp_k:
                return None
            spread_width = (sp_k - lp_k) / underlying
            pop = min(credit / max(spread_width * underlying, 0.01), 1.0)
            return {"credit": credit, "pop": pop, "short_k": sp_k, "long_k": lp_k}

        def _compute_call_spread(short_call, long_call):
            sc_bid = short_call.get("bid", 0) or 0
            lc_ask = long_call.get("ask", 999) or 999
            sc_k = short_call.get("strike", 0) or 0
            lc_k = long_call.get("strike", 0) or 0
            credit = sc_bid - lc_ask
            if credit <= 0 or lc_k <= 0 or lc_k <= sc_k:
                return None
            spread_width = (lc_k - sc_k) / underlying
            pop = min(credit / max(spread_width * underlying, 0.01), 1.0)
            return {"credit": credit, "pop": pop, "short_k": sc_k, "long_k": lc_k}

        best = None
        best_conf = 0.0

        for short_put in otm_puts:
            long_put = _find_long_put(short_put.get("strike", 0) or 0, sorted_puts)
            if not long_put:
                continue
            put_spread = _compute_put_spread(short_put, long_put)
            if put_spread is None or put_spread["pop"] < 0.80:
                continue

            for short_call in otm_calls:
                long_call = _find_long_call(short_call.get("strike", 0) or 0, sorted_calls)
                if not long_call:
                    continue
                call_spread = _compute_call_spread(short_call, long_call)
                if call_spread is None or call_spread["pop"] < 0.80:
                    continue

                combined_credit = put_spread["credit"] + call_spread["credit"]
                combined_credit_pct = combined_credit / underlying
                if combined_credit_pct < 0.005:
                    continue

                ic_width = (call_spread["short_k"] - put_spread["short_k"]) / underlying
                if ic_width < 0.03 or ic_width > 0.10:
                    continue

                avg_pop = (put_spread["pop"] + call_spread["pop"]) / 2.0
                confidence = min(combined_credit_pct * 15.0 + avg_pop * 0.3, 0.85)

                if confidence > best_conf:
                    best_conf = confidence
                    best = {
                        "direction": "neutral",
                        "confidence": round(confidence, 4),
                        "strategy": self.name,
                        "action": "sell",
                        "credits": {
                            "call": round(call_spread["credit"], 2),
                            "put": round(put_spread["credit"], 2),
                            "total": round(combined_credit, 2),
                        },
                        "pop": {
                            "call": round(call_spread["pop"], 4),
                            "put": round(put_spread["pop"], 4),
                        },
                        "strikes": {
                            "call_short": call_spread["short_k"],
                            "call_long": call_spread["long_k"],
                            "put_short": put_spread["short_k"],
                            "put_long": put_spread["long_k"],
                        },
                        "width_pct": round(ic_width * 100, 2),
                        "regime_suitable": True,
                        "dte": dte,
                    }

        if best is None:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        return best
