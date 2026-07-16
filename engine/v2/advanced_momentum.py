import numpy as np
from kronos.indicators.advanced_indicators import compute_all_advanced


class AdvancedMomentumStrategy:
    """V2 strategy using Fisher Transform, Aroon, CMO, KST, Coppock Curve.

    Combines multiple advanced momentum oscillators into a single
    high-confidence directional signal. Each oscillator votes; the final
    direction is determined by weighted majority.
    """

    def __init__(self):
        self.name = "advanced_momentum"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "UNKNOWN")
        ohlcv = context.get("ohlcv", [])

        if len(ohlcv) < 30:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0,
                    "strategy": self.name, "reasons": ["insufficient data"]}

        indicators = compute_all_advanced(ohlcv)
        reasons = []
        votes = {"long": 0, "short": 0, "neutral": 0}

        # Fisher Transform vote (weight: 2)
        fisher_sig = indicators.momentum.get("fisher_signal", "neutral")
        fisher_val = indicators.momentum.get("fisher", 0)
        if fisher_sig == "long":
            votes["long"] += 2
            reasons.append(f"fisher={fisher_val:.3f}")
        elif fisher_sig == "short":
            votes["short"] += 2
            reasons.append(f"fisher={fisher_val:.3f}")

        # Aroon vote (weight: 2)
        aroon_sig = indicators.signals.get("aroon_signal", "neutral")
        aroon_up = indicators.momentum.get("aroon_up", 50)
        aroon_down = indicators.momentum.get("aroon_down", 50)
        if aroon_sig == "long":
            votes["long"] += 2
            reasons.append(f"aroon_up={aroon_up:.0f} d={aroon_down:.0f}")
        elif aroon_sig == "short":
            votes["short"] += 2
            reasons.append(f"aroon_d={aroon_down:.0f} up={aroon_up:.0f}")

        # Chande Momentum Oscillator vote (weight: 1)
        cmo_sig = indicators.signals.get("cmo_signal", "neutral")
        cmo_val = indicators.momentum.get("cmo", 0)
        if cmo_sig == "long":
            votes["long"] += 1
            reasons.append(f"cmo={cmo_val:.1f}")
        elif cmo_sig == "short":
            votes["short"] += 1
            reasons.append(f"cmo={cmo_val:.1f}")

        # KST vote (weight: 1)
        kst_sig = indicators.momentum.get("kst_signal", "neutral")
        kst_val = indicators.momentum.get("kst", 0)
        if kst_sig == "long":
            votes["long"] += 1
            reasons.append(f"kst={kst_val:.2f}")
        elif kst_sig == "short":
            votes["short"] += 1
            reasons.append(f"kst={kst_val:.2f}")

        # Coppock Curve vote (weight: 1)
        coppock_sig = indicators.signals.get("coppock_signal", "neutral")
        coppock_val = indicators.momentum.get("coppock", 0)
        if coppock_sig == "long":
            votes["long"] += 1
            reasons.append(f"coppock={coppock_val:.1f}")
        elif coppock_sig == "short":
            votes["short"] += 1
            reasons.append(f"coppock={coppock_val:.1f}")

        total = sum(votes.values())
        direction = "neutral"
        confidence = 0.0

        if total >= 2 and votes["long"] > votes["short"]:
            direction = "long"
            confidence = min(votes["long"] / max(total, 1) * 0.85, 0.90)
        elif total >= 2 and votes["short"] > votes["long"]:
            direction = "short"
            confidence = min(votes["short"] / max(total, 1) * 0.85, 0.90)

        if not reasons:
            reasons.append("no momentum consensus")

        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
            "fisher": round(indicators.momentum.get("fisher", 0), 4),
        }
