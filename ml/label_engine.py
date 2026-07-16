"""Label engine for supervised ML training.
Primary: Triple-barrier labeling (volatility-adjusted)
Secondary: Fixed-horizon return
"""
import numpy as np
from typing import Any


class LabelEngine:
    """
    Generates training labels from OHLCV data.

    Primary method: Triple-barrier labeling
    - Upper barrier at entry + 1.5 x ATR
    - Lower barrier at entry - 1.5 x ATR
    - Label = +1 if upper touched first, -1 if lower, 0 if neither within max_bars

    Secondary method: Fixed-horizon return
    - Label = sign of forward return over N bars
    """

    def __init__(self, max_bars: int = 20):
        self.max_bars = max_bars

    def triple_barrier_label(self, entry_idx: int, closes: list[float],
                              highs: list[float], lows: list[float],
                              atr: float) -> int:
        entry_price = closes[entry_idx]
        upper_barrier = entry_price + 1.5 * atr
        lower_barrier = entry_price - 1.5 * atr

        end_idx = min(entry_idx + self.max_bars, len(closes) - 1)

        for i in range(entry_idx + 1, end_idx + 1):
            if highs[i] >= upper_barrier:
                return 1
            if lows[i] <= lower_barrier:
                return -1

        return 0

    def fixed_horizon_label(self, entry_idx: int, closes: list[float],
                             horizon: int = 5) -> int:
        end_idx = min(entry_idx + horizon, len(closes) - 1)
        if end_idx <= entry_idx:
            return 0

        fwd_return = (closes[end_idx] - closes[entry_idx]) / max(closes[entry_idx], 0.01)

        if fwd_return > 0.01:
            return 1
        elif fwd_return < -0.01:
            return -1
        return 0

    def generate_labels(self, ohlcv: list[dict], method: str = "triple_barrier") -> np.ndarray:
        if not ohlcv or len(ohlcv) < self.max_bars + 5:
            return np.array([])

        closes = np.array([c["close"] for c in ohlcv], dtype=float)
        highs = np.array([c["high"] for c in ohlcv], dtype=float)
        lows = np.array([c["low"] for c in ohlcv], dtype=float)

        labels = np.zeros(len(ohlcv), dtype=int)

        if method == "triple_barrier":
            from kronos.utils.helpers import calculate_atr
            atr = calculate_atr(highs.tolist(), lows.tolist(), closes.tolist(), 14)
            if not atr:
                return labels

            for i in range(len(ohlcv) - self.max_bars - 1):
                labels[i] = self.triple_barrier_label(i, closes.tolist(), highs.tolist(), lows.tolist(), atr)

        elif method == "fixed_horizon":
            for i in range(len(ohlcv) - 5):
                labels[i] = self.fixed_horizon_label(i, closes.tolist())

        return labels
