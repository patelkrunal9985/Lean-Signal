import logging

import numpy as np

from engine.v3.base import BaseV3Strategy

logger = logging.getLogger(__name__)


class CalendarSpread(BaseV3Strategy):
    name = "calendar_spread"
    description = "Calendar spread divergence from mean"
    applies_to = ("future",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        front_prices = context.get("front_hist", [])
        next_prices = context.get("next_hist", [])
        front = context.get("front_month_price", 0)
        nxt = context.get("next_month_price", 0)
        if len(front_prices) < 20 or len(next_prices) < 20:
            logger.info("calendar_spread: neutral (front_hist=%d, next_hist=%d) — <20 bars", len(front_prices), len(next_prices))
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        spreads = [f - n for f, n in zip(front_prices, next_prices)]
        current = front - nxt
        mean_s = np.mean(spreads)
        std_s = np.std(spreads) if np.std(spreads) > 0 else 0.001
        z = (current - mean_s) / std_s
        trend = spreads[-1] - spreads[-6] if len(spreads) >= 6 else 0
        confidence = min(abs(z) / 4.0, 0.75)
        logger.info("calendar_spread: front=%s nxt=%s curr_spread=%s mean=%s std=%s z=%s trend=%s conf=%s",
                     front, nxt, round(current, 2), round(mean_s, 2), round(std_s, 4),
                     round(z, 4), round(trend, 4), round(confidence, 4))
        if trend > 0 and z > 0:
            return {"direction": "long", "confidence": confidence * 0.6, "z_score": z, "strategy": self.name}
        elif trend < 0 and z < 0:
            return {"direction": "short", "confidence": confidence * 0.6, "z_score": z, "strategy": self.name}
        elif z > 2.0 and trend < 0:
            return {"direction": "short", "confidence": min(confidence * 0.85, 0.75), "z_score": z, "strategy": self.name}
        elif z < -2.0 and trend > 0:
            return {"direction": "long", "confidence": min(confidence * 0.85, 0.75), "z_score": z, "strategy": self.name}
        logger.info("calendar_spread: neutral (no condition met, z=%s)", round(z, 4))
        return {"direction": "neutral", "confidence": 0.0, "z_score": z, "strategy": self.name}
