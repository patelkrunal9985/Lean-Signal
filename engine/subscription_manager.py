"""
Subscription Manager — IBKR slot allocation with priority scoring.

Every cycle, every ticker is scored. Fixed tickers (SPY, QQQ, SPX,
all futures, their nearest-expiry ATM options) are always subscribed.
Dynamic slots go to the highest-scored tickers by type.

No fallback to yfinance/TV — if IBKR has no slot for a ticker, it
gets stale prices and generates no signals.
"""
import threading
from typing import Optional

import sys
from kronos.utils.logger import get_logger

logger = get_logger("kronos.skills.subscription_manager")

# ── Slot limits by instrument type ────────────────────────────────
# Slot allocation optimized: futures need 3+ dynamic slots for next-month contracts
# (carry_yield, calendar_spread, vix_term_structure all require next-month prices).
# Reduced option slots from 77→74 to free 3 slots for futures.
# Fixed tickers: 4 stocks + 13 futures = 17 lines
# Options: 83 lines / 3 underlyings = 27 per underlying = 13 strikes each (calls+puts)
# With Quote Booster: 183 lines / 3 = 61 per = 30 strikes each
SLOT_LIMITS = {"stock": 8, "future": 17, "option": 74}
TOTAL_SLOTS = 100
OPTION_LINES_PER_UNDERLYING = 27  # adjustable when booster added
SPARE = 1
SLOT_WARN_THRESHOLD = 0.80  # warn when any type exceeds 80% capacity

def seed_fixed_options():
    """Seed SPY/QQQ/SPX nearest-expiry ATM call+put at engine init.

    Called once at startup (before first V3 options scan) so fixed option
    slots are populated immediately and IBKR market data flows for these
    critical tickers from cycle 1. Falls back gracefully if IBKR is not
    connected or the chain fetch fails.
    """
    try:
        from engine.ibkr_data_feed import fetch_option_chain_ibkr
        from datetime import datetime
        now = datetime.now()
        for ticker in FIXED_STOCKS:
            try:
                chain = fetch_option_chain_ibkr(ticker)
                if not chain or not chain.get("calls"):
                    continue
                # Find nearest expiry with >= 2 DTE
                expirations = chain.get("expirations", [])
                best_exp = ""
                best_dte = 9999
                for exp_str in expirations:
                    try:
                        exp_clean = exp_str.replace("-", "").replace("/", "")[:8]
                        exp_date = datetime.strptime(exp_clean, "%Y%m%d")
                        dte = (exp_date - now).days
                        if 2 <= dte < best_dte:
                            best_dte = dte
                            best_exp = exp_str
                    except Exception:
                        pass
                if not best_exp:
                    continue
                # Find ATM strike closest to current price
                # Use the first call strike nearest to middle as ATM proxy
                calls = chain.get("calls", [])
                if not calls:
                    continue
                # Use median strike as ATM proxy when live price unknown
                strikes = sorted([c.get("strike", 0) for c in calls if c.get("strike", 0) > 0])
                if not strikes:
                    continue
                atm_strike = strikes[len(strikes) // 2]
                # Register both call and put at priority 10000 (pinned)
                # set_priority defined in this module, called directly
                for right in ("C", "P"):
                    set_priority(ticker, "option", 10000,
                        underlying=ticker, expiry=best_exp,
                        strike=atm_strike, right=right, pinned=True)
                logger.info("Seeded fixed options: %s exp=%s strike=%.2f dte=%d",
                    ticker, best_exp, atm_strike, best_dte)
            except Exception as e:
                logger.debug("seed_fixed_options: %s failed: %s", ticker, e)
    except Exception as e:
        logger.warning("seed_fixed_options: IBKR chain fetch failed: %s", e)




# ── Module-level slot usage tracking ──
_slot_usage: dict[str, int] = {"stock": 0, "future": 0, "option": 0}
_last_slot_warning: float = 0.0  # rate-limit warnings to once per 60s

# ── Fixed tickers: never evicted, never scored ────────────────────
FIXED_STOCKS = frozenset({"SPY", "QQQ", "SPX", "NDX"})
FIXED_FUTURES = frozenset({
    "ES=F", "NQ=F", "YM=F", "RTY=F",
    "MES=F", "MNQ=F", "MYM=F", "M2K=F", "MGC=F", "MCL=F",
    "GC=F", "CL=F", "VX=F",
})

# ── Module-level state (thread-safe) ──────────────────────────────
_priorities: dict[str, dict] = {}        # ticker_key -> {score, instr_type, meta}
_pinned: set[str] = set()                # tickers pinned by open positions
_lock = threading.Lock()
_prev_subscribed: set[str] = set()       # tickers subscribed in last cycle


def _option_key(ticker: str, expiry: str, strike: float, right: str) -> str:
    """Normalize option key for internal tracking."""
    exp = expiry.replace("-", "").replace("/", "")
    return f"{ticker.upper()}_{exp}_{strike}_{right.upper()}"


def set_priority(
    ticker: str,
    instr_type: str,
    score: float,
    *,
    underlying: Optional[str] = None,
    expiry: Optional[str] = None,
    strike: Optional[float] = None,
    right: Optional[str] = None,
    pinned: bool = False,
):
    """Record a priority score for a ticker.

    Called by engine cycle end, API handlers, engine init.

    For options, pass (underlying, expiry, strike, right) so the
    manager can build a normalized key.  Pinned tickers (open positions)
    are never evicted.
    """
    key = ticker
    meta = {"instr_type": instr_type}
    if instr_type == "option":
        if underlying and expiry and strike is not None and right:
            key = _option_key(underlying, expiry, strike, right)
            meta = {"underlying": underlying.upper(), "expiry": expiry,
                    "strike": strike, "right": right.upper()}
    with _lock:
        _priorities[key] = {"score": score, **meta}
        if pinned:
            _pinned.add(key)


def get_current_subscribed() -> list[str]:
    """Return the list of currently IBKR-subscribed tickers (for diagnostics)."""
    with _lock:
        return sorted(_prev_subscribed)


def refresh() -> dict:
    """Rank all scored tickers, return delta for IBKR (un)subscribe.

    Returns:
        {"to_subscribe": [...], "to_cancel": [...]}
    """
    global _prev_subscribed, _priorities, _pinned
    with _lock:
        priorities = dict(_priorities)
        pinned = set(_pinned)
        prev = set(_prev_subscribed)

    # ── 1. Build fixed tickers ──
    to_keep: set[str] = set()
    for t in FIXED_STOCKS:
        to_keep.add(t)
    for t in FIXED_FUTURES:
        to_keep.add(t)

    # ── 2. Fixed options: SPY/QQQ/SPX nearest-expiry ATM call+put ──
    fixed_opts = _resolve_fixed_options(priorities)
    to_keep.update(fixed_opts)
    # Remove fixed option priorities from dynamic pool
    for k in fixed_opts:
        priorities.pop(k, None)

    # ── 3. Separate pinned (holdings) from scored ──
    # Pinned tickers always stay
    for k in pinned:
        to_keep.add(k)
        priorities.pop(k, None)

    # ── 4. Rank remaining by type ──
    by_type: dict[str, list[tuple[str, float]]] = {
        "stock": [], "future": [], "option": []
    }
    for key, data in priorities.items():
        instr = data.get("instr_type", "stock")
        score = data.get("score", 0)
        by_type.setdefault(instr, []).append((key, score))

    # Sort desc by score within each type, take top N
    dynamic_slots = {
        "stock": SLOT_LIMITS["stock"] - len(FIXED_STOCKS),
        "future": SLOT_LIMITS["future"] - len(FIXED_FUTURES),
        "option": SLOT_LIMITS["option"] - len(fixed_opts),
    }
    # Count pinned by type to reduce dynamic slots
    for k in pinned:
        data = _priorities.get(k, {})
        instr = data.get("instr_type", "stock")
        dynamic_slots[instr] = max(0, dynamic_slots[instr] - 1)

    for instr, items in by_type.items():
        items.sort(key=lambda x: -x[1])  # highest score first
        limit = dynamic_slots.get(instr, 0)
        for key, _ in items[:limit]:
            to_keep.add(key)

    # ── 5. Compute delta ──
    to_add = to_keep - prev
    to_cancel = prev - to_keep

    msg = f"SubscriptionManager: {len(to_keep)} slots used"
    # ── Slot usage monitoring ──
    _update_slot_usage(to_keep, by_type)
    health = check_slot_health()
    if health["warnings"]:
        for w in health["warnings"]:
            logger.warning(w)
    if to_add:
        msg += f", +{len(to_add)} new"
    if to_cancel:
        msg += f", {len(to_cancel)} evicted"
    logger.info(msg)
    if to_cancel:
        logger.debug(f"  Evicted: {sorted(to_cancel)[:10]}{'...' if len(to_cancel)>10 else ''}")

    with _lock:
        _prev_subscribed = to_keep

    return {"to_subscribe": list(to_add), "to_cancel": list(to_cancel)}


def _update_slot_usage(to_keep: set[str], by_type: dict):
    """Update internal slot usage counters from current subscription state."""
    counts = {"stock": 0, "future": 0, "option": 0}
    for key in to_keep:
        for instr_type in ("option", "future", "stock"):
            # Options have _ in key (composite key pattern), futures end with =F
            if "_" in key and instr_type == "option":
                counts["option"] += 1
                break
            elif key.endswith("=F") and instr_type == "future":
                counts["future"] += 1
                break
            elif instr_type == "stock":
                counts["stock"] += 1
                break
    global _slot_usage
    with _lock:
        _slot_usage = counts


def get_slot_usage() -> dict:
    """Return current IBKR slot usage by instrument type.
    
    Returns dict with keys: stock, future, option (each with used, limit, pct).
    """
    with _lock:
        counts = dict(_slot_usage)
    result = {}
    for instr_type in ("stock", "future", "option"):
        used = counts.get(instr_type, 0)
        limit = SLOT_LIMITS[instr_type]
        result[instr_type] = {
            "used": used,
            "limit": limit,
            "pct": round(used / max(limit, 1), 4),
        }
    result["total_used"] = sum(c["used"] for c in result.values())
    result["total_limit"] = TOTAL_SLOTS
    return result


def check_slot_health() -> dict:
    """Check slot usage health and return warnings if near limits.
    
    Rate-limited to one warning per type per 60s.
    Returns dict with {warnings: [str], healthy: bool}.
    """
    import time
    global _last_slot_warning
    now = time.time()
    usage = get_slot_usage()
    warnings = []
    for instr_type in ("stock", "future", "option"):
        info = usage[instr_type]
        if info["pct"] >= SLOT_WARN_THRESHOLD:
            if now - _last_slot_warning > 60:
                warnings.append(
                    f"IBKR slot warning: {instr_type} at {info['used']}/{info['limit']} "
                    f"({info['pct']*100:.0f}%) — approaching slot limit"
                )
    if warnings:
        _last_slot_warning = now
    return {"warnings": warnings, "healthy": len(warnings) == 0}


def _resolve_fixed_options(priorities: dict) -> set[str]:
    """Find nearest-expiry ATM call+put for SPY, QQQ, SPX from priority data.

    Uses option-specific meta (underlying, expiry, strike, right) that was
    stored via set_priority() calls from the options scan or engine.

    When priorities is empty at startup (before first V3 options scan),
    returns empty set for that ticker — fixed option slots go unused
    until the first scan populates them.
    
    Returns up to 6 option keys (2 per fixed stock ticker).
    """
    result: set[str] = set()
    for underlying in FIXED_STOCKS:
        # Collect all option entries for this underlying
        candidates = []
        for key, data in priorities.items():
            if data.get("underlying") == underlying:
                candidates.append(data)
        if not candidates:
            continue
        # Pick the one with nearest expiry (smallest DTE)
        from datetime import datetime
        now = datetime.now()
        best = None
        best_dte = 9999
        for c in candidates:
            exp_str = c.get("expiry", "")
            try:
                exp_date = datetime.strptime(exp_str.replace("-", "").replace("/", "")[:8], "%Y%m%d")
                dte = (exp_date - now).days
                if 2 <= dte < best_dte:
                    best_dte = dte
                    best = c
            except Exception:
                pass
        if best:
            exp = best.get("expiry", "")
            strike = best.get("strike", 0)
            # Only subscribe rights that actually exist in the priority data.
            # This prevents synthesizing phantom put subscriptions when only
            # call data was scored (e.g., bullish P/C ratio in PASS 2).
            available_rights = set()
            for c in candidates:
                r = c.get("right", "")
                if r and abs(c.get("strike", 0) - strike) < 0.01:
                    available_rights.add(r)
            if not available_rights:
                available_rights = {"C", "P"}  # fallback: subscribe both
            for right in available_rights:
                result.add(_option_key(underlying, exp, strike, right))
    return result


def sync_from_ibkr(subscribed: set[str]):
    """Sync internal _prev_subscribed state from IBKR's actual subscriptions.

    Call once on startup (before the first refresh()) so the manager knows
    which tickers are already subscribed to IBKR.  This prevents the manager
    from keeping phantom subscriptions alive across restarts — every
    subsequent refresh() will correctly compute evictions.

    Thread-safe.  Idempotent — safe to call multiple times.
    """
    with _lock:
        _prev_subscribed = set(subscribed or set())
        logger.info(
            "SubscriptionManager synced from IBKR: %d tickers",
            len(_prev_subscribed),
        )
        # Also print to stderr so the message is visible in the main server log
        # (the file handler may not be set up yet at startup time).
        print(
            f"[SubscriptionManager] synced from IBKR: {len(_prev_subscribed)} tickers",
            file=sys.stderr, flush=True,
        )


def reset():
    """Clear all priorities and pinned set (for testing/cleanup)."""
    with _lock:
        _priorities.clear()
        _pinned.clear()
        _prev_subscribed.clear()


def reset_non_pinned():
    """Clear non-pinned priorities at cycle start.

    Pinned tickers (open positions) persist across cycles so they
    never lose their IBKR slot.  All signal/scan/API scores from
    the previous cycle are discarded — each cycle starts fresh.
    Thread-safe.
    """
    with _lock:
        to_remove = [k for k in _priorities if k not in _pinned]
        for k in to_remove:
            del _priorities[k]


def get_slot_summary() -> dict:
    """Return current slot budget usage summary for diagnostics/monitoring.

    Returns dict with by_type breakdown, fixed/pinned counts, limits.
    """
    with _lock:
        by_type: dict[str, int] = {"stock": 0, "future": 0, "option": 0, "other": 0}
        for key, info in _priorities.items():
            t = info.get("instr_type", "other")
            by_type[t] = by_type.get(t, 0) + 1
        fixed_stocks = len(FIXED_STOCKS)
        fixed_futures = len(FIXED_FUTURES)
        pinned = len(_pinned)
        prev = len(_prev_subscribed)
    return {
        "by_type": by_type,
        "fixed_stocks": fixed_stocks,
        "fixed_futures": fixed_futures,
        "pinned": pinned,
        "limits": dict(SLOT_LIMITS),
        "total_limit": TOTAL_SLOTS,
        "total_fixed": fixed_stocks + fixed_futures,
        "total_scored": sum(by_type.values()),
        "prev_subscribed": prev,
    }


def unpin_ticker(ticker: str, *, underlying: Optional[str] = None,
                 expiry: Optional[str] = None, strike: Optional[float] = None,
                 right: Optional[str] = None):
    """Remove a ticker from pinned set and priorities, freeing its IBKR slot.

    Called when a position closes so the slot is available for the next signal.
    Without this, closed positions leak slots until the next server restart.

    For option positions, pass (underlying, expiry, strike, right) so the
    normalized key matches how set_priority stored it.
    """
    key = ticker
    if underlying and expiry and strike is not None and right:
        key = _option_key(underlying, expiry, strike, right)
    with _lock:
        _pinned.discard(key)
        _priorities.pop(key, None)
