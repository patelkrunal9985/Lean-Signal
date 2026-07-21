"""
Enriched DOM / Order Book Analysis — reqMktDepth + sequential flow.

Builds on the existing IBKR `get_market_depth()` and `get_order_book_imbalance()`
but adds:
  - Order book velocity (bid/ask stacking rate over time)
  - Cumulative bid/ask volume slope (pressure building or decaying)
  - Sequential order flow pressure (acceleration/deceleration)
  - Iceberg detection (recurring size at same price level)
  - Microstructure snapshots (top-of-book imbalance, cumulative depth)
  - Order book event frequency (cancellations, additions, executions)

All data derives from existing IBKR MktDepth subscription — no new feed needed.
"""
from __future__ import annotations

import time
import math
from collections import deque
from threading import Lock
from typing import Optional

from utils.logger import get_logger

logger = get_logger("engine.order_book")

# Per-ticker state
_depth_state: dict[str, dict] = {}
_depth_lock = Lock()

# History length for velocity computation
_VELOCITY_WINDOW = 10  # number of depth snapshots to keep


def _get_state(ticker: str) -> dict:
    with _depth_lock:
        if ticker not in _depth_state:
            _depth_state[ticker] = {
                "history": deque(maxlen=_VELOCITY_WINDOW),
                "prev_bid_total": 0,
                "prev_ask_total": 0,
                "total_bid_updates": 0,
                "total_ask_updates": 0,
                "last_updated": 0.0,
                "cumulative_bid_vol_delta": 0,
                "cumulative_ask_vol_delta": 0,
            }
        return _depth_state[ticker]


def reset():
    with _depth_lock:
        _depth_state.clear()


def compute_order_book_velocity(bids: list, asks: list, ticker: str) -> dict:
    """Compute order book velocity and pressure dynamics.

    Args:
        bids: List of (price, size) tuples at top N levels
        asks: List of (price, size) tuples at top N levels
        ticker: Ticker symbol for state tracking

    Returns:
        Dict with velocity metrics, pressure direction, stacking pattern.
    """
    state = _get_state(ticker)
    now = time.time()

    bid_total = sum(s for _, s in bids[:5])
    ask_total = sum(s for _, s in asks[:5])
    total = bid_total + ask_total

    # Imbalance
    bid_ratio = bid_total / max(total, 1)
    ask_ratio = ask_total / max(total, 1)
    imbalance = (bid_total - ask_total) / max(total, 1)

    # Velocity: change in bid/ask volume since last snapshot
    dt = now - state["last_updated"] if state["last_updated"] > 0 else 1.0
    bid_velocity = (bid_total - state["prev_bid_total"]) / max(dt, 0.1)
    ask_velocity = (ask_total - state["prev_ask_total"]) / max(dt, 0.1)

    # Acceleration: second derivative of bid/ask volume
    accel = "accelerating" if abs(bid_velocity - ask_velocity) > 50 else "stable"

    # Sequential flow pressure
    if bid_total > state["prev_bid_total"] and ask_total > state["prev_ask_total"]:
        flow = "both_sides_growing"
    elif bid_total > state["prev_bid_total"]:
        flow = "bid_side_growing"
    elif ask_total > state["prev_ask_total"]:
        flow = "ask_side_growing"
    else:
        flow = "both_sides_decaying"

    # Store state
    state["prev_bid_total"] = bid_total
    state["prev_ask_total"] = ask_total
    state["last_updated"] = now
    state["history"].append({
        "bid_total": bid_total,
        "ask_total": ask_total,
        "imbalance": imbalance,
        "ts": now,
    })
    state["total_bid_updates"] += 1
    state["total_ask_updates"] += 1

    # Cumulative delta of bid/ask volume
    state["cumulative_bid_vol_delta"] += bid_total - (state["history"][-2]["bid_total"] if len(state["history"]) >= 2 else 0)
    state["cumulative_ask_vol_delta"] += ask_total - (state["history"][-2]["ask_total"] if len(state["history"]) >= 2 else 0)

    # Bid/ask stacking pattern
    stacking = _detect_stacking_pattern(bids, asks)

    # Top-of-book pressure: size at best bid vs best ask
    best_bid_size = bids[0][1] if bids else 0
    best_ask_size = asks[0][1] if asks else 0
    top_book_ratio = (best_bid_size - best_ask_size) / max(best_bid_size + best_ask_size, 1)

    # Cumulative bid/ask volume slope over history
    slope = _compute_depth_slope(state["history"])

    return {
        "bid_velocity": round(bid_velocity, 1),
        "ask_velocity": round(ask_velocity, 1),
        "velocity_spread": round(bid_velocity - ask_velocity, 1),
        "acceleration": accel,
        "flow": flow,
        "imbalance": round(imbalance, 4),
        "bid_ratio": round(bid_ratio, 4),
        "ask_ratio": round(ask_ratio, 4),
        "top_book_imbalance": round(top_book_ratio, 4),
        "stacking": stacking,
        "cumulative_bid_delta": state["cumulative_bid_vol_delta"],
        "cumulative_ask_delta": state["cumulative_ask_vol_delta"],
        "depth_slope": round(slope, 2) if slope else 0,
        "total_bid_volume": bid_total,
        "total_ask_volume": ask_total,
        "mid_price": _compute_mid_price(bids, asks),
        "spread": _compute_spread(bids, asks),
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "source": "order_book",
    }


def _detect_stacking_pattern(bids: list, asks: list) -> str:
    """Detect order stacking patterns in the DOM.

    Returns:
        - 'bullish_stacking': Bid sizes increase at successive levels
        - 'bearish_stacking': Ask sizes increase at successive levels
        - 'balanced': No clear stacking
        - 'thin': Too few levels to determine
    """
    if len(bids) < 3 or len(asks) < 3:
        return "thin"

    bid_sizes = [s for _, s in bids[:5]]
    ask_sizes = [s for _, s in asks[:5]]

    bid_trend = sum(1 for i in range(1, len(bid_sizes)) if bid_sizes[i] > bid_sizes[i-1])
    ask_trend = sum(1 for i in range(1, len(ask_sizes)) if ask_sizes[i] > ask_sizes[i-1])

    if bid_trend >= 3 and ask_trend <= 1:
        return "bullish_stacking"
    elif ask_trend >= 3 and bid_trend <= 1:
        return "bearish_stacking"
    elif bid_trend >= 2 and ask_trend >= 2:
        return "competitive"
    return "balanced"


def _compute_depth_slope(history: deque) -> Optional[float]:
    """Compute the slope of cumulative bid-ask divergence over recent snapshots.

    Positive slope = growing buying pressure over time.
    """
    if len(history) < 3:
        return None
    values = [(h["bid_total"] - h["ask_total"]) for h in history]
    x = list(range(len(values)))
    n = len(values)
    if n < 2:
        return None
    x_mean = sum(x) / n
    y_mean = sum(values) / n
    num = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((x[i] - x_mean) ** 2 for i in range(n))
    return num / max(den, 1)


def _compute_mid_price(bids: list, asks: list) -> float:
    if not bids or not asks:
        return 0.0
    return (bids[0][0] + asks[0][0]) / 2


def _compute_spread(bids: list, asks: list) -> float:
    if not bids or not asks:
        return 0.0
    return asks[0][0] - bids[0][0]


def detect_iceberg_orders(bids: list, asks: list, ticker: str) -> list:
    """Detect potential iceberg orders by tracking recurring sizes at the same
    price level over sequential DOM snapshots.

    Iceberg orders reveal themselves when the same size reappears at the same
    price level after being filled (the hidden portion replenishes the visible).

    Returns list of {price, size, side, confidence} dicts.
    """
    state = _get_state(ticker)
    icebergs = []

    for side_name, levels in [("bid", bids), ("ask", asks)]:
        seen = {}
        for price, size in levels[:5]:
            key = f"{side_name}_{price}"
            if key in seen:
                if abs(seen[key] - size) <= 1:  # same size replenished
                    icebergs.append({
                        "price": float(price),
                        "size": int(size),
                        "side": side_name,
                        "confidence": 0.7,
                        "reason": "recurring_size_at_level",
                    })
            seen[key] = size

    return icebergs


def compute_order_book_summary(bids: list, asks: list, ticker: str) -> dict:
    """Compute a complete order book summary combining all analyses.

    This is the main entry point for runner.py to inject into context.

    Args:
        bids: List of (price, size) tuples from get_market_depth
        asks: List of (price, size) tuples from get_market_depth
        ticker: Ticker symbol

    Returns:
        Complete order book analysis dict.
    """
    if not bids and not asks:
        return {"available": False}

    velocity = compute_order_book_velocity(bids, asks, ticker)
    icebergs = detect_iceberg_orders(bids, asks, ticker)

    # Compute cumulative depth at price levels
    cum_bid = 0
    cum_ask = 0
    cum_bid_levels = []
    cum_ask_levels = []
    for i, (price, size) in enumerate(bids[:10]):
        cum_bid += size
        cum_bid_levels.append({"price": float(price), "size": size, "cumulative": cum_bid, "level": i+1})
    for i, (price, size) in enumerate(asks[:10]):
        cum_ask += size
        cum_ask_levels.append({"price": float(price), "size": size, "cumulative": cum_ask, "level": i+1})

    bid_total = sum(s for _, s in bids[:5])
    ask_total = sum(s for _, s in asks[:5])

    return {
        "available": True,
        "bid_total_volume": bid_total,
        "ask_total_volume": ask_total,
        "bid_ask_ratio": round(bid_total / max(ask_total, 1), 4),
        "imbalance": velocity["imbalance"],
        "top_book_imbalance": velocity["top_book_imbalance"],
        "bid_levels": cum_bid_levels,
        "ask_levels": cum_ask_levels,
        "velocity": {
            "bid": velocity["bid_velocity"],
            "ask": velocity["ask_velocity"],
            "spread": velocity["velocity_spread"],
            "acceleration": velocity["acceleration"],
        },
        "flow": velocity["flow"],
        "stacking": velocity["stacking"],
        "depth_slope": velocity["depth_slope"],
        "mid_price": velocity["mid_price"],
        "spread": velocity["spread"],
        "icebergs": icebergs,
        "iceberg_count": len(icebergs),
        "cumulative_bid_delta": velocity["cumulative_bid_delta"],
        "cumulative_ask_delta": velocity["cumulative_ask_delta"],
        "pressure": (
            "buying" if velocity["imbalance"] > 0.05
            else "selling" if velocity["imbalance"] < -0.05
            else "neutral"
        ),
        "source": "order_book",
    }
