"""
Kronos Trading System - IBKR (Interactive Brokers) Data Feed
Provides real-time market data via TWS/IB Gateway using ib_insync.
Runs as a background daemon thread with auto-reconnect.
"""
import asyncio
import math
import random
import re
import threading
import time
import traceback
from datetime import date, datetime
from typing import Optional

from utils.logger import get_logger
from utils.config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID

logger = get_logger("engine.skills.ibkr_data_feed")

# ── Shared live price cache (thread-safe, simple dict) ──
# Updated in real-time by the IBKR streamer callbacks.
# Structure: { "TICKER": LiveQuote }
_live_prices: dict = {}
_live_prices_lock = threading.Lock()
_streamer_lock = threading.Lock()
_connected: bool = False
_connected_at: float = 0.0  # time.time() of last connect, used to delay until TWS is ready
_subscribed_tickers: set = set()

# ── Live option price cache (thread-safe) ──
# Structure: { "TICKER_EXPIRY_STRIKE_RIGHT": LiveOptionQuote }
# Populated by _on_pending_tickers when OPT contracts arrive,
# and by fetch_live_option_prices for on-demand snapshots.
_live_option_prices: dict = {}

# P3.2: Streamer heartbeat — updated on every tick, checked by runner
_last_tick_time: float = 0.0
_last_tick_lock = threading.Lock()

# ── Contract Validation Cache (prevents repeated 321 errors) ──
# Valid contracts cached for 24h, invalid cached for 1h
_contract_cache: dict = {}           # cache_key -> qualified_contract
_contract_cache_ts: dict = {}        # cache_key -> timestamp
_invalid_contract_cache: set = set() # contract keys that returned 321
_INVALID_CACHE_TS: dict = {}         # cache_key -> timestamp for invalid cache
_CONTRACT_CACHE_TTL = 86400          # 24h
_INVALID_CACHE_TTL = 3600            # 1h

# Micro futures have smaller contract sizes (1/10th or 1/2 of regular).
# They need tighter sanity gates to catch IBKR tick glitches like
# the MYM 400-point bump bug (1% deviation passes 15% gate unnoticed).
_MICRO_FUTURE_SYMBOLS = {"MCL", "MGC"}
_BLOCKED_FUTURE_SYMBOLS = {"MES", "MNQ", "MYM", "M2K"}


def _is_micro_future(ticker_or_key: str) -> bool:
    """Check if a ticker or cache key corresponds to a micro futures contract."""
    return ticker_or_key.replace("=F", "").strip() in _MICRO_FUTURE_SYMBOLS


def _is_blocked_future(ticker_or_key: str) -> bool:
    """Check if a ticker is a removed micro futures contract that should be ignored."""
    return ticker_or_key.replace("=F", "").strip() in _BLOCKED_FUTURE_SYMBOLS


def _validate_futures_contract(contract) -> tuple[bool, str]:
    """Return (is_valid, reason). Pre-qualify before calling IBKR."""
    if not contract.lastTradeDateOrContractMonth:
        return False, "NO_EXPIRY"
    try:
        expiry = datetime.strptime(contract.lastTradeDateOrContractMonth, "%Y%m").date()
        if expiry < date.today():
            return False, "EXPIRED"
    except ValueError:
        return False, "INVALID_EXPIRY_FORMAT"
    valid_exchanges = {"CME", "CBOT", "NYMEX", "COMEX", "ICE", "EUREX", "CFE"}
    if contract.exchange and contract.exchange not in valid_exchanges:
        return False, f"INVALID_EXCHANGE_{contract.exchange}"
    return True, "OK"


# ── Futures with pre-month expiry (expire ~20th of the month PRIOR to contract label) ──
# e.g., CLQ6 (August 2026) expires ~July 20; GCQ6 (August 2026) expires ~July 28
_PRE_MONTH_EXPIRY_FUTURES = {"CL", "MCL", "GC", "MGC"}


def _is_near_expiry(symbol: str, expiry_yyyymm: str) -> bool:
    """Check if a futures contract is within 2 days of estimated expiry.

    For pre-month expiry futures (CL/MCL/GC/MGC), the contract expires
    ~20th of the month BEFORE its label month. For all others, the
    contract expires mid-month of its label month.

    Returns True if the contract should be skipped (near/at expiry).
    """
    today = date.today()
    try:
        expiry_year = int(expiry_yyyymm[:4])
        expiry_month = int(expiry_yyyymm[4:6])
        if symbol in _PRE_MONTH_EXPIRY_FUTURES:
            if expiry_month == 1:
                est_expiry = date(expiry_year - 1, 12, 20)
            else:
                est_expiry = date(expiry_year, expiry_month - 1, 20)
        else:
            est_expiry = date(expiry_year, expiry_month, 15)
        return (today - est_expiry).days >= -2
    except ValueError:
        return False


def _validate_option_params(symbol: str, expiry: str, strike: float, right: str) -> tuple[bool, str]:
    if not expiry or len(expiry) != 8:
        return False, "INVALID_EXPIRY_FORMAT"
    try:
        exp_date = datetime.strptime(expiry, "%Y%m%d").date()
        if exp_date < date.today():
            return False, "EXPIRED"
    except ValueError:
        return False, "INVALID_EXPIRY_FORMAT"
    if strike <= 0:
        return False, f"INVALID_STRIKE_{strike}"
    if right.upper() not in ("C", "P"):
        return False, f"INVALID_RIGHT_{right}"
    return True, "OK"


def _get_cache_key(contract) -> str:
    """Generate stable cache key from contract."""
    return f"{contract.symbol}:{contract.secType}:{contract.exchange}:{contract.lastTradeDateOrContractMonth}:{contract.strike}:{contract.right}"


def _cache_get(key: str):
    """Get cached valid contract if not expired."""
    ts = _contract_cache_ts.get(key)
    if ts and time.time() - ts < _CONTRACT_CACHE_TTL:
        return _contract_cache.get(key)
    return None


def _cache_set(key: str, contract):
    _contract_cache[key] = contract
    _contract_cache_ts[key] = time.time()


def _is_invalid_cached(key: str) -> bool:
    """Check if contract is in invalid cache and not expired."""
    ts = _INVALID_CACHE_TS.get(key)
    if ts and time.time() - ts < _INVALID_CACHE_TTL:
        return True
    elif ts and time.time() - ts >= _INVALID_CACHE_TTL:
        # Expired - remove from invalid cache to allow retry
        _invalid_contract_cache.discard(key)
        _INVALID_CACHE_TS.pop(key, None)
    return False


def _cache_invalid(key: str):
    _invalid_contract_cache.add(key)
    _INVALID_CACHE_TS[key] = time.time()


def _cleanup_caches():
    """Remove expired cache entries."""
    now = time.time()
    expired = [k for k, ts in _contract_cache_ts.items() if now - ts > _CONTRACT_CACHE_TTL]
    for k in expired:
        _contract_cache.pop(k, None)
        _contract_cache_ts.pop(k, None)
    # Clean invalid cache
    expired_inv = [k for k, ts in _INVALID_CACHE_TS.items() if now - ts > _INVALID_CACHE_TTL]
    for k in expired_inv:
        _invalid_contract_cache.discard(k)
        _INVALID_CACHE_TS.pop(k, None)


class IBKRStreamer:
    """Background thread that connects to TWS/IB Gateway and streams real-time prices."""

    def __init__(self, host: str = None, port: int = None, client_id: int = None):
        self.host = host or IBKR_HOST
        self.port = port or IBKR_PORT
        self.client_id = client_id or IBKR_CLIENT_ID
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ib = None  # ib_insync.IB instance
        self._running = False
        self._reconnect_delay = 1.0  # starts at 1s, max 60s
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def is_connected(self) -> bool:
        global _connected
        return _connected

    @property
    def subscribed_count(self) -> int:
        return len(_subscribed_tickers)

    def start(self):
        if self._running:
            logger.info("IBKR streamer already running")
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ibkr-streamer")
        self._thread.start()
        logger.info(f"IBKR streamer started (host={self.host}, port={self.port}, client_id={self.client_id})")

    def stop(self):
        self._running = False
        self._stop_event.set()
        self._disconnect()
        logger.info("IBKR streamer stopped")

    def update_subscriptions(self, tickers: list[str]):
        """Update the list of subscribed tickers. Only re-subscribes if changed."""
        global _subscribed_tickers
        new_set = {t.upper().strip() for t in tickers if t and t.strip() and not _is_blocked_future(t)}
        with _live_prices_lock:
            if new_set == _subscribed_tickers:
                return
            _subscribed_tickers = new_set
        if self.is_connected and self._ib and self._loop and not self._loop.is_closed():
            logger.info(f"IBKR subscribing to {len(new_set)} tickers: {sorted(new_set)}")
            asyncio.run_coroutine_threadsafe(self._async_resubscribe_all(), self._loop)
        else:
            logger.debug(f"update_subscriptions: skip (connected={self.is_connected}, ib={bool(self._ib)}, loop_alive={self._loop and not self._loop.is_closed()})")

    def _run_loop(self):
        loop_count = 0
        consecutive_failures = 0
        while self._running and not self._stop_event.is_set():
            try:
                self._connect_and_stream()
                # Connected and streamed successfully — reset failure counter.
                # Use connected duration to detect "insta-crash" where the
                # socket connects but self._ib.run() fails immediately.
                connected_duration = time.time() - _connected_at if _connected_at > 0 else 0
                if connected_duration > 10:
                    consecutive_failures = 0
                else:
                    consecutive_failures = max(consecutive_failures, 1)
            except Exception as e:
                consecutive_failures += 1
                # Full traceback only for first 3 failures; after that it's noise
                if consecutive_failures <= 3:
                    tb = traceback.format_exc()
                    logger.warning(f"IBKR connection error (attempt {consecutive_failures}): {e}\n{tb}")
                else:
                    logger.warning(f"IBKR connection error (attempt {consecutive_failures}): {type(e).__name__}")
            if self._running and not self._stop_event.is_set():
                # Cleanup caches periodically
                loop_count += 1
                if loop_count % 10 == 0:
                    _cleanup_caches()
                # Switch to 5-min cooldown after 10 consecutive failures
                if consecutive_failures >= 10:
                    delay = 300.0
                    self._reconnect_delay = 300.0
                else:
                    delay = min(self._reconnect_delay, 120.0)
                    self._reconnect_delay = min(self._reconnect_delay * 2, 120.0)
                logger.info(f"IBKR reconnecting in {delay:.0f}s...")
                self._stop_event.wait(delay)

    def _connect_and_stream(self):
        global _connected, _connected_at
        # Use a random client ID offset on each reconnect to avoid
        # conflicts with stale sessions in the Gateway.
        effective_client_id = self.client_id + random.Random(self.client_id).randint(0, 31)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            from ib_insync import IB, Contract, Stock, Future
            self._ib = IB()
            self._ib.connect(self.host, self.port, clientId=effective_client_id)
            # Monkey-patch reqMktData to strip bL/236 from genericTickList
            # TWS 10+ auto-adds bL to option requests, but snapshot mode rejects it
            _orig_req = self._ib.client.reqMktData
            def _patched_req(reqId, contract, genericTickList, snapshot, regulatorySnapshot, mktDataOptions):
                if 'bL' in genericTickList or '236' in genericTickList:
                    cleaned = ','.join(t for t in genericTickList.split(',') if t.strip() not in ('bL', '236'))
                    logger.debug("reqMktData strip bL: orig=%s cleaned=%s snapshot=%s contract=%s(%s)",
                                 genericTickList, cleaned, snapshot, contract.symbol, contract.secType)
                    if cleaned:
                        genericTickList = cleaned
                    else:
                        genericTickList = ''
                _orig_req(reqId, contract, genericTickList, snapshot, regulatorySnapshot, mktDataOptions)
            self._ib.client.reqMktData = _patched_req
            _connected = True
            _connected_at = time.time()
            self._reconnect_delay = 1.0
            logger.info(f"IBKR connected to {self.host}:{self.port}")
            # Wire up live tick callback
            self._ib.pendingTickersEvent += self._on_pending_tickers
            self._resubscribe_all()
            # Run the event loop until disconnect
            self._ib.run()
        except ConnectionRefusedError:
            _connected = False
            raise ConnectionRefusedError(
                f"Cannot connect to IBKR Gateway at {self.host}:{self.port}. "
                f"Make sure TWS/IB Gateway is running on your local machine. "
                f"If you're on Render/cloud, disable IBKR in Settings."
            ) from None
        except Exception as e:
            _connected = False
            logger.debug(f"IBKR connection attempt failed: {e}")
            raise
        finally:
            _connected = False
            self._disconnect()
            if self._loop and not self._loop.is_closed():
                # Cancel all pending tasks to prevent "Task was destroyed but it is pending!" errors
                # (e.g., orphaned _do_fetch() coroutines from fetch_option_chain_ibkr
                #  scheduled via run_coroutine_threadsafe while IBKR was connected)
                try:
                    pending = asyncio.all_tasks(loop=self._loop)
                    if pending:
                        for task in pending:
                            task.cancel()
                        self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception as e:
                    logger.debug(f"Task cleanup during disconnect: {e}")
                self._loop.stop()
                self._loop.close()
            self._loop = None

    def _disconnect(self):
        try:
            if self._ib and self._ib.isConnected():
                self._ib.disconnect()
        except Exception:
            pass
        self._ib = None

    def _resubscribe_all(self):
        """Backward-compat sync shim — dispatches the async version. New callers
        should use update_subscriptions() which already calls _async_resubscribe_all
        via run_coroutine_threadsafe.

        When called from inside the streamer thread (e.g. during _connect_and_stream
        BEFORE self._ib.run() starts the loop), uses loop.create_task directly to
        avoid run_coroutine_threadsafe's same-thread pipelock footgun.
        """
        if not self._ib or not self._ib.isConnected():
            return
        if self._loop and not self._loop.is_closed():
            if threading.current_thread() is self._thread:
                # Same thread as loop owner — safe to create_task directly.
                # run_coroutine_threadsafe works from any thread but in the loop's
                # own thread during pre-run() setup it can queue-pipe-lock.
                self._loop.create_task(self._async_resubscribe_all())
            else:
                asyncio.run_coroutine_threadsafe(
                    self._async_resubscribe_all(), self._loop
                )

    async def _async_resubscribe_all(self):
        """Async version of _resubscribe_all — uses qualifyContractsAsync to avoid
        blocking the event loop (sync qualifyContracts raises RuntimeError when
        called from inside a running asyncio loop).

        Uses contract.secType == 'FUT' instead of isinstance(contract, Future)
        because Future isn't reliably scope-resolvable in some ib_insync versions,
        and secType is an attribute the IB instance reliably sets on every Future.
        """
        if not self._ib or not self._ib.isConnected():
            return
        # Snapshot under the lock (avoid concurrent mutation during iteration)
        with _live_prices_lock:
            tickers = sorted(list(_subscribed_tickers), key=lambda t: (1 if '=F' not in t else 0, t))
        # First connect: _subscribed_tickers is still empty — delta_subscribe
        # will push tickers after the engine seeds the SubscriptionManager.
        # On reconnect, _subscribed_tickers survives the disconnect so this
        # guard only fires once on the very first startup.
        if not tickers:
            logger.debug("First connect — _subscribed_tickers empty, "
                         "skipping resubscribe. delta_subscribe will push tickers.")
            return
        # If connected recently, wait up to 8s for TWS/IB Gateway to finish
        # initializing market data connections.  Without this delay,
        # qualifyContractsAsync fails for ALL futures contracts.
        elapsed = time.time() - _connected_at
        if elapsed < 8:
            await asyncio.sleep(8 - elapsed)
        if tickers:
            logger.info(f"IBKR subscribing to {len(tickers)} tickers: {tickers[:5]}...")
        else:
            logger.debug("_async_resubscribe_all: no tickers to subscribe")
        for ticker in tickers:
            try:
                contract = self._make_contract(ticker)
                if not contract:
                    continue
                # Futures need qualification to resolve front month.
                # NOTE: qualifyContractsAsync silently drops ambiguous contracts
                # (len(detailsList) > 1) without adding them to the result list.
                # We use reqContractDetailsAsync directly and pick the first
                # (front month) contract ourselves.
                if contract.secType == 'FUT':
                    # Layer 1: Pre-validation
                    is_valid, reason = _validate_futures_contract(contract)
                    if not is_valid:
                        logger.debug(f"Contract skip: {ticker} | type=FUT | reason={reason}")
                        continue

                    # Layer 2: Cache check
                    cache_key = _get_cache_key(contract)
                    cached = _cache_get(cache_key)
                    if cached:
                        contract = cached
                        logger.debug(f"Contract cache hit: {ticker} | type=FUT")
                    elif _is_invalid_cached(cache_key):
                        logger.debug(f"Contract skip: {ticker} | type=FUT | reason=CACHED_INVALID")
                        continue
                    else:
                        # Qualify with retry
                        if not hasattr(self._ib, 'reqContractDetailsAsync'):
                            raise AttributeError(
                                f"ib_insync >= 0.9.36 required for {ticker} futures qualification"
                            )
                        details_list = await self._ib.reqContractDetailsAsync(contract)
                        if not details_list:
                            logger.info(f"IBKR retrying qualification for {ticker} in 3s...")
                            await asyncio.sleep(3)
                            details_list = await self._ib.reqContractDetailsAsync(contract)
                        if details_list:
                            contract = details_list[0].contract
                            _cache_set(cache_key, contract)
                        else:
                            _cache_invalid(cache_key)
                            logger.debug(f"Contract skip: {ticker} | type=FUT | reason=QUALIFICATION_FAILED | cached=True")
                            continue
                self._ib.reqMktData(contract, '', False, False)
            except Exception as e:
                logger.debug(f"IBKR subscribe {ticker} failed: {e}")

    def _on_pending_tickers(self, tickers):
        """Callback fired by ib_insync when new market data arrives.

        Handles Stock, Future, and Option contracts.
        Option data is cached in _live_option_prices for use by
        get_option_chain() in real_time_data_feeds.
        """
        now = datetime.now()
        for t in tickers:
            try:
                pass
                # ── Option contract handling ──
                if t.contract.secType == 'OPT':
                    key = f"{t.contract.symbol}_{t.contract.lastTradeDateOrContractMonth}_{t.contract.strike}_{t.contract.right}"
                    with _live_prices_lock:
                        # Generic tick merge: same pattern as fetch_live_option_prices.
                        # Init once, then merge individual fields on subsequent ticks
                        # so generic ticks 100/101/106 don't wipe out earlier data.
                        # IMPORTANT: option contract volume comes as standard tickType 8
                        # (td.volume), NOT callVolume/putVolume which are for the
                        # underlying stock's aggregate option chain volume.
                        _opt_vol = int(t.volume or 0)
                        _opt_oi = int(getattr(t, 'callOpenInterest', 0) or getattr(t, 'putOpenInterest', 0) or 0)
                        if key not in _live_option_prices:
                            # Extract Greeks from modelGreeks (OptionComputation object)
                            greeks = getattr(t, 'modelGreeks', None) or getattr(t, 'bidGreeks', None) or getattr(t, 'askGreeks', None) or getattr(t, 'lastGreeks', None)
                            _opt_iv = float((greeks.impliedVol if greeks else 0) or getattr(t, 'impliedVolatility', 0) or 0) * 100
                            _live_option_prices[key] = {
                                "ticker": t.contract.symbol,
                                "strike": float(t.contract.strike),
                                "expiry": t.contract.lastTradeDateOrContractMonth,
                                "right": t.contract.right.lower(),
                                "bid": float(t.bid or 0),
                                "ask": float(t.ask or 0),
                                "last": float(t.last or 0),
                                "volume": _opt_vol,
                                "openInterest": _opt_oi,
                                "histVolatility": round(float(getattr(t, 'histVolatility', 0) or 0) * 100, 2) if getattr(t, 'histVolatility', 0) and float(getattr(t, 'histVolatility', 0)) < 1.0 else float(getattr(t, 'histVolatility', 0) or 0),
                                "avgVolume": int(getattr(t, 'averageOptionVolumeAbove', 0) or 0),
                                "impliedVolatility": _opt_iv,
                                "delta": float((greeks.delta if greeks else 0) or getattr(t, 'delta', 0) or 0),
                                "gamma": float((greeks.gamma if greeks else 0) or getattr(t, 'gamma', 0) or 0),
                                "theta": float((greeks.theta if greeks else 0) or getattr(t, 'theta', 0) or 0),
                                "vega": float((greeks.vega if greeks else 0) or getattr(t, 'vega', 0) or 0),
                                "timestamp": now.isoformat(),
                                "source": "ibkr",
                            }
                        else:
                            cur = _live_option_prices[key]
                            if t.bid > 0:
                                cur["bid"] = float(t.bid)
                            if t.ask > 0:
                                cur["ask"] = float(t.ask)
                            if t.last > 0:
                                cur["last"] = float(t.last)
                            if _opt_vol > 0:
                                cur["volume"] = _opt_vol
                            if _opt_oi > 0:
                                cur["openInterest"] = _opt_oi
                            # Update Greeks from modelGreeks if available
                            greeks = getattr(t, 'modelGreeks', None) or getattr(t, 'bidGreeks', None) or getattr(t, 'askGreeks', None) or getattr(t, 'lastGreeks', None)
                            if greeks:
                                if greeks.impliedVol:
                                    cur["impliedVolatility"] = float(greeks.impliedVol) * 100
                                if greeks.delta:
                                    cur["delta"] = float(greeks.delta)
                                if greeks.gamma:
                                    cur["gamma"] = float(greeks.gamma)
                                if greeks.theta:
                                    cur["theta"] = float(greeks.theta)
                                if greeks.vega:
                                    cur["vega"] = float(greeks.vega)
                            else:
                                # Fallback: impliedVolatility attribute on Ticker
                                # (populated from tickOptionComputation when modelGreeks is None)
                                _opt_iv_attr = getattr(t, 'impliedVolatility', 0)
                                if _opt_iv_attr:
                                    cur["impliedVolatility"] = float(_opt_iv_attr) * 100
                            _hv = getattr(t, 'histVolatility', 0)
                            if _hv:
                                cur["histVolatility"] = round(float(_hv) * 100, 2) if float(_hv) < 1.0 else float(_hv)
                            _av = getattr(t, 'averageOptionVolumeAbove', 0)
                            if _av:
                                cur["avgVolume"] = int(_av)
                            cur["timestamp"] = now.isoformat()
                    continue

                # ── Futures option contract handling (FOP) ──
                # FOP contracts arrive from fetch_live_option_prices FuturesOption
                # subscriptions. Their prices (premiums) must NOT be stored as
                # underlying futures prices (e.g. ES put at $0.90 overwriting ES=F)
                if t.contract.secType == 'FOP':
                    continue

                # ── Index / stock / futures handling ──
                # Only process STK, FUT, and other non-option secTypes
                if t.contract.secType not in ('STK', 'FUT', 'IND'):
                    continue

                ticker = t.contract.symbol
                # IBKR qualifies futures with month/year suffix (e.g., M2KU4)
                # Strip to base symbol for reverse mapping
                base = self._extract_base_symbol(ticker, t.contract)
                mapped = self._reverse_map(base)
                price = (t.last or t.close or 0)

                # Backstop: if this tick has option-specific fields populated 
                # (impliedVol, histVolatility, openInterest, avgOptionVolume)
                # despite having a non-OPT/non-FOP secType, it's clearly an
                # option tick leaking in — skip it.
                # This catches cases where fetch_live_option_prices' FuturesOption
                # subscriptions somehow arrive with secType='FUT' instead of 'FOP'.
                key_before = mapped or base
                _opt_fields = (
                    float(getattr(t, 'impliedVol', 0) or 0) > 0
                    or float(getattr(t, 'histVolatility', 0) or 0) > 0
                    or int(getattr(t, 'openInterest', 0) or 0) > 0
                    or int(getattr(t, 'averageOptionVolume', 0) or 0) > 0
                )
                if _opt_fields:
                    logger.info(
                        f"IBKR option field backstop SKIP: {key_before} "
                        f"secType={t.contract.secType} "
                        f"iv={getattr(t,'impliedVol',0)} oi={getattr(t,'openInterest',0)}"
                    )
                    continue

                has_bidask = (t.bid or 0) > 0 or (t.ask or 0) > 0
                if price > 0 or has_bidask:
                    change = getattr(t, 'change', 0) or 0
                    change_pct = getattr(t, 'changePct', 0) or 0
                    key = mapped or base
                    live = {
                        "ticker": key,
                        "price": float(price),
                        "bid": float(t.bid or 0),
                        "ask": float(t.ask or 0),
                        "change": float(change),
                        "change_pct": float(change_pct),
                        "volume": int(t.volume or 0),
                        "high": float(t.high or 0),
                        "low": float(t.low or 0),
                        "open": float(t.open or 0),
                        "halted": bool(getattr(t, 'halted', False) or 0),
                        "mark": float(getattr(t, 'mark', 0) or 0),
                        "timestamp": now.isoformat(),
                        "source": "ibkr",
                    }
                    # Feed tick to real-time tick engine for Lee-Ready signing
                    try:
                        from engine.tick_engine import on_tick as _tick_on_tick
                        _tick_on_tick(
                            ticker=key,
                            sec_type=t.contract.secType,
                            last_price=float(price),
                            bid=float(t.bid or 0),
                            ask=float(t.ask or 0),
                            volume=int(t.volume or 0),
                            last_size=int(getattr(t, 'lastSize', 0) or 0),
                            timestamp=now.timestamp(),
                        )
                    except Exception:
                        pass

                    # Futures absolute price floor: protect against option
                    # premiums leaking into _live_prices as first-tick data
                    # (e.g. ES=F at bid=7.6 instead of ~5500).
                    # No major futures contract trades below $2.00.
                    if key and key.endswith("=F"):
                        _fb = float(t.bid or 0)
                        _fa = float(t.ask or 0)
                        _fl = float(t.last or 0)
                        _fc = float(t.close or 0)
                        _fm = float(getattr(t, 'mark', 0) or 0)
                        _peak = max(_fb, _fa, _fl, _fc, _fm)
                        if _peak > 0 and _peak < 2.0:
                            logger.warning(
                                f"IBKR price floor SKIP: {key} "
                                f"bid={_fb:.2f} ask={_fa:.2f} "
                                f"last={_fl:.2f} mark={_fm:.2f} "
                                f"— too low for futures, likely option leak"
                            )
                            continue
                    # Price sanity gate: skip absurd tick-to-tick jumps
                    # (RTY=F at 0.9, ES=F at 7543, etc.)
                    # Futures: 15% threshold (CME halts before this)
                    # Stocks: 30% threshold (allows gaps but blocks glitches)
                    _skip = False
                    with _live_prices_lock:
                        _existing = _live_prices.get(key)
                    if _existing and _existing.get("price", 0) > 0:
                        _dev = abs(price - _existing["price"]) / _existing["price"]
                        _is_fut = key.endswith("=F") if key else False
                        _is_micro = _is_fut and _is_micro_future(key) if key else False
                        _thresh = 0.02 if _is_micro else (0.15 if _is_fut else 0.30)
                        if _dev > _thresh:
                            _age = (now - datetime.fromisoformat(_existing["timestamp"])).total_seconds()
                            if _age < 120:
                                logger.warning(
                                    f"IBKR price sanity SKIP: {key} new={price:.2f} "
                                    f"cached={_existing['price']:.2f} deviation={_dev:.1%} "
                                    f"(threshold={_thresh:.0%})"
                                )
                                _skip = True
                    if not _skip:
                        with _live_prices_lock:
                            _live_prices[key] = live
                        # P3.2: Update streamer heartbeat
                        with _last_tick_lock:
                            _last_tick_time = time.time()
            except Exception as e:
                logger.debug(f"IBKR _on_pending_tickers error: {e}")

    def _extract_base_symbol(self, symbol: str, contract) -> str:
        """Extract base symbol from qualified IBKR futures contract.
        Qualified format: BASE + MONTH_CODE + YEAR_DIGIT (e.g., M2KU4)
        """
        if hasattr(contract, 'secType') and contract.secType == 'FUT':
            # Futures month codes: FGHJKMNQUVXZ
            # Last 2 chars are month code + year digit, but can vary
            # Use the registry base symbols for matching
            for base in sorted(["ES", "NQ", "YM", "RTY", "MCL", "MGC", "VX", "GC", "SI", "HG"], key=len, reverse=True):
                if symbol.startswith(base):
                    return base
        return symbol

    def _reverse_map(self, ibkr_symbol: str) -> Optional[str]:
        """Map IBKR base symbol back to yahoo-style ticker."""
        rev = {"ES": "ES=F", "NQ": "NQ=F", "YM": "YM=F", "RTY": "RTY=F",

               "MCL": "MCL=F", "MGC": "MGC=F",
               "VX": "VX=F",
               "GC": "GC=F",
               "SI": "SI=F",
               "HG": "HG=F"}
        return rev.get(ibkr_symbol)

    # Per-future contract month cycles (not all follow quarterly)
    _FUTURE_CONTRACT_CYCLES = {
        "ES": [3, 6, 9, 12], "NQ": [3, 6, 9, 12], "YM": [3, 6, 9, 12],
        "RTY": [3, 6, 9, 12],
        "GC": [2, 4, 6, 8, 10, 12], "MGC": [2, 4, 6, 8, 10, 12],
        "SI": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "HG": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "CL": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "MCL": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "VX": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    }

    @staticmethod
    def _next_future_expiry(symbol: str) -> str:
        """Next valid contract month for a given futures symbol.

        Uses per-future contract cycles (quarterly, bi-monthly, or monthly).
        Returns YYYYMM format (e.g. '202608' for Aug 2026).

        Auto-rolls contracts within 2 days of estimated expiry to the next
        cycle month (prevents querying dead contracts like CLQ6 on Jul 21).

        VIX futures (VX) trade monthly and expire mid-month (~3rd Wednesday).
        Before the 16th the front month is the current month; after the 15th
        it rolls to next month. All other futures use m > month (current month
        already expired when the front month rolls).
        """
        cycles = IBKRStreamer._FUTURE_CONTRACT_CYCLES.get(symbol, [3, 6, 9, 12])
        today = date.today()
        month = today.month
        year = today.year
        # VIX futures: monthly, expire ~3rd Wednesday (15th-21st).
        if symbol == "VX":
            day = today.day
            for m in sorted(cycles):
                if (day <= 21 and m >= month) or (day > 21 and m > month):
                    return f"{year}{m:02d}"
            return f"{year + 1}{cycles[0]:02d}"
        for m in sorted(cycles):
            if m > month:
                candidate = f"{year}{m:02d}"
                if _is_near_expiry(symbol, candidate):
                    logger.debug("Futures rollover: %s %s near expiry, advancing to next cycle", symbol, candidate)
                    continue
                return candidate
        # Wrap to next year — also check near-expiry on first cycle of new year
        candidate = f"{year + 1}{cycles[0]:02d}"
        if _is_near_expiry(symbol, candidate) and len(cycles) > 1:
            return f"{year + 1}{cycles[1]:02d}"
        return candidate

    @staticmethod
    def _next_quarterly_futures_expiry() -> str:
        """Next quarterly futures expiry (Mar/Jun/Sep/Dec) as YYYYMM."""
        today = date.today()
        month = today.month
        year = today.year
        for m in (3, 6, 9, 12):
            if m > month:
                return f"{year}{m:02d}"
        return f"{year + 1}03"

    def _make_contract(self, ticker: str) -> Optional[object]:
        from ib_insync import Stock, Future, Index
        ticker = ticker.upper().strip()
        # Block removed micro futures from ever being subscribed
        if _is_blocked_future(ticker):
            logger.debug(f"IBKR: blocking removed micro future {ticker}")
            return None
        # Special symbol mappings for IBKR
        special_map = {
            "BRK.B": "BRK-B",
            "BRK-B": "BRK-B",
            "SPX": "SPX",
            "NDX": "NDX",
            "VIX": "VIX",
        }
        if ticker in special_map:
            mapped = special_map[ticker]
            if mapped in ("SPX", "VIX", "NDX"):
                return Index(mapped, exchange="CBOE", currency="USD")
            return Stock(mapped, exchange="SMART", currency="USD")
        # Futures mapping (IBKR uses different symbols than yahoo)
        future_map = {
            "ES=F": ("ES", "CME"),
            "NQ=F": ("NQ", "CME"),
            "YM=F": ("YM", "CBOT"),
            "RTY=F": ("RTY", "CME"),

            "MCL=F": ("MCL", "NYMEX"),
            "MGC=F": ("MGC", "COMEX"),
            "VX=F": ("VX", "CFE"),
            "GC=F": ("GC", "COMEX"),
            "SI=F": ("SI", "COMEX"),
            "HG=F": ("HG", "COMEX"),
            "CL=F": ("CL", "NYMEX"),
        }
        if ticker in future_map:
            symbol, exchange = future_map[ticker]
            expiry = self._next_future_expiry(symbol)
            # SI=F needs tradingClass='SI' to disambiguate from SIL (mini silver)
            extra = {}
            if symbol == "SI":
                extra["tradingClass"] = "SI"
            return Future(
                symbol=symbol,
                exchange=exchange,
                lastTradeDateOrContractMonth=expiry,
                includeExpired=False,
                **extra,
            )
        if ticker.endswith(".NS"):
            return Stock(ticker.replace(".NS", ""), exchange="NSE", currency="INR")
        if ticker.endswith(".BO"):
            return Stock(ticker.replace(".BO", ""), exchange="BSE", currency="INR")
        # Option key detection: TICKER_YYYYMMDD_STRIKE_C/P (from subscription_manager._option_key)
        opt_match = re.match(
            r'^([A-Z]+(?:\.[A-Z]+)?)_(\d{8})_(\d+(?:\.\d+)?)_([CP])$',
            ticker
        )
        if opt_match:
            from ib_insync import Option
            sym, exp, strike_str, right = opt_match.groups()
            return Option(
                symbol=sym,
                lastTradeDateOrContractMonth=exp,
                strike=float(strike_str),
                right=right,
                multiplier="100",
                exchange="SMART",
                currency="USD",
            )
        return Stock(ticker, exchange="SMART", currency="USD") if ticker else None

# ── Streamer singleton for cross-module access ──
_streamer_instance: Optional['IBKRStreamer'] = None


def get_streamer() -> Optional['IBKRStreamer']:
    with _streamer_lock:
        return _streamer_instance


def set_streamer(instance: Optional['IBKRStreamer']):
    global _streamer_instance
    with _streamer_lock:
        _streamer_instance = instance


def fetch_historical_bars(ticker: str, duration: str = "2 M", bar_size: str = "1 day") -> Optional[list]:
    """Fetch historical OHLCV bars from IBKR (thread-safe, blocking call).

    Args:
        ticker: Yahoo-style ticker (e.g. 'ES=F', 'AAPL')
        duration: IBKR duration string ('1 D', '2 M', '1 Y')
        bar_size: IBKR bar size ('1 min', '5 mins', '1 hour', '1 day')

    Returns:
        List of dicts {open, high, low, close, volume, timestamp}, or None on failure.
    """
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return None
    contract = streamer._make_contract(ticker)
    if not contract:
        return None

    # ── Futures contract qualification ──
    # Futures need a properly qualified contract (with conId) before IBKR
    # will serve historical data, especially for intraday bar sizes.
    if contract.secType == 'FUT':
        is_valid, reason = _validate_futures_contract(contract)
        if not is_valid:
            logger.debug(f"Historical bars skip: {ticker} | type=FUT | reason={reason}")
            return None

        cache_key = _get_cache_key(contract)
        cached = _cache_get(cache_key)
        if cached:
            contract = cached
        elif _is_invalid_cached(cache_key):
            logger.debug(f"Historical bars skip: {ticker} | type=FUT | reason=CACHED_INVALID")
            return None
        else:
            qual_result: list = [contract]
            qual_event = threading.Event()

            async def _qualify():
                try:
                    if not hasattr(streamer._ib, 'reqContractDetailsAsync'):
                        qual_event.set()
                        return
                    details_list = await streamer._ib.reqContractDetailsAsync(contract)
                    if not details_list:
                        await asyncio.sleep(3)
                        details_list = await streamer._ib.reqContractDetailsAsync(contract)
                    if details_list:
                        qc = details_list[0].contract
                        if contract.exchange:
                            qc.exchange = contract.exchange
                        _cache_set(cache_key, qc)
                        qual_result[0] = qc
                    else:
                        _cache_invalid(cache_key)
                except Exception as e:
                    logger.debug(f"IBKR qualify failed for {ticker}: {e}")
                finally:
                    qual_event.set()

            asyncio.run_coroutine_threadsafe(_qualify(), streamer._loop)
            qual_event.wait(timeout=15)
            contract = qual_result[0]

    # Use run_coroutine_threadsafe + reqHistoricalDataAsync to dispatch the request
    # onto the streamer's event loop.  ib_insync's sync reqHistoricalData uses
    # util.run() → asyncio.get_event_loop() which can return the wrong loop in
    # Python 3.12+ when called from a non-event-loop thread, causing silent fails.
    # By using run_coroutine_threadsafe + the async version, we ensure the request
    # runs on the correct event loop (streamer._loop).
    result_container: list = [None]  # list-of-one for mutable container pattern
    event = threading.Event()

    async def _fetch_async():
        try:
            bars = await streamer._ib.reqHistoricalDataAsync(
                contract, endDateTime='', durationStr=duration,
                barSizeSetting=bar_size, whatToShow='TRADES',
                useRTH='=F' not in ticker, formatDate=1, keepUpToDate=False,
            )
            processed = []
            for bar in bars:
                ts = bar.date
                if hasattr(ts, 'isoformat'):
                    ts_str = ts.isoformat()
                else:
                    ts_str = str(ts)
                processed.append({
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": int(bar.volume),
                    "timestamp": ts_str,
                })
            result_container[0] = processed
            elapsed = time.time() - _start_ts
            logger.debug(f"IBKR historical bars: {len(processed)} bars for {ticker} ({bar_size}, {duration}) in {elapsed:.1f}s")
        except Exception as e:
            logger.debug(f"IBKR historical bars failed for {ticker}: {e}")
            result_container[0] = None
        finally:
            event.set()

    _start_ts = time.time()
    asyncio.run_coroutine_threadsafe(_fetch_async(), streamer._loop)
    event.wait(timeout=30)
    return result_container[0]


def fetch_option_chain_ibkr(ticker: str) -> Optional[dict]:
    """Fetch option chain structure (expirations, strikes) from IBKR (thread-safe).

    Returns dict with {ticker, expirations, strikes} or None on failure.

    Dispatches async IBKR methods into the streamer's event loop via
    run_coroutine_threadsafe, because sync qualifyContracts / reqSecDefOptParams
    call loop.run_until_complete internally (via util.run) which raises
    "This event loop is already running" when the streamer loop is active.
    """
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return None
    from ib_insync import Option, Stock, Future

    contract = streamer._make_contract(ticker)
    if not contract:
        return None

    event = threading.Event()
    result_container: Optional[dict] = None

    async def _do_fetch():
        """Async coroutine to fetch option chain inside streamer's event loop."""
        nonlocal result_container
        logger.debug("fetch_option_chain_ibkr(%s): _do_fetch started", ticker)
        try:
            # Layer 1: Check cache for qualified underlying
            cache_key = _get_cache_key(contract)
            cached_qc = _cache_get(cache_key)
            if cached_qc:
                qc = cached_qc
                logger.debug(f"Option chain cache hit: {ticker} | type=underlying")
            else:
                # For futures, use reqContractDetailsAsync to get the correct conId.
                # qualifyContractsAsync can incorrectly resolve to Stock for
                # dual-listed symbols like "ES" (Eversource vs S&P E-mini).
                if isinstance(contract, Future):
                    details = await streamer._ib.reqContractDetailsAsync(contract)
                    if details:
                        qc = details[0].contract
                    else:
                        logger.warning("fetch_option_chain_ibkr(%s) | underlying=%s | reason=CONTRACT_DETAILS_EMPTY", ticker, contract.symbol if hasattr(contract, 'symbol') else ticker)
                        result_container = None
                        return
                else:
                    qualified_list = await streamer._ib.qualifyContractsAsync(contract)
                    qc = qualified_list[0] if qualified_list else contract
                if qc.conId:
                    _cache_set(cache_key, qc)
                else:
                    _cache_invalid(cache_key)
                    logger.warning("fetch_option_chain_ibkr(%s) | underlying=%s | reason=QUALIFY_EMPTY", ticker, contract.symbol if hasattr(contract, 'symbol') else ticker)
                    result_container = None
                    return

            # Determine params for reqSecDefOptParamsAsync.
            # Use isinstance on BOTH the original contract and the qualified qc
            # since qualifyContractsAsync can change Future to Stock.
            is_future = isinstance(contract, Future) or isinstance(qc, Future)
            if is_future:
                con_id = qc.conId
                symbol = qc.symbol if hasattr(qc, 'symbol') else ticker.replace("=F", "")
                sec_type = "FUT"
                exchange = qc.exchange or getattr(contract, 'exchange', '') or ""
            else:
                con_id = qc.conId or 0
                symbol = qc.symbol if hasattr(qc, 'symbol') else ticker.replace("=F", "")
                sec_type = "STK"
                exchange = ""  # Query all option exchanges for stocks

            # Layer 1 for options: Validate expirations before requesting
            # We'll fetch the full chain first, then filter
            chain = await streamer._ib.reqSecDefOptParamsAsync(symbol, exchange, sec_type, con_id)
            if not chain:
                logger.warning("fetch_option_chain_ibkr(%s) | reqSecDefOptParamsAsync returned empty | symbol=%s exchange=%s secType=%s conId=%d", ticker, symbol, exchange, sec_type, con_id)
                result_container = None
                return
            params = chain[0]

            # Layer 1 for options: Pre-validate expirations
            today = date.today()
            valid_expirations = []
            for exp in params.expirations:
                is_valid, reason = _validate_option_params(symbol, exp, 1.0, "C")
                if is_valid:
                    valid_expirations.append(exp)
                else:
                    logger.debug(f"Contract skip: {symbol} {exp} | type=OPT | reason={reason}")

            if not valid_expirations:
                logger.warning("fetch_option_chain_ibkr(%s) | no valid expirations after validation | total_expirations=%d first_few=%s", ticker, len(params.expirations), params.expirations[:5])
                result_container = None
                return

            expirations = sorted(valid_expirations)[:10]
            # Return ALL strikes — the caller (option_metrics.py) slices to
            # its budget.  With sequential full-chain mode, we need 41+ strikes
            # available per underlying (half=20 → 41 strikes).
            strikes = sorted(params.strikes)
            result_container = {
                "ticker": ticker,
                "expirations": expirations,
                "strikes": strikes,
            }
        except Exception as exc:
            logger.warning("fetch_option_chain_ibkr(%s) failed: %s: %s", ticker, type(exc).__name__, exc)
            result_container = None
        finally:
            event.set()

    asyncio.run_coroutine_threadsafe(_do_fetch(), streamer._loop)
    event.wait(timeout=60)
    # Safety: if coroutine didn't complete (timeout/error), result_container
    # is still None. Ensure we don't pass a list/None to callers expecting a dict.
    if result_container is None or isinstance(result_container, list):
        return None
    return result_container


def fetch_live_option_prices(ticker: str, expiration: str, strikes: list[float], max_strikes: int = 0) -> Optional[dict]:
    """Fetch live option prices from IBKR for a specific expiration and strikes.

    Uses streaming mode (reqMktData with snapshot=False) because TWS 10+
    auto-adds bL to genericTickList server-side, which causes Error 321
    rejection in snapshot mode. Subscriptions are explicitly cancelled
    after data arrives. Returns merged bid/ask/last/IV/greeks from IBKR
    with your paid Options subscription.

    Args:
        ticker: Stock ticker (e.g. 'AAPL', 'SPY')
        expiration: Expiration date string, can be '2026-06-14' or '20260614'
        strikes: List of strike prices to fetch
        max_strikes: If > 0, trim strikes to ATM±max_strikes/2 (reduces IBKR snapshot load)

    Returns:
        Dict with {strike: {call: {bid, ask, last, iv, delta, gamma, theta, vega},
                           put: {bid, ask, last, iv, ...}}},
        or None on failure.
    """
    # If max_strikes > 0, trim strikes to ATM±(max_strikes/2) to reduce IBKR snapshot load
    if max_strikes > 0 and len(strikes) > max_strikes:
        sorted_strikes = sorted(strikes)
        mid = len(sorted_strikes) // 2
        half = max_strikes // 2
        strikes = sorted_strikes[max(0, mid - half):min(len(sorted_strikes), mid + half)]
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return None

    from ib_insync import Option
    import time as _time

    # Normalize expiration to YYYYMMDD format for IBKR
    exp_clean = expiration.replace("-", "").replace("/", "")

    result: Optional[dict] = None
    event = threading.Event()

    def _safe_int(v, default=0):
        """Convert to int safely, returning default for NaN/None/invalid."""
        try:
            f = float(v or 0)
            if math.isnan(f):
                return default
            return int(f)
        except (ValueError, TypeError, AttributeError):
            return default


    async def _do_fetch_async():
        nonlocal result
        try:
            prices = {}
            contracts = []
            # Create Option contracts for each strike × right
            # Futures options need FuturesOption with correct symbol/exchange/multiplier
            try:
                from engine.countries.usa.futures_registry import get_futures_by_yf_ticker
            except ImportError:
                get_futures_by_yf_ticker = lambda t: None
            from ib_insync import FuturesOption
            fut_spec = get_futures_by_yf_ticker(ticker)
            for strike in strikes:
                for right in ["C", "P"]:
                    if fut_spec:
                        contract = FuturesOption(
                            symbol=fut_spec["contract_code"],
                            lastTradeDateOrContractMonth=exp_clean,
                            strike=strike,
                            right=right,
                            multiplier=str(fut_spec["multiplier"]),
                            exchange=fut_spec["exchange"],
                            currency="USD",
                    tradingClass=fut_spec.get("tradingClass") or fut_spec["contract_code"],
                        )
                    else:
                        from ib_insync import Option
                        contract = Option(
                            symbol=ticker,
                            lastTradeDateOrContractMonth=exp_clean,
                            strike=strike,
                            right=right,
                            multiplier="100",
                            exchange="SMART",
                            currency="USD",
                        )
                    contracts.append(contract)
                    # Request market data (streaming mode — TWS 10+ rejects bL in snapshot).
                    # Option volume comes as standard tickType 8 (td.volume), no generic tick needed.
                    # Generic ticks: 101=OI, 104=hist vol, 105=avg vol, 106=implied vol.
                    # Note: generic tick 100 (callVolume/putVolume) is for the UNDERLYING stock
                    # aggregate, NOT for individual option contracts.
                    streamer._ib.reqMktData(contract, '101,104,105,106', False, False)

            # Poll for data up to 35 seconds.
            # Generic ticks 101/106 (OI, IV) arrive 5-25s after
            # bid/ask/last. The 35s window + 50% generic-tick break condition
            # ensures we collect slow generic tick 106 (IV) before cancelling subscriptions.
            deadline = _time.time() + 35.0
            while _time.time() < deadline:
                for c in contracts:
                    td = streamer._ib.ticker(c)
                    if td and (td.bid or td.ask or td.last or td.volume or getattr(td, 'impliedVolatility', 0) or getattr(td, 'callOpenInterest', 0) or getattr(td, 'putOpenInterest', 0)):
                        key = f"{c.strike}_{c.right}"
                        # Generic tick merge: IBKR delivers bid/ask/last in one tick,
                        # then generic ticks 100/101/104/105/106 (volume, OI, IV,
                        # greeks) arrive in SEPARATE subsequent updates.
                        # Full dict overwrite would lose data from earlier ticks.
                        # Instead: init once, then merge individual fields on update.
                        if key not in prices:
                            greeks_init = getattr(td, 'modelGreeks', None) or getattr(td, 'bidGreeks', None) or getattr(td, 'askGreeks', None) or getattr(td, 'lastGreeks', None)
                            # Volume: standard tickType 8 for individual option contracts
                            # (callVolume/putVolume is for underlying aggregate, not per-contract)
                            prices[key] = {
                                "strike": float(c.strike),
                                "right": c.right.lower(),
                                "bid": float(td.bid or 0),
                                "ask": float(td.ask or 0),
                                "last": float(td.last or 0),
                                "volume": _safe_int(td.volume),
                                "openInterest": _safe_int(getattr(td, 'callOpenInterest', 0) or getattr(td, 'putOpenInterest', 0)),
                                "histVolatility": round(float(getattr(td, 'histVolatility', 0) or 0) * 100, 2) if getattr(td, 'histVolatility', 0) and float(getattr(td, 'histVolatility', 0)) < 1.0 else float(getattr(td, 'histVolatility', 0) or 0),
                                "avgVolume": _safe_int(getattr(td, 'averageOptionVolumeAbove', 0)),
                                "impliedVolatility": float((greeks_init.impliedVol if greeks_init else 0) or getattr(td, 'impliedVolatility', 0) or 0) * 100,
                                "delta": float((greeks_init.delta if greeks_init else 0) or getattr(td, 'delta', 0) or 0),
                                "gamma": float((greeks_init.gamma if greeks_init else 0) or getattr(td, 'gamma', 0) or 0),
                                "theta": float((greeks_init.theta if greeks_init else 0) or getattr(td, 'theta', 0) or 0),
                                "vega": float((greeks_init.vega if greeks_init else 0) or getattr(td, 'vega', 0) or 0),
                            }
                        else:
                            # Merge: update bid/ask/last (market moves), then fill
                            # generic ticks only when they arrive (> 0). Prevents
                            # later ticks from zeroing out previously-arrived data.
                            cur = prices[key]
                            cur["bid"] = float(td.bid or 0)
                            cur["ask"] = float(td.ask or 0)
                            cur["last"] = float(td.last or 0)
                            _nv = _safe_int(td.volume)
                            if _nv > 0:
                                cur["volume"] = _nv
                            _noi = _safe_int(getattr(td, 'callOpenInterest', 0) or getattr(td, 'putOpenInterest', 0))
                            if _noi > 0:
                                cur["openInterest"] = _noi
                            if getattr(td, 'impliedVolatility', 0):
                                cur["impliedVolatility"] = float(getattr(td, 'impliedVolatility', 0)) * 100
                            _hv = getattr(td, 'histVolatility', 0)
                            if _hv:
                                cur["histVolatility"] = round(float(_hv) * 100, 2) if float(_hv) < 1.0 else float(_hv)
                            _av = getattr(td, 'averageOptionVolumeAbove', 0)
                            if _av:
                                cur["avgVolume"] = _safe_int(_av)
                            greeks = td.modelGreeks or td.bidGreeks or td.askGreeks or td.lastGreeks
                            if greeks:
                                if getattr(greeks, 'impliedVol', 0):
                                    cur["impliedVolatility"] = float(greeks.impliedVol) * 100
                                if greeks.delta:
                                    cur["delta"] = float(greeks.delta)
                                if greeks.gamma:
                                    cur["gamma"] = float(greeks.gamma)
                                if greeks.theta:
                                    cur["theta"] = float(greeks.theta)
                                if greeks.vega:
                                    cur["vega"] = float(greeks.vega)
                            # Also cache for subsequent reads
                            cache_key = f"{ticker}_{exp_clean}_{c.strike}_{c.right}"
                            with _live_prices_lock:
                                _live_option_prices[cache_key] = prices[key].copy()
                                _live_option_prices[cache_key]["ticker"] = ticker
                                _live_option_prices[cache_key]["expiry"] = exp_clean
                                _live_option_prices[cache_key]["timestamp"] = _time.time()
                # Only break early when 80% of contracts have bid/ask/last AND
                # 50% have received generic tick data (volume, OI, or IV > 0).
                # Without this, we cancel subscriptions before generic ticks
                # 100/101/106 arrive in subsequent updates.
                _has_generic = sum(1 for p in prices.values() if (
                    p.get("volume", 0) > 0
                    or p.get("openInterest", 0) > 0
                    or p.get("impliedVolatility", 0) > 0
                ))
                if len(prices) >= len(contracts) * 0.8 and _has_generic >= len(contracts) * 0.5:
                    break
                await asyncio.sleep(0.15)

            # ── DIAGNOSTIC: audit which generic tick fields arrived ──
            _deadline = deadline  # capture for elapsed calc
            _elapsed = _time.time() - (_deadline - 35.0)
            _total = len(prices)
            _n_contracts = len(contracts)

            # Generic tick field delivery audit (count + % of total contracts)
            _with_bid = sum(1 for p in prices.values() if p.get("bid", 0) > 0)
            _with_ask = sum(1 for p in prices.values() if p.get("ask", 0) > 0)
            _with_last = sum(1 for p in prices.values() if p.get("last", 0) > 0)
            _with_vol = sum(1 for p in prices.values() if p.get("volume", 0) > 0)
            _with_oi = sum(1 for p in prices.values() if p.get("openInterest", 0) > 0)
            _with_iv = sum(1 for p in prices.values() if p.get("impliedVolatility", 0) > 0)
            _with_delta = sum(1 for p in prices.values() if p.get("delta", 0) != 0)
            _with_gamma = sum(1 for p in prices.values() if p.get("gamma", 0) != 0)
            _with_theta = sum(1 for p in prices.values() if p.get("theta", 0) != 0)
            _with_vega = sum(1 for p in prices.values() if p.get("vega", 0) != 0)
            _with_hv = sum(1 for p in prices.values() if p.get("histVolatility", 0) > 0)
            _with_avgvol = sum(1 for p in prices.values() if p.get("avgVolume", 0) > 0)
            _timeout = _elapsed >= 34.5

            # ── Tick 106 (impliedVolatility) specific audit ──
            # IBKR delivers implied vol via: (a) modelGreeks.impliedVol (fast, snapshot),
            # or (b) generic tick 106 / tickOptionComputation (slow, 5-25s delayed).
            # Contracts with zero IV after the full 35s window indicate tick 106
            # is NOT being delivered at all (potentially a TWS permission/market data issue).
            _zero_iv = []
            _zero_vol = []
            _zero_oi = []
            _zero_greeks = []
            for k, p in prices.items():
                _iv_val = p.get("impliedVolatility", 0) or 0
                if isinstance(_iv_val, float) and (math.isnan(_iv_val) or math.isinf(_iv_val)):
                    _iv_val = 0.0
                if _iv_val <= 0:
                    _zero_iv.append(k)
                if p.get("volume", 0) <= 0:
                    _zero_vol.append(k)
                if p.get("openInterest", 0) <= 0:
                    _zero_oi.append(k)
                if p.get("delta", 0) == 0 and p.get("gamma", 0) == 0:
                    _zero_greeks.append(k)

            # Sample first 3 contracts with all field values
            _sample = []
            for i, (k, p) in enumerate(prices.items()):
                if i >= 3:
                    break
                _iv_val = p.get('impliedVolatility', 0) or 0
                if isinstance(_iv_val, float) and (math.isnan(_iv_val) or math.isinf(_iv_val)):
                    _iv_val = 0.0
                _sample.append(
                    f"{k}: bid={p.get('bid',0):.2f} ask={p.get('ask',0):.2f} last={p.get('last',0):.2f} "
                    f"vol={p.get('volume',0)} oi={p.get('openInterest',0)} iv={_iv_val:.1f}% hv={p.get('histVolatility',0)} avgvol={p.get('avgVolume',0)} "
                    f"d={p.get('delta',0):.3f} g={p.get('gamma',0):.5f} th={p.get('theta',0):.5f} v={p.get('vega',0):.5f}"
                )

            # Main diagnostic line
            logger.info(
                "OPT DIAG [%s]: %d/%d contracts in %.1fs (timeout=%s) | "
                "bid=%d ask=%d last=%d | vol=%d oi=%d iv=%d hv=%d avgvol=%d | "
                "delta=%d gamma=%d theta=%d vega=%d | samples: [%s]",
                ticker, _total, _n_contracts, _elapsed, _timeout,
                _with_bid, _with_ask, _with_last,
                _with_vol, _with_oi, _with_iv, _with_hv, _with_avgvol,
                _with_delta, _with_gamma, _with_theta, _with_vega,
                " | ".join(_sample) if _sample else "none",
            )

            # ── Tick-106 failure detail: log exactly which contracts are missing IV ──
            if _zero_iv:
                _iv_pct = (1.0 - len(_zero_iv) / max(_total, 1)) * 100
                # Show first 10 and last 5 zero-IV contracts to keep log manageable
                _show = _zero_iv[:10]
                if len(_zero_iv) > 10:
                    _show += ["..."] + _zero_iv[-5:]
                logger.warning(
                    "OPT DIAG [%s]: tick 106 (impliedVolatility) MISSING on %d/%d contracts (%.0f%% received) | "
                    "zero-IV strikes: %s",
                    ticker, len(_zero_iv), _total, _iv_pct, ", ".join(_show),
                )
            if _zero_vol:
                logger.info(
                    "OPT DIAG [%s]: tickType 8 (volume) MISSING on %d/%d contracts",
                    ticker, len(_zero_vol), _total,
                )
            if _zero_oi:
                logger.info(
                    "OPT DIAG [%s]: generic tick 101 (openInterest) MISSING on %d/%d contracts",
                    ticker, len(_zero_oi), _total,
                )
            if _zero_greeks and _with_iv >= _total * 0.5:
                # Only warn about missing greeks if IV actually arrived
                # (if IV is also missing, the root cause is likely upstream)
                logger.info(
                    "OPT DIAG [%s]: modelGreeks (delta/gamma/theta/vega) MISSING on %d/%d contracts",
                    ticker, len(_zero_greeks), _total,
                )

            # ── Generic tick delivery summary (percentage of contracts that received each field) ──
            _pcts = {}
            for _field, _count in [
                ("bid", _with_bid), ("ask", _with_ask), ("last", _with_last),
                ("vol(tick8)", _with_vol), ("oi(tick101)", _with_oi),
                ("iv(tick106)", _with_iv), ("hv(tick104)", _with_hv),
                ("avgvol(tick105)", _with_avgvol),
                ("delta", _with_delta), ("gamma", _with_gamma),
                ("theta", _with_theta), ("vega", _with_vega),
            ]:
                _pcts[_field] = f"{_count}/{_n_contracts} (" + (
                    f"{_count / max(_n_contracts, 1) * 100:.0f}%"
                ) + ")"
            logger.info(
                "OPT DIAG [%s]: generic tick arrival summary => %s",
                ticker,
                " | ".join(f"{k}={v}" for k, v in _pcts.items()),
            )

            if prices:
                # Group by strike
                by_strike = {}
                for key, data in prices.items():
                    stk = data["strike"]
                    rt = data["right"]
                    if stk not in by_strike:
                        by_strike[stk] = {}
                    by_strike[stk][rt] = data
                result = by_strike
                logger.info(f"IBKR live option prices: {len(prices)} contracts for {ticker}")
            else:
                logger.debug(f"IBKR live option prices: no data received for {ticker}")
                result = None
            # Cancel streaming subscriptions to free ticker slots
            if contracts:
                for c in contracts:
                    try:
                        streamer._ib.cancelMktData(c)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"IBKR live option prices failed for {ticker}: {e}", exc_info=True)
            result = None
        finally:
            event.set()

    asyncio.run_coroutine_threadsafe(_do_fetch_async(), streamer._loop)
    event.wait(timeout=45)  # must exceed inner polling loop (35s) + margin
    return result


def clear_live_prices():
    """Force-clear all cached live prices and subscriptions.

    Call this when data goes stale (e.g. after IBKR disconnect/reconnect)
    so fresh ticks start from a clean slate instead of overwriting old data.
    """
    global _live_prices, _subscribed_tickers
    with _live_prices_lock:
        _live_prices.clear()
        _subscribed_tickers.clear()
    with _last_tick_lock:
        global _last_tick_time
        _last_tick_time = 0.0
    logger.warning("Live price cache cleared (force-reset)")


def get_last_tick_time() -> float:
    """P3.2: Return timestamp of last tick received."""
    with _last_tick_lock:
        return _last_tick_time


def get_live_price(ticker: str) -> Optional[dict]:
    """Thread-safe read from the live price cache."""
    ticker = ticker.upper().strip()
    with _live_prices_lock:
        q = _live_prices.get(ticker)
        if q:
            age = (datetime.now() - datetime.fromisoformat(q["timestamp"])).total_seconds()
            return {**q, "age_seconds": age}
        return None


def get_all_live_prices() -> dict:
    """Return a copy of the entire live price cache."""
    with _live_prices_lock:
        now = datetime.now()
        result = {}
        for ticker, q in _live_prices.items():
            age = (now - datetime.fromisoformat(q["timestamp"])).total_seconds()
            result[ticker] = {**q, "age_seconds": age}
        return result


def get_subscribed_tickers() -> list[str]:
    with _live_prices_lock:
        return sorted(_subscribed_tickers)


def get_live_option_price(ticker: str, expiry: str, strike: float, right: str) -> Optional[dict]:
    """Read a single live option price from the cache.

    Args:
        ticker: Stock symbol (e.g. 'AAPL')
        expiry: Expiration YYYYMMDD (e.g. '20260614')
        strike: Strike price
        right: 'C' or 'P' (uppercase)

    Returns:
        LiveOptionQuote dict, or None if not found.
    """
    exp_clean = expiry.replace("-", "").replace("/", "")
    key = f"{ticker.upper()}_{exp_clean}_{strike}_{right.upper()}"
    with _live_prices_lock:
        q = _live_option_prices.get(key)
        if q:
            return dict(q)
        return None


def get_all_live_option_prices() -> dict:
    """Return a copy of the entire live option price cache."""
    with _live_prices_lock:
        return {k: dict(v) for k, v in _live_option_prices.items()}


def clear_live_option_prices():
    """Clear the live option price cache. Called when chain expires."""
    with _live_prices_lock:
        _live_option_prices.clear()


def cancel_all_option_subscriptions():
    """Safety blanket: cancel any lingering option market-data subscriptions.

    fetch_live_option_prices already cancels its contracts before returning,
    but this provides defense-in-depth when cycling between underlyings in
    sequential full-chain mode.  Cancellations are dispatched onto the
    streamer's event loop (thread-safe).  Does NOT touch stock/futures subs.
    """
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return
    if not streamer._loop or streamer._loop.is_closed():
        return
    cancelled = [0]  # mutable to capture from async
    cancel_event = threading.Event()

    async def _cancel_async():
        try:
            if hasattr(streamer._ib, 'tickers'):
                for ticker in list(streamer._ib.tickers()):
                    ct = ticker.contract
                    if ct.secType in ('OPT', 'FOP'):
                        try:
                            streamer._ib.cancelMktData(ct)
                            cancelled[0] += 1
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"cancel_all_option_subscriptions: {e}")
        finally:
            cancel_event.set()

    asyncio.run_coroutine_threadsafe(_cancel_async(), streamer._loop)
    cancel_event.wait(timeout=3)
    if cancelled[0]:
        logger.debug(f"cancel_all_option_subscriptions: cancelled {cancelled[0]} option contracts")


def subscribe_ticker(ticker: str):
    """Subscribe a single ticker to IBKR without resubscribing everything.

    Adds the ticker to the global subscription set and calls reqMktData
    for just this ticker (skips the full _resubscribe_all loop).

    Uses qualifyContractsAsync via run_coroutine_threadsafe because the
    sync qualifyContracts raises RuntimeError when called from inside the
    streamer's running asyncio loop (silently swallowed → reqMktData never
    called → stale prices on dashboard).
    """
    ticker = ticker.upper().strip()
    if not ticker:
        return
    with _live_prices_lock:
        if ticker in _subscribed_tickers:
            return
        _subscribed_tickers.add(ticker)
    streamer = get_streamer()
    if not streamer or not streamer._ib:
        logger.debug(f"subscribe_ticker: no streamer for {ticker}")
        return
    if not streamer._ib.isConnected():
        logger.debug(f"subscribe_ticker: IBKR not connected yet for {ticker}, will be picked up by delta_subscribe")
        return
    if streamer._loop and not streamer._loop.is_closed():
        async def _do_sub():
            try:
                logger.info(f"subscribe_ticker: subscribing {ticker}")
                contract = streamer._make_contract(ticker)
                if contract:
                    if contract.secType == 'FUT':
                        # Pre-validation
                        is_valid, reason = _validate_futures_contract(contract)
                        if not is_valid:
                            logger.debug(f"Contract skip: {ticker} | type=FUT | reason={reason}")
                            return

                        # Cache check
                        cache_key = _get_cache_key(contract)
                        cached = _cache_get(cache_key)
                        if cached:
                            contract = cached
                            logger.debug(f"Contract cache hit: {ticker} | type=FUT")
                        elif _is_invalid_cached(cache_key):
                            logger.debug(f"Contract skip: {ticker} | type=FUT | reason=CACHED_INVALID")
                            return
                        else:
                            # Qualify with reqContractDetailsAsync + retry (matches _async_resubscribe_all)
                            if not hasattr(streamer._ib, 'reqContractDetailsAsync'):
                                raise AttributeError(
                                    f"ib_insync >= 0.9.36 required for {ticker} futures qualification"
                                )
                            details_list = await streamer._ib.reqContractDetailsAsync(contract)
                            if not details_list:
                                logger.info(f"IBKR retrying qualification for {ticker} in 3s...")
                                await asyncio.sleep(3)
                                details_list = await streamer._ib.reqContractDetailsAsync(contract)
                            if details_list:
                                contract = details_list[0].contract
                                _cache_set(cache_key, contract)
                            else:
                                _cache_invalid(cache_key)
                                logger.info(f"IBKR qualification failed for {ticker}, subscribing unqualified contract as fallback")
                                # Fallback: still subscribe the unqualified contract so we get some data
                    streamer._ib.reqMktData(contract, '', False, False)
            except Exception as e:
                logger.debug(f"IBKR subscribe_ticker {ticker} failed: {e}")
        if threading.current_thread() is streamer._thread:
            streamer._loop.create_task(_do_sub())
        else:
            asyncio.run_coroutine_threadsafe(_do_sub(), streamer._loop)


def subscribe_option(ticker: str, expiry: str, strike: float, right: str):
    """Subscribe an option contract to IBKR for persistent streaming.

    Args:
        ticker: Underlying stock symbol (e.g. 'AAPL')
        expiry: Expiration YYYYMMDD (e.g. '20260618')
        strike: Strike price
        right: 'C' or 'P' (uppercase)
    """
    ticker = ticker.upper().strip()
    right = right.upper().strip()
    if not ticker or not expiry or strike <= 0 or right not in ("C", "P"):
        return
    exp_clean = expiry.replace("-", "").replace("/", "")
    sub_key = f"{ticker}_{exp_clean}_{strike}_{right}"
    with _live_prices_lock:
        if sub_key in _subscribed_tickers:
            return
        _subscribed_tickers.add(sub_key)
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return
    if streamer._loop and not streamer._loop.is_closed():
        def _do_sub():
            try:
                from ib_insync import Option
                contract = Option(
                    symbol=ticker,
                    lastTradeDateOrContractMonth=exp_clean,
                    strike=strike,
                    right=right,
                    multiplier="100",
                    exchange="SMART",
                    currency="USD",
                    tradingClass=fut_spec.get("tradingClass") or fut_spec["contract_code"],
                )
                streamer._ib.reqMktData(contract, '', False, False)
            except Exception as e:
                logger.debug(f"IBKR subscribe_option {ticker} {exp_clean} {strike} {right} failed: {e}")
        streamer._loop.call_soon_threadsafe(_do_sub)


def cancel_market_data(ticker: str):
    """Cancel IBKR market data subscription for a ticker (frees a slot).

    Call when the subscription manager evicts a ticker so another
    can take its slot within the ~100-ticker IBKR limit.
    """
    ticker = ticker.upper().strip()
    if not ticker:
        return
    with _live_prices_lock:
        _subscribed_tickers.discard(ticker)
        _live_prices.pop(ticker, None)
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return
    if streamer._loop and not streamer._loop.is_closed():
        def _do_cancel():
            try:
                contract = streamer._make_contract(ticker)
                if contract:
                    streamer._ib.cancelMktData(contract)
            except Exception as e:
                logger.debug(f"IBKR cancel_market_data {ticker} failed: {e}")
        streamer._loop.call_soon_threadsafe(_do_cancel)


def delta_subscribe(to_add: list[str], to_cancel: list[str]):
    """Efficiently subscribe new tickers and cancel evicted ones.

    Avoids a full _resubscribe_all — just calls subscribe_ticker /
    cancel_market_data for the delta.  This preserves existing
    streaming subscriptions without disruption.
    """
    for t in to_cancel:
        cancel_market_data(t)
    if to_add:
        logger.info(f"delta_subscribe: {len(to_add)} new tickers: {to_add[:5]}...")
    for t in to_add:
        subscribe_ticker(t)


# ── Shortable shares / borrow rate ──────────────────────────────

_shortable_cache: dict = {}
_SHORTABLE_CACHE_TTL = 120


def get_shortable_info(ticker: str):
    """Query IBKR for shortable shares and borrow fee rate.

    Returns dict with shares_available, fee_rate, easy_to_borrow
    or empty dict if IBKR is unavailable. Results cached 2 min.
    """
    import time
    now = time.time()
    cached = _shortable_cache.get(ticker)
    if cached and (now - cached.get('ts', 0)) < _SHORTABLE_CACHE_TTL:
        return {k: v for k, v in cached.items() if k != 'ts'}
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return {}
    try:
        from ib_insync import Stock
        contract = Stock(ticker, 'SMART', 'USD')
        streamer._ib.qualifyContracts(contract)
        details = streamer._ib.reqContractDetails(contract)
        shares_avail = 0
        fee_rate = 0.0
        easy = False
        if details:
            cd = details[0]
            shares_avail = getattr(cd, 'shortableShares', 0) or 0
            long_name = str(getattr(cd, 'longName', '') or '').lower()
            if 'hard' in long_name or 'ntb' in long_name:
                fee_rate = 5.0
            elif shares_avail > 100000:
                fee_rate = 0.25
                easy = True
            else:
                fee_rate = 1.0
                easy = shares_avail > 10000
        result = {
            'shares_available': int(shares_avail),
            'fee_rate': round(fee_rate, 2),
            'easy_to_borrow': easy,
            'ts': now,
        }
        _shortable_cache[ticker] = result
        return {k: v for k, v in result.items() if k != 'ts'}
    except Exception:
        return {}


# ── IEX Depth of Book (market depth) ────────────────────────────

_depth_cache: dict = {}
_DEPTH_CACHE_TTL = 5
_depth_subscribed: set = set()


def get_market_depth(ticker: str):
    """Get latest market depth snapshot for a ticker.

    Returns {bids: [(price, size), ...], asks: [(price, size), ...]}
    Auto-subscribes on first call. Requires IEX Depth of Book subscription.
    """
    import time
    now = time.time()
    cached = _depth_cache.get(ticker)
    if cached and (now - cached.get('ts', 0)) < _DEPTH_CACHE_TTL:
        return {k: v for k, v in cached.items() if k != 'ts'}
    if ticker not in _depth_subscribed:
        _subscribe_market_depth(ticker)
    # Re-read cache in case callback fired during subscription
    cached = _depth_cache.get(ticker)
    if cached:
        return {k: v for k, v in cached.items() if k != 'ts'}
    return {'bids': [], 'asks': []}


def _subscribe_market_depth(ticker: str) -> bool:
    """Subscribe to market depth for a ticker via IBKR reqMktDepth.

    In ib_insync 0.9.86, reqMktDepth is a PURELY SYNCHRONOUS method — it
    calls self.client.reqMktDepth() directly (no util.run()). This means it
    CAN be safely dispatched via call_soon_threadsafe onto the event loop.

    Futures contracts are qualified FIRST using the async pattern
    (run_coroutine_threadsafe + reqContractDetailsAsync) because
    qualifyContracts() DOES use util.run() which would fail from the wrong
    thread/loop on Python 3.12+.
    """
    if ticker in _depth_subscribed:
        return True
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return False

    # Step 1: Create contract
    contract = streamer._make_contract(ticker)
    if not contract:
        return False

    # Step 2: Qualify futures contracts (async dispatch needed — qualifyContracts uses util.run())
    if contract.secType == 'FUT':
        is_valid, reason = _validate_futures_contract(contract)
        if not is_valid:
            logger.debug(f"Depth subscribe skip: {ticker} | type=FUT | reason={reason}")
            return False

        cache_key = _get_cache_key(contract)
        cached = _cache_get(cache_key)
        if cached:
            contract = cached
        elif _is_invalid_cached(cache_key):
            logger.debug(f"Depth subscribe skip: {ticker} | type=FUT | reason=CACHED_INVALID")
            return False
        else:
            qual_result: list = [contract]
            qual_event = threading.Event()

            async def _qualify():
                try:
                    if not hasattr(streamer._ib, 'reqContractDetailsAsync'):
                        qual_event.set()
                        return
                    dl = await streamer._ib.reqContractDetailsAsync(contract)
                    if not dl:
                        await asyncio.sleep(3)
                        dl = await streamer._ib.reqContractDetailsAsync(contract)
                    if dl:
                        qc = dl[0].contract
                        if contract.exchange:
                            qc.exchange = contract.exchange
                        _cache_set(cache_key, qc)
                        qual_result[0] = qc
                    else:
                        _cache_invalid(cache_key)
                except Exception as e:
                    logger.debug(f"IBKR qualify failed for depth {ticker}: {e}")
                finally:
                    qual_event.set()

            asyncio.run_coroutine_threadsafe(_qualify(), streamer._loop)
            qual_event.wait(timeout=15)
            contract = qual_result[0]

    # Step 3: Wire up depth callback
    # marketDepthEvent signature: (contract, bids, asks, is_mkt_depth)
    # marketDepthEvent fires for ALL depth-subscribed tickers, not just this one.
    # Filter by checking _contract.symbol against our expected ticker to avoid
    # cross-ticker data corruption (one ticker's depth overwriting another's cache).
    _depth_ticker = ticker
    _depth_base = ticker.replace('=F', '') if ticker.endswith('=F') else ticker
    def _on_depth(_contract, bids_list, asks_list, _depth_flag):
        # Skip if this update is for a different ticker
        if _contract and hasattr(_contract, 'symbol'):
            if _contract.symbol != _depth_base:
                return
        _depth_cache[_depth_ticker] = {
            'bids': [(float(b.price), int(b.size)) for b in (bids_list or [])[:10]],
            'asks': [(float(a.price), int(a.size)) for a in (asks_list or [])[:10]],
            'ts': time.time(),
        }
    try:
        streamer._ib.marketDepthEvent += _on_depth
    except Exception:
        pass

    # Step 4: Subscribe via reqMktDepth (sync, no util.run() — safe for call_soon_threadsafe)
    result_container: list = [False]
    event = threading.Event()

    def _do_subscribe():
        try:
            logger.debug(f"IBKR depth: calling reqMktDepth for {ticker} (contract={contract.symbol}/{contract.secType}/{contract.exchange})")
            streamer._ib.reqMktDepth(contract, numRows=10, isSmartDepth=False)
            _depth_subscribed.add(ticker)
            result_container[0] = True
        except Exception as e:
            logger.debug(f"IBKR depth subscribe FAILED for {ticker}: {e}")
        finally:
            event.set()

    streamer._loop.call_soon_threadsafe(_do_subscribe)
    event.wait(timeout=15)
    return result_container[0]


def get_order_book_imbalance(ticker: str) -> dict:
    """Compute order book imbalance from market depth.

    Returns {imbalance_ratio: float, bid_volume: int, ask_volume: int,
             direction: str, pressure: float}
    where positive imbalance = buying pressure.
    """
    depth = get_market_depth(ticker)
    bids = depth.get('bids', [])
    asks = depth.get('asks', [])
    bid_vol = sum(s for _, s in bids[:5])
    ask_vol = sum(s for _, s in asks[:5])
    total = bid_vol + ask_vol
    if total == 0:
        return {'imbalance_ratio': 0.0, 'bid_volume': 0, 'ask_volume': 0,
                'direction': 'neutral', 'pressure': 0.0}
    ratio = round((bid_vol - ask_vol) / total, 4)
    direction = 'bullish' if ratio > 0.1 else ('bearish' if ratio < -0.1 else 'neutral')
    return {
        'imbalance_ratio': ratio,
        'bid_volume': bid_vol,
        'ask_volume': ask_vol,
        'direction': direction,
        'pressure': round(abs(ratio), 4),
    }


# ── Tick-by-tick historical data ────────────────────────────────

_tick_cache: dict = {}
_TICK_CACHE_TTL = 30


def fetch_historical_ticks(ticker: str, duration_sec: int = 600) -> Optional[list]:
    """Fetch tick-by-tick trade data from IBKR (thread-safe).

    Args:
        ticker: Yahoo-style ticker (e.g. 'AAPL', 'ES=F')
        duration_sec: How far back to fetch (max ~1000 ticks, ~10 min for active tickers)

    Returns:
        List of dicts {price, size, timestamp} or None if IBKR unavailable.
    """
    key = f"{ticker}_{duration_sec}"
    now = time.time()
    cached = _tick_cache.get(key)
    if cached and (now - cached['ts']) < _TICK_CACHE_TTL:
        return cached['data']
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return None
    contract = streamer._make_contract(ticker)
    if not contract:
        return None

    # Futures contract qualification (same pattern as fetch_historical_bars)
    if contract.secType == 'FUT':
        is_valid, reason = _validate_futures_contract(contract)
        if not is_valid:
            logger.debug(f"Historical ticks skip: {ticker} | type=FUT | reason={reason}")
            return None
        cache_key = _get_cache_key(contract)
        cached = _cache_get(cache_key)
        if cached:
            contract = cached
        elif _is_invalid_cached(cache_key):
            logger.debug(f"Historical ticks skip: {ticker} | type=FUT | reason=CACHED_INVALID")
            return None
        else:
            qual_result: list = [contract]
            qual_event = threading.Event()
            async def _qualify():
                try:
                    if not hasattr(streamer._ib, 'reqContractDetailsAsync'):
                        qual_event.set(); return
                    details_list = await streamer._ib.reqContractDetailsAsync(contract)
                    if not details_list:
                        await asyncio.sleep(3)
                        details_list = await streamer._ib.reqContractDetailsAsync(contract)
                    if details_list:
                        qc = details_list[0].contract
                        if contract.exchange:
                            qc.exchange = contract.exchange
                        _cache_set(cache_key, qc)
                        qual_result[0] = qc
                    else:
                        _cache_invalid(cache_key)
                except Exception as e:
                    logger.debug(f"IBKR qualify failed for ticks {ticker}: {e}")
                finally:
                    qual_event.set()
            asyncio.run_coroutine_threadsafe(_qualify(), streamer._loop)
            qual_event.wait(timeout=15)
            contract = qual_result[0]

    # Use run_coroutine_threadsafe + reqHistoricalTicksAsync (same pattern as
    # fetch_historical_bars) to dispatch onto the streamer's event loop.
    from datetime import datetime as _dt, timedelta as _td
    end = _dt.now()
    start = end - _td(seconds=duration_sec)
    
    result_container: list = [None]
    event = threading.Event()

    async def _fetch_async():
        try:
            ticks = await streamer._ib.reqHistoricalTicksAsync(
                contract,
                startDateTime=start.strftime('%Y%m%d %H:%M:%S'),
                endDateTime=end.strftime('%Y%m%d %H:%M:%S'),
                numberOfTicks=1000,
                whatToShow='TRADES',
                useRth=False,
                ignoreSize=False,
            )
            result = []
            if ticks:
                for t in ticks:
                    result.append({
                        'price': float(t.price),
                        'size': int(t.size),
                        'timestamp': t.time.isoformat() if hasattr(t.time, 'isoformat') else str(t.time),
                        'side': '?',
                    })
            result_container[0] = result
            _raw_ts = time.time()
            _tick_cache[key] = {'ts': _raw_ts, 'data': result}
        except Exception as e:
            logger.debug(f"IBKR historical ticks failed for {ticker}: {e}")
            result_container[0] = None
        finally:
            event.set()

    asyncio.run_coroutine_threadsafe(_fetch_async(), streamer._loop)
    event.wait(timeout=15)
    return result_container[0]


# ── Real-time news headlines ─────────────────────────────────────



# ── Market scanners ──────────────────────────────────────────────

_scanner_cache: dict = {}
_SCANNER_CACHE_TTL = 30


def run_scanner(scan_code: str = 'TOP_PERC_GAIN', max_results: int = 20) -> list:
    """Run an IBKR market scanner (thread-safe).

    Args:
        scan_code: IBKR scan code — 'TOP_PERC_GAIN', 'TOP_PERC_LOSE',
                   'HOT_BY_VOLUME', 'MOST_ACTIVE', 'TOP_TRADE_COUNT',
                   'HIGH_OPEN_GAP', 'LOW_OPEN_GAP'
        max_results: Max tickers to return

    Returns:
        List of dicts {ticker, price, change_pct, volume} or empty list.
    """
    import time as _time
    now = _time.time()
    cached = _scanner_cache.get(scan_code)
    if cached and (now - cached['ts']) < _SCANNER_CACHE_TTL:
        return cached['data'][:max_results]
    streamer = get_streamer()
    if not streamer or not streamer._ib or not streamer._ib.isConnected():
        return []
    result = []
    event = threading.Event()

    def _do_fetch():
        nonlocal result
        try:
            from ib_insync import ScannerSubscription
            sub = ScannerSubscription(
                instrument='STK',
                locationCode='STK.US.MAJOR',
                scanCode=scan_code,
                numberOfRows=max_results,
            )
            scan_data = streamer._ib.reqScannerSubscription(sub)
            if scan_data:
                for s in scan_data[:max_results]:
                    cd = s.contractDetails
                    result.append({
                        'ticker': cd.contract.symbol,
                        'price': float(getattr(cd, 'marketPrice', 0) or 0),
                        'change_pct': round(float(getattr(s, 'distance', 0) or 0), 2),
                        'volume': int(getattr(cd, 'volume', 0) or 0),
                    })
            streamer._ib.cancelScannerSubscription(sub)
        except Exception as e:
            logger.debug(f"IBKR scanner {scan_code} failed: {e}")
        finally:
            event.set()

    streamer._loop.call_soon_threadsafe(_do_fetch)
    event.wait(timeout=15)
    if result:
        _scanner_cache[scan_code] = {'ts': now, 'data': result}
    return result[:max_results]
