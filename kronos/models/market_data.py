from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class OHLCV:
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime


@dataclass
class Position:
    ticker: str
    direction: str
    quantity: float
    entry_price: float
    current_price: float
    instrument_type: str = "stock"
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    opened_at: Optional[datetime] = None


@dataclass
class Portfolio:
    cash: float
    initial_capital: float
    positions: list = field(default_factory=list)
    total_value: float = 0.0
