from indicators.advanced_indicators import compute_all_advanced


class VolatilityRegimeStrategy:
    """V2 strategy using volatility-based indicators.

    Uses Hurst Exponent (trend persistence), SuperTrend (trend
    following), Chandelier Exit (volatility trailing stop), and
    Kalman Filter to determine the prevailing volatility regime
    and trade direction.
    """

    def __init__(self):
        self.name = "volatility_regime"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "UNKNOWN")
        ohlcv = context.get("ohlcv", [])

        if len(ohlcv) < 25:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0,
                    "strategy": self.name, "reasons": ["insufficient data"]}

        indicators = compute_all_advanced(ohlcv)
        reasons = []

        # SuperTrend (weight: 3) — primary trend direction
        st_sig = indicators.signals.get("supertrend_signal", "neutral")
        st_trend = indicators.volatility.get("supertrend", "neutral")

        # Hurst Exponent (weight: 2) — confirms trend quality
        hurst_sig = indicators.signals.get("hurst_signal", "neutral")
        hurst_val = indicators.volatility.get("hurst", 0.5)
        hurst_regime = indicators.volatility.get("hurst_regime", "random")

        # Chandelier Exit (weight: 2)
        chandelier_sig = indicators.signals.get("chandelier_signal", "neutral")
        chandelier_dir = indicators.volatility.get("chandelier_direction", "neutral")

        # Kalman Filter trend (weight: 1)
        kalman_trend = indicators.volatility.get("kalman_trend", "neutral")

        # Ulcer Index (weight: 1) — risk qualifier
        ulcer_val = indicators.volatility.get("ulcer", 0)
        ulcer_risk = indicators.volatility.get("ulcer_risk", "low")

        # Voting
        direction = "neutral"
        confidence = 0.0
        long_score = 0
        short_score = 0
        total_score = 0

        if st_sig == "long":
            long_score += 3
            reasons.append(f"supertrend={st_trend}")
        elif st_sig == "short":
            short_score += 3
            reasons.append(f"supertrend={st_trend}")

        if hurst_sig == "long":
            long_score += 2
            reasons.append(f"hurst={hurst_val:.3f}({hurst_regime})")
        elif hurst_sig == "short":
            short_score += 2
            reasons.append(f"hurst={hurst_val:.3f}({hurst_regime})")

        if chandelier_sig == "long":
            long_score += 2
            reasons.append(f"chandelier={chandelier_dir}")
        elif chandelier_sig == "short":
            short_score += 2
            reasons.append(f"chandelier={chandelier_dir}")

        if kalman_trend == "up":
            long_score += 1
        elif kalman_trend == "down":
            short_score += 1

        total_score = long_score + short_score

        if long_score > short_score and long_score >= 3:
            direction = "long"
            base_conf = long_score / max(total_score, 1)
            # Boost confidence when trend is strong and risk is low
            if hurst_regime == "trending" and ulcer_risk == "low":
                base_conf = min(base_conf + 0.10, 0.92)
            elif ulcer_risk == "high":
                base_conf = max(base_conf - 0.08, 0.20)
            confidence = base_conf
        elif short_score > long_score and short_score >= 3:
            direction = "short"
            base_conf = short_score / max(total_score, 1)
            if hurst_regime == "trending" and ulcer_risk == "low":
                base_conf = min(base_conf + 0.10, 0.92)
            elif ulcer_risk == "high":
                base_conf = max(base_conf - 0.08, 0.20)
            confidence = base_conf
        else:
            if long_score > 0 or short_score > 0:
                reasons.append("volatility_conflict")
            else:
                reasons.append("no_volatility_consensus")

        if not reasons:
            reasons.append("no clear volatility signal")

        supertrend_val = indicators.volatility.get("supertrend", "neutral")
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
            "supertrend": supertrend_val,
        }
