from abc import ABC, abstractmethod
from typing import Any


# Strategy families for confluence diversity checking.
# Each family represents an independent information axis.
STRATEGY_FAMILIES = {
    "technical": "price/volume patterns, indicators (RSI, MACD, BB, SMA)",
    "flow": "order flow, cumulative delta, depth, tape reading",
    "options_micro": "option greeks, gamma, delta, charm, vanna, IV skew",
    "options_macro": "option flow, OI concentration, put/call ratios, whale trades",
    "macro": "COT, VIX term structure, breadth, regime, calendar",
    "volume": "volume profile, VPIN, volume spike, iceberg detection",
    "volatility": "vol term structure, IV/RV, skew, straddles, convexity",
    "mtf": "multi-timeframe alignment, session structure",
}

# Strategy weight multipliers by family.
# Higher-weight families have proven reliability data axes.
# Lower-weight families are noise-prone or confirmatory only.
WEIGHT_BY_FAMILY = {
    "options_micro": 0.08,   # gamma, delta, Greeks — most reliable for 0DTE
    "options_macro": 0.06,   # whale flow, OI concentration
    "flow": 0.06,            # order flow, cumulative delta
    "mtf": 0.06,             # multi-timeframe alignment
    "volume": 0.05,          # volume profile (default)
    "technical": 0.04,       # RSI, MACD, BB — noisy for 0DTE
    "volatility": 0.04,      # IV/RV, skew — useful but late
    "macro": 0.03,           # COT, breadth — slow, confirmatory only
}


class BaseV3Strategy(ABC):
    name: str = ""
    description: str = ""
    applies_to: tuple[str, ...] = ("stock",)
    default_weight: float = 0.05
    family: str = "technical"  # one of STRATEGY_FAMILIES keys

    @abstractmethod
    def compute(self, context: dict) -> dict:
        ...

    def get_required_data(self) -> list[str]:
        return []
