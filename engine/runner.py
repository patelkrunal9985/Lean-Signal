"""
Lean Signals — Signal-Only Cycle Runner.

On each cycle:
1. Check IBKR connection
2. Use SubscriptionManager to determine ticker slots
3. Fetch data for each ticker (IBKR primary)
4. Run V2 strategies → V3 strategies → Consensus → Output signals
5. No position management, no risk gates, no SL/TP
"""
import time
import json
import threading
from datetime import datetime
from typing import Optional
from pathlib import Path

from utils.logger import get_logger
from utils.time_utils import now_iso, now_ny
from engine.ibkr_connector import is_connected, get_connection_status
from engine.subscription_manager import (
    set_priority, refresh, reset_non_pinned, get_slot_summary,
    seed_fixed_options, sync_from_ibkr,
)

logger = get_logger("engine.runner")

DATA_DIR = Path(__file__).parent.parent / "data"
CYCLE_HISTORY_FILE = DATA_DIR / "cycle_history.json"

MAX_HISTORY = 5

_cycle_in_progress = False
_cycle_count = 0
_last_cycle_result = None
_cycle_history = []
_auto_run_enabled = False
_auto_run_thread = None
_stop_auto_run = threading.Event()


def _load_history():
    global _cycle_history
    try:
        if CYCLE_HISTORY_FILE.exists():
            with open(CYCLE_HISTORY_FILE) as f:
                _cycle_history = json.load(f)[:MAX_HISTORY]
    except Exception:
        _cycle_history = []


def _save_history():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CYCLE_HISTORY_FILE, "w") as f:
            json.dump(_cycle_history[:MAX_HISTORY], f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Failed to save history: {e}")


def get_status() -> dict:
    return {
        "cycle_in_progress": _cycle_in_progress,
        "cycle_count": _cycle_count,
        "auto_run": _auto_run_enabled,
        "last_cycle": _last_cycle_result,
        "history": _cycle_history[:MAX_HISTORY],
        "connection": get_connection_status(),
        "slot_usage": get_slot_summary(),
    }


def run_cycle() -> dict:
    global _cycle_in_progress, _cycle_count, _last_cycle_result

    if _cycle_in_progress:
        return {"status": "skipped", "reason": "cycle_in_progress"}

    _cycle_in_progress = True
    _cycle_count += 1
    cycle_id = _cycle_count
    start_time = time.time()
    logger.info(f"=== Cycle #{cycle_id} started ===")

    try:
        conn = is_connected()
        if not conn:
            result = {
                "cycle_id": cycle_id,
                "status": "error",
                "reason": "ibkr_not_connected",
                "timestamp": now_iso(),
                "signals": [],
            }
            _last_cycle_result = result
            _cycle_history.insert(0, result)
            _cycle_history[:] = _cycle_history[:MAX_HISTORY]
            _save_history()
            logger.warning("Cycle skipped: IBKR not connected")
            return result

        from engine.ibkr_data_feed import (
            fetch_historical_bars, get_live_price, get_market_depth,
            fetch_option_chain_ibkr, subscribe_ticker,
        )
        from engine.v2.registry import V2StrategyRegistry
        from engine.v3.registry import get_strategies
        from engine.v3.gate import FiveLayerGate
        from engine.consensus_coordinator import compute_consensus
        from engine.real_time_data_feeds import RealTimeDataFeedsSkill
        from regime.detector import RegimeDetector
        from indicators.advanced_indicators import compute_all_advanced

        reset_non_pinned()
        seed_fixed_options()
        slot_info = get_slot_summary()

        data_feeds = RealTimeDataFeedsSkill()
        regime_detector = RegimeDetector()

        all_tickers = []
        from utils.config import FIXED_STOCKS, FIXED_FUTURES
        for t in FIXED_STOCKS:
            all_tickers.append((t, "stock"))
        for t in FIXED_FUTURES:
            all_tickers.append((t, "future"))

        ticker_data_map = {}
        for ticker, instr_type in all_tickers:
            try:
                subscribe_ticker(ticker)
            except Exception:
                pass

            ohlcv = []
            try:
                bars = fetch_historical_bars(ticker, "2 W", "1 day")
                if bars:
                    ohlcv = bars
            except Exception:
                pass

            live_price = 0
            try:
                q = get_live_price(ticker)
                if q:
                    live_price = q.get("price", 0)
            except Exception:
                pass

            depth_data = {}
            try:
                depth_data = get_market_depth(ticker)
            except Exception:
                pass

            fundamentals = data_feeds.get_fundamentals(ticker)
            news = data_feeds.get_news(ticker, max_items=3)
            sentiment = data_feeds.get_sentiment(ticker)
            earnings = data_feeds.get_earnings(ticker)
            insider = data_feeds.get_insider_trades(ticker)

            indicators = {}
            if ohlcv:
                try:
                    indicators = compute_all_advanced(ohlcv)
                except Exception:
                    pass

            ticker_data_map[ticker] = {
                "ticker": ticker,
                "instrument_type": instr_type,
                "ohlcv": ohlcv,
                "current_price": live_price,
                "depth": depth_data,
                "indicators": indicators,
                "fundamentals": fundamentals,
                "earnings": earnings,
                "insider_trades": insider,
                "news": news,
                "sentiment": sentiment,
                "data_source": "ibkr" if live_price > 0 else "ohlcv",
            }

        signals = []
        v2_registry = V2StrategyRegistry()
        gate = FiveLayerGate()

        for ticker, data in ticker_data_map.items():
            instr_type = data["instrument_type"]
            context = data.copy()

            v2_results = v2_registry.run_all(context)
            v3_strategies = []
            if instr_type in ("stock", "future", "option"):
                v3_strategies = get_strategies(instr_type)

            v3_results_raw = []
            for strategy in v3_strategies:
                try:
                    result = strategy.compute(context)
                    if isinstance(result, dict):
                        direction = result.get("direction", "neutral")
                        confidence = result.get("confidence", 0)
                        if direction in ("long", "short") and confidence > 0:
                            v3_results_raw.append({
                                "name": strategy.name,
                                "strategy": strategy.name,
                                "direction": direction,
                                "confidence": float(confidence),
                                "source": "v3",
                                "reasoning": result.get("reasoning", ""),
                            })
                except Exception:
                    pass

            regime = regime_detector.detect(data.get("ohlcv", []), data.get("indicators", {}))

            atr = data.get("indicators", {}).get("atr_14", 0)
            sma_50 = data.get("indicators", {}).get("sma_50", 0)

            direction, conf, consensus_meta = compute_consensus(
                v2_results, v3_results_raw, None, regime.get("primary_regime", "ranging"),
                ticker, instr_type, data.get("current_price", 0), atr, sma_50,
            )

            all_strategy_votes = v2_results + v3_results_raw

            set_priority(ticker, instr_type, int(conf * 100))

            gate_result = gate.process_ticker(
                ticker_data=data,
                portfolio_state={"account_value": 50000, "positions": []},
                active_strategies=all_strategy_votes,
                regime=regime,
            )

            signal = {
                "ticker": ticker,
                "instrument_type": instr_type,
                "direction": direction if direction != "neutral" else gate_result.get("direction", "neutral"),
                "confidence": round(conf, 4),
                "composite_score": round(conf, 4),
                "strategy_count": len(all_strategy_votes),
                "agreeing_count": len([s for s in all_strategy_votes if s.get("direction") == direction]),
                "regime": regime.get("primary_regime", "unknown"),
                "regime_confidence": round(regime.get("confidence", 0), 2),
                "current_price": data.get("current_price", 0),
                "action": gate_result.get("action", "skip"),
                "strategies": [
                    {
                        "name": s.get("name", s.get("strategy", "?")),
                        "direction": s.get("direction", "neutral"),
                        "confidence": round(float(s.get("confidence", 0)), 4),
                        "source": s.get("source", "?"),
                        "reasoning": s.get("reasoning", ""),
                    }
                    for s in all_strategy_votes if float(s.get("confidence", 0)) > 0
                ],
                "posterior": round(gate_result.get("posterior", 0.5), 4),
                "news": data.get("news", []),
                "sentiment": data.get("sentiment", {}),
                "consensus_meta": consensus_meta,
            }

            if direction != "neutral":
                signals.append(signal)
                set_priority(ticker, instr_type, 5000)

        signals.sort(key=lambda s: s["composite_score"], reverse=True)

        slot_refresh = refresh()

        elapsed = time.time() - start_time
        result = {
            "cycle_id": cycle_id,
            "status": "completed",
            "timestamp": now_iso(),
            "elapsed_seconds": round(elapsed, 2),
            "tickers_scanned": len(all_tickers),
            "signals_count": len(signals),
            "slot_refresh": slot_refresh,
            "signals": {
                "stock": [s for s in signals if s["instrument_type"] == "stock"],
                "future": [s for s in signals if s["instrument_type"] == "future"],
                "option": [s for s in signals if s["instrument_type"] == "option"],
            },
        }

        _last_cycle_result = result
        _cycle_history.insert(0, result)
        _cycle_history[:] = _cycle_history[:MAX_HISTORY]
        _save_history()

        logger.info(f"Cycle #{cycle_id} done: {len(signals)} signals in {elapsed:.1f}s")
        return result

    except Exception as e:
        logger.error(f"Cycle #{cycle_id} failed: {e}", exc_info=True)
        result = {
            "cycle_id": cycle_id,
            "status": "error",
            "reason": str(e),
            "timestamp": now_iso(),
            "signals": [],
        }
        _last_cycle_result = result
        _cycle_history.insert(0, result)
        _cycle_history[:] = _cycle_history[:MAX_HISTORY]
        _save_history()
        return result

    finally:
        _cycle_in_progress = False


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
    _load_history()
    from engine.ibkr_data_feed import get_streamer, IBKRStreamer
    from utils.config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID
    streamer = get_streamer()
    if streamer is None:
        streamer = IBKRStreamer(host=IBKR_HOST, port=IBKR_PORT, client_id=IBKR_CLIENT_ID)
        from engine.ibkr_data_feed import set_streamer
        set_streamer(streamer)
        streamer.start()
        logger.info("IBKR streamer started")
    from engine.ibkr_connector import start_monitoring
    start_monitoring(interval=10)
