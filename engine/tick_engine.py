"""Real-time tick engine — Lee-Ready trade signing, cumulative delta, VPIN buckets.

Attaches to IBKRStreamer._on_pending_tickers and signs every STK/FUT tick.
No additional IBKR market data subscriptions required — piggybacks on existing tickers.
"""

import time
import math
from collections import deque
from threading import Lock
from typing import Optional

from utils.logger import get_logger

logger = get_logger("engine.tick_engine")

# ── Per-ticker state ──

_ticker_state: dict[str, dict] = {}
_lock = Lock()


def _empty_state() -> dict:
    return {
        "cumulative_delta": 0,
        "total_buy_vol": 0,
        "total_sell_vol": 0,
        "buy_count": 0,
        "sell_count": 0,
        "last_price": 0.0,
        "last_bid": 0.0,
        "last_ask": 0.0,
        "prev_volume": 0,
        "prev_last_price": 0.0,
        "last_trade_size": 0,
        "tick_buffer": deque(maxlen=100),           # last 100 signed ticks
        "volume_buckets": deque(maxlen=50),          # VPIN equal-volume buckets
        "bucket_buy_vol": 0,
        "bucket_sell_vol": 0,
        "bucket_target_vol": 0,
        "last_60s_trades": deque(),                  # (timestamp, signed_size)
        "last_updated": 0.0,
    }


def _sign_trade(last_price: float, bid: float, ask: float,
                prev_last_price: float) -> tuple[str, float]:
    """Lee-Ready trade signing with tick-rule fallback.

    Returns (sign, confidence) where sign is "buy" or "sell".
    confidence ∈ [0, 1] — 1.0 for unambiguous, < 1.0 for tick-rule.
    """
    if last_price <= 0:
        return "neutral", 0.0
    if bid > 0 and ask > 0 and ask > bid:
        mid = (bid + ask) / 2
        if last_price >= ask:
            return "buy", 1.0
        elif last_price <= bid:
            return "sell", 1.0
        elif last_price > mid:
            return "buy", 0.7
        elif last_price < mid:
            return "sell", 0.7
    if prev_last_price > 0:
        if last_price > prev_last_price:
            return "buy", 0.5
        elif last_price < prev_last_price:
            return "sell", 0.5
    return "neutral", 0.0


def _compute_vpin(state: dict) -> float:
    """Compute VPIN from last N equal-volume buckets."""
    buckets = list(state["volume_buckets"])
    if len(buckets) < 3:
        return 0.0
    oi_values = []
    for b in buckets:
        total = b["buy"] + b["sell"]
        if total > 0:
            oi_values.append(abs(b["buy"] - b["sell"]) / total)
    return sum(oi_values) / max(len(oi_values), 1)


def _compute_60s_delta(state: dict) -> int:
    """Sum of signed trade sizes in last 60 seconds."""
    now = time.time()
    cutoff = now - 60
    total = 0
    # Prune expired entries
    valid = []
    for ts, size, sign in state["last_60s_trades"]:
        if ts >= cutoff:
            valid.append((ts, size, sign))
            if sign == "buy":
                total += size
            elif sign == "sell":
                total -= size
    state["last_60s_trades"] = valid
    return total


def on_tick(ticker: str, sec_type: str, last_price: float, bid: float, ask: float,
            volume: int, last_size: int, timestamp: float) -> None:
    """Process a single tick from the IBKR streamer.

    Called once per tick per ticker from _on_pending_tickers.
    """
    global _ticker_state
    if sec_type not in ("STK", "FUT"):
        return
    if ticker in ("SPX", "NDX"):
        return

    with _lock:
        state = _ticker_state.get(ticker)
        if state is None:
            state = _empty_state()
            _ticker_state[ticker] = state

        # Detect trade: use lastSize if available, else infer from volume delta
        trade_size = 0
        if last_size > 0:
            trade_size = last_size
        elif volume > state["prev_volume"]:
            trade_size = volume - state["prev_volume"]

        # Sign the trade
        if trade_size > 0 and last_price > 0:
            sign, conf = _sign_trade(last_price, bid, ask, state["prev_last_price"])
            if sign == "buy":
                signed_size = trade_size
                state["total_buy_vol"] += trade_size
                state["buy_count"] += 1
                state["cumulative_delta"] += trade_size
            elif sign == "sell":
                signed_size = -trade_size
                state["total_sell_vol"] += trade_size
                state["sell_count"] += 1
                state["cumulative_delta"] -= trade_size
            else:
                signed_size = 0

            if signed_size != 0:
                state["tick_buffer"].append({
                    "time": timestamp,
                    "price": last_price,
                    "size": abs(trade_size),
                    "sign": sign,
                    "confidence": round(conf, 2),
                })
                # Track for 60s delta
                state["last_60s_trades"].append((timestamp, abs(trade_size), sign))

                # VPIN equal-volume bucket accumulation
                if state["bucket_target_vol"] == 0:
                    state["bucket_target_vol"] = max(trade_size * 50, 500)
                if sign == "buy":
                    state["bucket_buy_vol"] += trade_size
                else:
                    state["bucket_sell_vol"] += trade_size
                bucket_total = state["bucket_buy_vol"] + state["bucket_sell_vol"]
                if bucket_total >= state["bucket_target_vol"]:
                    state["volume_buckets"].append({
                        "buy": state["bucket_buy_vol"],
                        "sell": state["bucket_sell_vol"],
                        "target": state["bucket_target_vol"],
                    })
                    state["bucket_buy_vol"] = 0
                    state["bucket_sell_vol"] = 0
                    target = state["bucket_target_vol"]
                    state["bucket_target_vol"] = int(target * 0.9 + bucket_total * 0.1)

            state["last_trade_size"] = trade_size

        # Always update quote state
        if last_price > 0:
            state["prev_last_price"] = state["last_price"]
            state["last_price"] = last_price
        if bid > 0:
            state["last_bid"] = bid
        if ask > 0:
            state["last_ask"] = ask
        if volume > 0:
            state["prev_volume"] = volume
        state["last_updated"] = timestamp


def get_tick_stats(ticker: str) -> dict:
    """Thread-safe read of per-ticker tick statistics.

    Returns dict with cumulative delta, buy/sell breakdown, VPIN, etc.
    Returns empty dict if no tick data has been collected.
    """
    with _lock:
        state = _ticker_state.get(ticker)
        if state is None:
            return {}

        delta_60s = _compute_60s_delta(state)
        vpin = _compute_vpin(state)

        total_vol = state["total_buy_vol"] + state["total_sell_vol"]
        avg_trade_size = 0
        if state["buy_count"] + state["sell_count"] > 0:
            avg_trade_size = total_vol / max(state["buy_count"] + state["sell_count"], 1)

        trade_imb = 0
        if state["buy_count"] + state["sell_count"] > 0:
            trade_imb = (state["buy_count"] - state["sell_count"]) / max(state["buy_count"] + state["sell_count"], 1)

        vol_imb = 0
        if total_vol > 0:
            vol_imb = (state["total_buy_vol"] - state["total_sell_vol"]) / max(total_vol, 1)

        return {
            "cumulative_delta": state["cumulative_delta"],
            "delta_60s": delta_60s,
            "total_buy_vol": state["total_buy_vol"],
            "total_sell_vol": state["total_sell_vol"],
            "buy_count": state["buy_count"],
            "sell_count": state["sell_count"],
            "total_volume": total_vol,
            "avg_trade_size": round(avg_trade_size, 1),
            "trade_imbalance": round(trade_imb, 4),
            "volume_imbalance": round(vol_imb, 4),
            "vpin": round(vpin, 4),
            "last_price": state["last_price"],
            "last_bid": state["last_bid"],
            "last_ask": state["last_ask"],
            "last_trade_size": state["last_trade_size"],
            "last_updated": state["last_updated"],
            "source": "tick_engine",
        }


def get_vpin(ticker: str) -> float:
    """Quick read of just the VPIN value."""
    stats = get_tick_stats(ticker)
    return stats.get("vpin", 0.0)


def reset():
    """Clear all accumulated tick state."""
    global _ticker_state
    with _lock:
        _ticker_state = {}
    logger.debug("tick_engine: state reset")
