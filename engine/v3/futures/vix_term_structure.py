import logging
import numpy as np
from kronos.strategies.v3.base import BaseV3Strategy


class VIXTermStructure(BaseV3Strategy):
    # Class-level warning rate-limit (V3 strategies instantiate fresh per cycle)
    _warned_vix_unavailable_class: bool = False
    name = "vix_term_structure"
    description = "VIX futures curve contango/backwardation for equity signals"
    applies_to = ("future", "stock")
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        vix_spot = context.get("vix_spot", 0)
        vix_1m = context.get("vix_1m", 0)
        vix_2m = context.get("vix_2m", 0)
        if vix_spot <= 0 or vix_1m <= 0:
            cls = type(self)
            if not cls._warned_vix_unavailable_class:
                logging.getLogger(__name__).warning(
                    "VIXTermStructure: vix_spot=%s vix_1m=%s (data feed unavailable (IBKR connect or market state)); "
                    "strategy returning neutral. check IBKR data stream and market hours.",
                    vix_spot, vix_1m
                )
                cls._warned_vix_unavailable_class = True
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        total_slope = np.log(vix_2m / vix_spot) if vix_spot > 0 and vix_2m > 0 else 0
        is_elevated = vix_spot > 20
        is_extreme = vix_spot > 30
        is_low = vix_spot < 16
        confidence = 0.0
        direction = "neutral"
        if total_slope < -0.05 and is_elevated:
            direction = "long"
            confidence = min(abs(total_slope) * 3, 0.80)
            if is_extreme:
                confidence = min(confidence * 1.2, 0.90)
        elif total_slope > 0.10 and is_low:
            direction = "short"
            confidence = min(total_slope * 2, 0.65)
        return {"direction": direction, "confidence": confidence, "total_slope": total_slope, "vix_spot": vix_spot, "strategy": self.name}
