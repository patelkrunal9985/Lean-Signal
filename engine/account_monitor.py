"""
Account Monitor — IBKR account integration for risk-aware signal gating.

Provides:
  - Real-time account summary (buying power, excess liquidity, P&L)
  - Open positions per ticker
  - Daily P&L tracking with loss limit circuit breaker
  - Position-aware consensus: reduce conviction of new signals in same
    direction as existing positions
  - Buying power check before signal generation

All data from IBKR reqAccountSummary + reqPositions.  No external sources.
"""
from __future__ import annotations

import threading
import time
from datetime import date
from typing import Optional

from utils.logger import get_logger

logger = get_logger("engine.account_monitor")

# Daily loss limit as % of prior day's closing equity
DAILY_LOSS_LIMIT_PCT = 0.03  # 3% max daily loss

# Position overlap threshold: if existing position > 50% of buying power,
# reduce new signal confidence
POSITION_OVERLAP_THRESHOLD = 0.50


_account_cache: dict = {}
_account_cache_ts: float = 0
_ACCOUNT_CACHE_TTL = 5.0  # 5-second cache
_account_lock = threading.Lock()
_account_daily_pnl: float = 0.0
_account_starting_equity: float = 0.0
_account_date: Optional[date] = None
_consecutive_losses: int = 0


def reset_daily_pnl():
    """Reset daily P&L and loss streak tracking at market open."""
    global _account_daily_pnl, _account_date, _consecutive_losses
    _account_date = date.today()
    _account_daily_pnl = 0.0
    _consecutive_losses = 0


def fetch_account_summary() -> dict:
    """Fetch account summary from IBKR via reqAccountSummary.

    Thread-safe, cached for _ACCOUNT_CACHE_TTL seconds.

    Returns dict with:
      - total_cash_value, buying_power, excess_liquidity,
        gross_position_value, realized_pnl, unrealized_pnl,
        daily_pnl, available_funds, equity_with_loan
    """
    global _account_cache, _account_cache_ts, _account_date, _account_daily_pnl, _account_starting_equity

    now = time.time()
    with _account_lock:
        if _account_cache and (now - _account_cache_ts) < _ACCOUNT_CACHE_TTL:
            return dict(_account_cache)

    streamer = _get_streamer()
    if not streamer:
        return {}

    result_container: list = [None]
    event = threading.Event()

    async def _do_fetch():
        nonlocal result_container
        try:
            summary = await streamer._ib.reqAccountSummaryAsync()
            if summary:
                acct = summary[0]
                total_cash = float(getattr(acct, 'TotalCashValue', '0') or 0)
                buying_power = float(getattr(acct, 'BuyingPower', '0') or 0)
                excess_liquidity = float(getattr(acct, 'ExcessLiquidity', '0') or 0)
                gross_pos = float(getattr(acct, 'GrossPositionValue', '0') or 0)
                realized_pnl = float(getattr(acct, 'RealizedPnL', '0') or 0)
                unrealized_pnl = float(getattr(acct, 'UnrealizedPnL', '0') or 0)
                equity = float(getattr(acct, 'EquityWithLoanValue', '0') or 0)
                available = float(getattr(acct, 'AvailableFunds', '0') or 0)

                # Track daily P&L
                today = date.today()
                if _account_date != today:
                    _account_starting_equity = equity
                    _account_date = today
                    _account_daily_pnl = 0.0

                # Daily P&L = change in equity from session start
                if _account_starting_equity > 0:
                    _account_daily_pnl = equity - _account_starting_equity
                    if _account_daily_pnl < 0 and abs(_account_daily_pnl) > abs(equity * 0.005):
                        _consecutive_losses += 1
                    elif _account_daily_pnl >= 0:
                        _consecutive_losses = 0

                result = {
                    "total_cash_value": total_cash,
                    "buying_power": buying_power,
                    "excess_liquidity": excess_liquidity,
                    "gross_position_value": gross_pos,
                    "realized_pnl": realized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "daily_pnl": round(_account_daily_pnl, 2),
                    "equity_with_loan": equity,
                    "available_funds": available,
                    "daily_loss_limit_hit": _account_daily_pnl < -abs(equity * DAILY_LOSS_LIMIT_PCT) if equity > 0 else False,
                    "daily_loss_limit_pct": round(abs(_account_daily_pnl) / max(equity, 1) * 100, 2) if equity > 0 else 0,
                    "timestamp": time.time(),
                    "source": "ibkr",
                }
                result_container[0] = result
        except Exception as e:
            logger.debug(f"Account summary fetch failed: {e}")
        finally:
            event.set()

    try:
        from engine.ibkr_data_feed import get_streamer
        s = get_streamer()
        if s and s._ib and s._loop:
            import asyncio
            asyncio.run_coroutine_threadsafe(_do_fetch(), s._loop)
            event.wait(timeout=10)
    except Exception as e:
        logger.debug(f"Account summary dispatch failed: {e}")

    result = result_container[0] or {}
    with _account_lock:
        _account_cache = result
        _account_cache_ts = now
    return result


def fetch_positions() -> list[dict]:
    """Fetch open positions from IBKR via reqPositions.

    Thread-safe, returns list of dicts with:
      - ticker, position, avg_cost, market_price, market_value,
        unrealized_pnl, realized_pnl, instrument_type
    """
    streamer = _get_streamer()
    if not streamer:
        return []

    result_container: list = [None]
    event = threading.Event()

    async def _do_fetch():
        nonlocal result_container
        try:
            positions = await streamer._ib.reqPositionsAsync()
            result = []
            for pos in positions:
                contract = pos.contract
                ticker = contract.symbol
                sec_type = contract.secType
                result.append({
                    "ticker": ticker,
                    "instrument_type": "future" if sec_type == "FUT" else "stock" if sec_type == "STK" else "option",
                    "position": int(pos.position),
                    "avg_cost": float(pos.avgCost),
                    "market_price": float(pos.marketPrice),
                    "market_value": float(pos.marketValue),
                    "unrealized_pnl": float(pos.unrealizedPNL),
                    "realized_pnl": float(pos.realizedPNL),
                })
            result_container[0] = result
        except Exception as e:
            logger.debug(f"Positions fetch failed: {e}")
        finally:
            event.set()

    try:
        import asyncio
        asyncio.run_coroutine_threadsafe(_do_fetch(), streamer._loop)
        event.wait(timeout=10)
    except Exception as e:
        logger.debug(f"Positions dispatch failed: {e}")

    return result_container[0] or []


def get_position_summary(positions: list[dict]) -> dict:
    """Summarize open positions into a per-ticker direction map.

    Returns:
        Dict with:
          - ticker -> {direction: long/short, size, pnl, pct_of_bp}
          - total_long_exposure, total_short_exposure
          - position_count, direction_skew
    """
    if not positions:
        return {"position_count": 0, "direction_skew": 0, "positions": {}}

    by_ticker = {}
    total_long = 0
    total_short = 0
    for pos in positions:
        ticker = pos["ticker"]
        qty = pos["position"]
        direction = "long" if qty > 0 else "short" if qty < 0 else "flat"
        mv = abs(pos["market_value"])
        by_ticker[ticker] = {
            "direction": direction,
            "size": abs(qty),
            "market_value": mv,
            "unrealized_pnl": pos["unrealized_pnl"],
            "avg_cost": pos["avg_cost"],
            "instrument_type": pos["instrument_type"],
        }
        if direction == "long":
            total_long += mv
        elif direction == "short":
            total_short += mv

    total = total_long + total_short
    skew = (total_long - total_short) / max(total, 1) if total > 0 else 0

    return {
        "positions": by_ticker,
        "total_long_exposure": total_long,
        "total_short_exposure": total_short,
        "position_count": len(positions),
        "direction_skew": round(skew, 4),
    }


def check_signal_blockers(
    signal_ticker: str,
    signal_direction: str,
    signal_instrument: str,
    account: dict,
    positions: dict,
) -> tuple[bool, str]:
    """Check if account state should block or reduce a signal.

    Returns (blocked, reason).
    If not blocked, signal can proceed (possibly with reduced confidence).

    Rules:
      1. Daily loss limit hit → block all signals
      2. Existing position in same ticker & same direction → reduce (not block)
      3. Existing position in same ticker & opposite direction → block (hedging?)
      4. Buying power too low → block
      5. Excess liquidity below threshold → block
    """
    if not account:
        return False, "no_account_data"

    # Rule 0: Consecutive losing cycles detection
    if _consecutive_losses >= 3:
        return True, f"consecutive_losses_{_consecutive_losses}"

    # Rule 1: Daily loss limit
    if account.get("daily_loss_limit_hit", False):
        return True, "daily_loss_limit_hit"

    # Rule 4: Buying power too low
    bp = account.get("buying_power", 0)
    if bp < 1000:  # $1k minimum for futures
        return True, f"buying_power_too_low_{bp}"

    # Rule 5: Excess liquidity
    excess = account.get("excess_liquidity", 0)
    if excess < 500:
        return True, f"excess_liquidity_too_low_{excess}"

    # Position overlap
    if positions and "positions" in positions:
        existing = positions["positions"].get(signal_ticker)
        if existing:
            existing_dir = existing["direction"]
            # Rule 3: Opposite direction → likely a hedge, let it pass
            if existing_dir == signal_direction:
                return False, "existing_position_same_direction_reduce_confidence"
            elif existing_dir != "flat" and existing_dir != signal_direction:
                return True, "existing_position_opposite_direction"

    return False, "ok"


def _get_streamer():
    try:
        from engine.ibkr_data_feed import get_streamer
        s = get_streamer()
        if s and s._ib and s._ib.isConnected() and s._loop:
            return s
    except Exception:
        pass
    return None
