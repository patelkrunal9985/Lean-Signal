from engine.v3.base import BaseV3Strategy


class CreditSpreadDetector(BaseV3Strategy):
    name = "credit_spread_detector"
    description = "Detects favorable credit spread opportunities — sell OTM premium when IV is elevated and skew supports it"
    applies_to = ("option",)
    default_weight = 0.10

    def compute(self, context: dict) -> dict:
        chain = context.get("option_chain", {})
        underlying = context.get("underlying_price", 0)
        dte = context.get("dte", 30)
        iv = context.get("iv", 0)

        if underlying <= 0 or not chain or dte > 14 or dte < 1:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        iv_pct = iv / 100.0 if iv > 1 else iv
        if iv_pct <= 0.15:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        calls = chain.get("calls", [])
        puts = chain.get("puts", [])

        if not calls or not puts:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        def _strike_key(c):
            return c.get("strike", 0) or 0

        sorted_calls = sorted(calls, key=_strike_key)
        sorted_puts = sorted(puts, key=_strike_key, reverse=True)

        otm_puts = [p for p in sorted_puts if 0.85 * underlying <= (p.get("strike", 0) or 0) <= 0.95 * underlying]
        otm_calls = [c for c in sorted_calls if 1.05 * underlying <= (c.get("strike", 0) or 0) <= 1.15 * underlying]

        if not otm_puts or not otm_calls:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        def _find_long_put(short_strike, put_list):
            for p in put_list:
                k = p.get("strike", 0) or 0
                if k < short_strike:
                    return p
            return {}

        def _find_long_call(short_strike, call_list):
            for c in call_list:
                k = c.get("strike", 0) or 0
                if k > short_strike:
                    return c
            return {}

        best = {"credit": 0.0, "pop": 0.0, "width": 0.0, "short_put": 0, "short_call": 0, "long_put": 0, "long_call": 0}
        num_opportunities = 0

        for short_put in otm_puts:
            spk = short_put.get("strike", 0) or 0
            sp_bid = short_put.get("bid", 0) or 0
            long_put = _find_long_put(spk, sorted_puts)
            lpk = long_put.get("strike", 0) or 0
            lp_ask = long_put.get("ask", 999) or 999
            put_credit = sp_bid - lp_ask if lpk > 0 else sp_bid * 0.5
            if put_credit <= 0:
                continue

            for short_call in otm_calls:
                sck = short_call.get("strike", 0) or 0
                sc_bid = short_call.get("bid", 0) or 0
                long_call = _find_long_call(sck, sorted_calls)
                lck = long_call.get("strike", 0) or 0
                lc_ask = long_call.get("ask", 999) or 999
                call_credit = sc_bid - lc_ask if lck > 0 else sc_bid * 0.5
                if call_credit <= 0:
                    continue

                total_credit = put_credit + call_credit
                width = (sck - spk) / underlying
                if width <= 0:
                    continue

                pop = total_credit / width
                pop = min(pop, 1.0)

                if pop > 0.85 and width < 0.05:
                    num_opportunities += 1
                    if total_credit > best["credit"]:
                        credit_collected_pct = total_credit / underlying
                        confidence = min(pop * credit_collected_pct * 1.2, 0.90)
                        best = {
                            "credit": total_credit,
                            "pop": pop,
                            "width": width,
                            "short_put": spk,
                            "short_call": sck,
                            "long_put": lpk,
                            "long_call": lck,
                            "confidence": confidence,
                        }

        if best["credit"] <= 0:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}

        return {
            "direction": "neutral",
            "confidence": round(best["confidence"], 4),
            "strategy": self.name,
            "action": "sell",
            "best_credit": round(best["credit"], 2),
            "best_pop": round(best["pop"], 4),
            "best_width": round(best["width"], 4),
            "short_put_strike": best["short_put"],
            "long_put_strike": best["long_put"],
            "short_call_strike": best["short_call"],
            "long_call_strike": best["long_call"],
            "dte": dte,
            "num_opportunities": num_opportunities,
        }
