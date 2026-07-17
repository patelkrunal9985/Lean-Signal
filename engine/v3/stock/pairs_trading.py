import logging

from engine.v3.base import BaseV3Strategy

logger = logging.getLogger(__name__)

TRADING_PAIRS = [
    ("ES=F", "NQ=F", "future"),
    ("YM=F", "RTY=F", "future"),
    ("GC=F", "MGC=F", "future"),  # Gold/Micro-Gold micro relative (replaces broken GC=F/SI=F since SI=F remains dormant)  # GCC_si_DORMANT: GC=F/SI=F removed from FIXED_FUTURES July 2026; entries dormant until user re-adds gold/silver pairs trading.
    ("SPY", "QQQ", "stock"),
    ("AAPL", "MSFT", "stock"),
    ("JPM", "GS", "stock"),
]


class PairsTrading(BaseV3Strategy):
    name = "pairs_trading"
    description = "Mean reversion on cointegrated pair spreads"
    applies_to = ("stock", "future")
    default_weight = 0.05

    def _get_pair(self, ticker: str):
        for leg_a, leg_b, instr_type in TRADING_PAIRS:
            if ticker.upper() == leg_a.upper():
                return leg_a, leg_b, instr_type, 1.0
            if ticker.upper() == leg_b.upper():
                return leg_a, leg_b, instr_type, -1.0
        return None

    def compute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        ohlcv = context.get("ohlcv", [])
        pair_info = self._get_pair(ticker)
        if not pair_info or not ohlcv or len(ohlcv) < 20:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        leg_a, leg_b, instr_type, direction_sign = pair_info
        leg_b_bars = None
        try:
            from engine.skills.ibkr_data_feed import fetch_historical_bars
            leg_b_bars = fetch_historical_bars(leg_b, duration="2 M", bar_size="1 day")
        except Exception:
            pass
        if not leg_b_bars or len(leg_b_bars) < 20:
            return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
        try:
            leg_a_closes = [b["close"] for b in ohlcv]
            leg_b_closes = [b["close"] for b in leg_b_bars]
            n = min(len(leg_a_closes), len(leg_b_closes), 20)
            ratios = [leg_a_closes[-n + i] / leg_b_closes[-n + i] for i in range(n) if leg_b_closes[-n + i] > 0]
            if len(ratios) < 10:
                return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
            mu = sum(ratios) / len(ratios)
            var = sum((r - mu) ** 2 for r in ratios) / len(ratios)
            sigma = var ** 0.5
            if sigma <= 0:
                return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
            ratio = leg_a_closes[-1] / leg_b_closes[-1]
            z = (ratio - mu) / sigma
            if direction_sign < 0:
                z = -z
            if z < -2.0:
                conf = min(min(abs(z) * 0.20, 0.80) + 0.10, 0.85)
                return {"direction": "long", "confidence": round(conf, 4), "strategy": self.name, "pair": f"{leg_a}/{leg_b}", "z_score": round(z, 3)}
            elif z > 2.0:
                conf = min(min(abs(z) * 0.20, 0.80) + 0.10, 0.85)
                return {"direction": "short", "confidence": round(conf, 4), "strategy": self.name, "pair": f"{leg_a}/{leg_b}", "z_score": round(z, 3)}
        except Exception as e:
            logger.debug("pairs_trading: compute failed for %s: %s", ticker, e)
        return {"direction": "neutral", "confidence": 0.0, "strategy": self.name}
