from indicators.advanced_indicators import compute_all_advanced


class MarketStructureStrategy:
    """V2 strategy using ICT/Smart Money concepts.

    Analyzes Fair Value Gaps (FVG), Market Structure Breaks (MSB),
    liquidity sweeps, and volume profile to detect institutional
    order flow and high-probability entry points.
    """

    def __init__(self):
        self.name = "market_structure"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "UNKNOWN")
        ohlcv = context.get("ohlcv", [])
        ctx_indicators = context.get("indicators", {})

        if len(ohlcv) < 15:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0,
                    "strategy": self.name, "reasons": ["insufficient data"]}

        indicators = compute_all_advanced(ohlcv)
        reasons = []

        # FVG signal (weight: 3) — strongest structure signal
        fvg_sig = indicators.signals.get("fvg_signal", "neutral")
        fvg_count = indicators.smart_money.get("fvg_count", 0)
        fvg_strength = indicators.smart_money.get("fvg_strength", 0)

        # MSB signal (weight: 2)
        msb_sig = indicators.signals.get("msb_signal", "neutral")
        msb_dir = indicators.smart_money.get("msb_direction", "neutral")

        # Liquidity signal (weight: 2)
        liq_sig = indicators.signals.get("liq_signal", "neutral")
        liq_sweep = indicators.smart_money.get("liquidity_sweep", False)

        # Value area signal (weight: 1)
        va_sig = indicators.signals.get("value_area_signal", "neutral")
        va_width = indicators.volume_profile.get("value_area_width", 0)

        # Count votes with weights
        direction = "neutral"
        confidence = 0.0
        long_score = 0
        short_score = 0
        max_score = 0

        if fvg_sig == "long":
            long_score += 3 * fvg_count if fvg_count > 0 else 3
            reasons.append(f"fvg={fvg_count}x str={fvg_strength:.2f}")
        elif fvg_sig == "short":
            short_score += 3 * fvg_count if fvg_count > 0 else 3
            reasons.append(f"fvg={fvg_count}x str={fvg_strength:.2f}")

        if msb_sig == "long":
            long_score += 2
            reasons.append(f"msb={msb_dir}")
        elif msb_sig == "short":
            short_score += 2
            reasons.append(f"msb={msb_dir}")

        if liq_sig == "long":
            long_score += 2
            reasons.append("liq_sweep_buy")
        elif liq_sig == "short":
            short_score += 2
            reasons.append("liq_sweep_sell")

        if va_sig == "long":
            long_score += 1
            reasons.append("value_area_support")
        elif va_sig == "short":
            short_score += 1
            reasons.append("value_area_resistance")

        # Divergence from cumulative delta (weight: 1)
        delta_sig = indicators.signals.get("delta_signal", "neutral")
        delta_val = indicators.flow.get("cumulative_delta", 0)
        if delta_sig == "long":
            long_score += 1
            reasons.append(f"delta_div={delta_val:.0f}")
        elif delta_sig == "short":
            short_score += 1
            reasons.append(f"delta_div={delta_val:.0f}")

        max_score = long_score + short_score
        if long_score > short_score and long_score >= 2:
            direction = "long"
            confidence = min(long_score / max(long_score + short_score, 1) * 0.88, 0.92)
        elif short_score > long_score and short_score >= 2:
            direction = "short"
            confidence = min(short_score / max(long_score + short_score, 1) * 0.88, 0.92)
        elif long_score > 0 and long_score == short_score:
            direction = "neutral"
            confidence = 0.0
            reasons.append("structure_conflict")

        # IEX depth order flow confirmation (from context, not AdvancedIndicatorSet)
        if direction != "neutral":
            of = ctx_indicators.get("order_flow_imbalance", {})
            iex_dir = of.get("direction", "neutral")
            if (direction == "long" and iex_dir == "bullish") or (direction == "short" and iex_dir == "bearish"):
                confidence = min(confidence + 0.10, 0.92)
                reasons.append("iex_flow_confirm")
            elif (direction == "long" and iex_dir == "bearish") or (direction == "short" and iex_dir == "bullish"):
                confidence = max(confidence - 0.08, 0.0)
                reasons.append("iex_flow_conflict")

        if not reasons:
            reasons.append("no clear structure")

        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
            "structure_scores": {"long": long_score, "short": short_score},
        }
