"""
Lean Signals — Signal-Only Cycle Runner.

On each cycle:
1. Check IBKR connection
2. Pre-fetch all data per ticker → engine/data_prep.py
3. Run V2/V3 strategies → Consensus → Gate → engine/strategy_eval.py
4. Build result + persistence → engine/signal_assembly.py
5. No portfolio, no risk gates, no position sizing
"""
import time
import json
import threading
from datetime import datetime
from pathlib import Path

_cycle_lock = threading.Lock()

from utils.logger import get_logger
from utils.time_utils import now_iso, now_ny
from datetime import datetime, timezone as tz
from engine.ibkr_connector import is_connected, get_connection_status
from engine.subscription_manager import (
    refresh, reset_non_pinned, get_slot_summary,
    seed_fixed_options,
)

logger = get_logger("engine.runner")

DATA_DIR = Path(__file__).parent.parent / "data"
CYCLE_HISTORY_FILE = DATA_DIR / "cycle_history.json"

MAX_HISTORY = 200

_cycle_in_progress = False
_cycle_count = 0
_CYCLE_MAX_SECONDS = 180  # watchdog: if a cycle runs > 3 min, something is wrong
_last_cycle_result = None
_cycle_history = []
_auto_run_enabled = False
_auto_run_thread = None
_stop_auto_run = threading.Event()
_stale_consecutive = 0  # consecutive cycles with stale prices


def _load_history():
    global _cycle_history
    try:
        if CYCLE_HISTORY_FILE.exists():
            with open(CYCLE_HISTORY_FILE) as f:
                _cycle_history = json.load(f)
    except Exception:
        _cycle_history = []


def _save_history():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CYCLE_HISTORY_FILE, "w") as f:
            json.dump(_cycle_history, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Failed to save history: {e}")


def _is_new_trading_day(last_utc_iso: str, now_ny_dt: datetime) -> bool:
    """True if last stored cycle is from a prior trading day in Eastern time."""
    try:
        last_utc = datetime.fromisoformat(last_utc_iso)
        if last_utc.tzinfo is None:
            last_utc = last_utc.replace(tzinfo=tz.utc)
        last_et = last_utc.astimezone(now_ny_dt.tzinfo)
        return last_et.date() < now_ny_dt.date()
    except Exception:
        return False


def get_status() -> dict:
    option_health = []
    try:
        from engine.option_metrics import get_option_health_events
        option_health = get_option_health_events()[-10:]  # last 10
    except Exception:
        pass
    try:
        from kronos.countries.usa.market_hours import market_summary
        ms = market_summary()
    except Exception:
        ms = {"open": False, "label": "Unknown", "futures_open": False}
    return {
        "cycle_in_progress": _cycle_in_progress,
        "cycle_count": _cycle_count,
        "auto_run": _auto_run_enabled,
        "last_cycle": _last_cycle_result,
        "history": _cycle_history[:MAX_HISTORY],
        "connection": get_connection_status(),
        "slot_usage": get_slot_summary(),
        "option_health": option_health,
        "market_hours": ms,
    }


def run_cycle() -> dict:
    global _cycle_in_progress, _cycle_count, _last_cycle_result

    with _cycle_lock:
        if _cycle_in_progress:
            return {"status": "skipped", "reason": "cycle_in_progress"}
        _cycle_in_progress = True
        _cycle_count += 1
        cycle_id = _cycle_count

    start_time = time.time()
    logger.info(f"=== Cycle #{cycle_id} started ===")

    try:
        # ── Connection check ──
        conn = is_connected()
        if not conn:
            result = _error_result(cycle_id, "ibkr_not_connected")
            _last_cycle_result = result
            _cycle_history.insert(0, result)
            _save_history()
            logger.warning("Cycle skipped: IBKR not connected")
            return result

        # ── Stale data guard ──
        global _stale_consecutive
        try:
            from engine.ibkr_data_feed import get_all_live_prices, clear_live_prices
            prices = get_all_live_prices()
            fresh_count = sum(1 for p in prices.values() if p.get("age_seconds", 999) < 300)
            stale_count = len(prices) - fresh_count
            if len(prices) >= 4 and stale_count >= len(prices) // 2:
                _stale_consecutive += 1
                if _stale_consecutive >= 3:
                    logger.error(
                        "All prices stale for %d consecutive cycles (stale=%d/%d). "
                        "Resetting data feed...",
                        _stale_consecutive, stale_count, len(prices),
                    )
                    clear_live_prices()
                    from engine.tick_engine import reset as reset_tick_engine
                    reset_tick_engine()
                    _stale_consecutive = 0
                    result = _error_result(cycle_id, "stale_data_reset")
                    _last_cycle_result = result
                    _cycle_history.insert(0, result)
                    _save_history()
                    logger.info("Stale data reset complete — next cycle will start fresh")
                    return result
            else:
                _stale_consecutive = 0
        except Exception as e:
            logger.debug("Stale data check failed: %s", e)

        # ── Market-open history reset (use market_summary instead of hardcoded 9:30) ──
        try:
            from kronos.countries.usa.market_hours import market_summary
            ms = market_summary()
            now_et = now_ny()
            if ms.get("is_open", False) and _cycle_history:
                last_ts = _cycle_history[0].get("timestamp", "")
                if last_ts and _is_new_trading_day(last_ts, now_et):
                    _cycle_history.clear()
                    logger.info("Market open: cleared previous cycle history")
        except Exception:
            pass

        reset_non_pinned()
        seed_fixed_options()

        # ── Phase 1-6: Data preparation ──
        from engine.data_prep import prepare_all_data
        prep = prepare_all_data(cycle_id, start_time)
        ticker_data_map = prep["ticker_data_map"]
        all_tickers = prep["all_tickers"]
        account_data = prep["account_data"]
        positions_data = prep["positions_data"]
        daytype_prediction = prep["daytype_prediction"]

        # ── Watchdog check after data prep ──
        elapsed_total = time.time() - start_time
        if elapsed_total > _CYCLE_MAX_SECONDS:
            logger.error(
                "Cycle #%d: ABORTING — exceeded %ds max (%.1fs elapsed)",
                cycle_id, _CYCLE_MAX_SECONDS, elapsed_total,
            )
            raise TimeoutError(f"Cycle exceeded {_CYCLE_MAX_SECONDS}s max duration")

        # ── Phase 7: Strategy evaluation ──
        logger.info("Cycle #%d: running strategies on %d tickers (phase 7/7)", cycle_id, len(ticker_data_map))
        from engine.strategy_eval import evaluate_tickers
        eval_result = evaluate_tickers(
            ticker_data_map, all_tickers, account_data, positions_data,
            cycle_id, start_time,
        )
        signals = eval_result["signals"]
        gate_evaluations = eval_result["gate_evaluations"]

        # ── Phase 8: Result assembly + persistence ──
        slot_refresh = refresh()
        from engine.signal_assembly import assemble_cycle_result
        result = assemble_cycle_result(
            signals, gate_evaluations, all_tickers, cycle_id, start_time,
            daytype_prediction, account_data, positions_data, slot_refresh,
        )

        _last_cycle_result = result
        _cycle_history.insert(0, result)
        logger.info("Cycle #%d: saving history", cycle_id)
        _save_history()

        elapsed = result["elapsed_seconds"]
        logger.info(f"Cycle #{cycle_id} done: {len(signals)} signals in {elapsed:.1f}s")
        return result

    except Exception as e:
        logger.error(f"Cycle #{cycle_id} failed: {e}", exc_info=True)
        result = _error_result(cycle_id, str(e))
        _last_cycle_result = result
        _cycle_history.insert(0, result)
        _save_history()
        return result

    finally:
        with _cycle_lock:
            _cycle_in_progress = False


def _error_result(cycle_id: int, reason: str) -> dict:
    return {
        "cycle_id": cycle_id,
        "status": "error",
        "reason": reason,
        "timestamp": now_iso(),
        "signals": [],
        "gate_evaluations": [],
        "gate_rejections": [],
        "gate_rejection_breakdown": {},
        "daytype_prediction": {"prediction": "unknown", "confidence": 0, "trained": False},
        "account_summary": {},
    }


def _auto_run_loop():
    while not _stop_auto_run.is_set():
        run_cycle()
        for _ in range(60):
            if _stop_auto_run.is_set():
                return
            time.sleep(1)


def start_auto_run():
    global _auto_run_enabled, _auto_run_thread, _stop_auto_run
    if _auto_run_enabled:
        return
    _auto_run_enabled = True
    _stop_auto_run.clear()
    _auto_run_thread = threading.Thread(target=_auto_run_loop, daemon=True)
    _auto_run_thread.start()
    logger.info("Auto-run started")


def stop_auto_run():
    global _auto_run_enabled
    _auto_run_enabled = False
    _stop_auto_run.set()
    logger.info("Auto-run stopped")


def init():
    global _stale_consecutive, _cycle_history, _cycle_count
    _stale_consecutive = 0
    _cycle_count = 0

    # ── Always start with a clean tick state ──
    # Loading a stale checkpoint from a prior session corrupts cumulative delta
    # and tick buffers, causing strategies to see phantom data.
    from engine.tick_engine import reset as reset_tick_engine
    reset_tick_engine()

    # ── Clear stale cycle history — prevents dashboard showing 49MB of old data ──
    _cycle_history.clear()
    _save_history()

    # ── Clear stale signal persistence ──
    try:
        state_file = DATA_DIR / "signal_state.json"
        if state_file.exists():
            state_file.unlink()
            logger.info("Cleared stale signal_state.json")
    except Exception as e:
        logger.debug("Could not clear signal_state.json: %s", e)

    from engine.ibkr_data_feed import clear_live_prices, get_streamer, IBKRStreamer
    from utils.config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID

    # Force-stop any existing streamer to avoid stale connections
    old_streamer = get_streamer()
    if old_streamer is not None:
        try:
            old_streamer.stop()
        except Exception:
            pass

    clear_live_prices()
    streamer = IBKRStreamer(
        host=IBKR_HOST, port=IBKR_PORT, client_id=IBKR_CLIENT_ID
    )
    from engine.ibkr_data_feed import set_streamer
    set_streamer(streamer)
    streamer.start()
    logger.info("IBKR streamer started (fresh)")

    from engine.ibkr_connector import start_monitoring
    start_monitoring(interval=10)
    from engine.daytype_model import init as init_daytype
    init_daytype()
    start_auto_run()
