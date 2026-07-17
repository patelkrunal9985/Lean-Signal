"""
Lean Signals — Signal-Only Cycle Runner.

On each cycle:
1. Check IBKR connection
2. Pre-fetch all data per ticker (IBKR-only, no fallbacks)
3. Compute session context (VWAP, opening range) for market structure
4. Run V2 strategies → V3 strategies → Consensus → Gate → Signals
5. Strike selection + Position sizing for 0DTE options
6. No portfolio, no risk gates, no position sizing
"""
import time
import json
import re
import threading
import traceback
from datetime import datetime
from typing import Optional
from pathlib import Path

_cycle_lock = threading.Lock()

from utils.logger import get_logger
from utils.time_utils import now_iso, now_ny
from datetime import datetime, timezone as tz
from engine.ibkr_connector import is_connected, get_connection_status
from engine.subscription_manager import (
    set_priority, refresh, reset_non_pinned, get_slot_summary,
    seed_fixed_options, sync_from_ibkr,
)

logger = get_logger("engine.runner")

DATA_DIR = Path(__file__).parent.parent / "data"
CYCLE_HISTORY_FILE = DATA_DIR / "cycle_history.json"

MAX_HISTORY = 200

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


# Strip "_<digit>..." suffixes from gate reasons so variable numerics (e.g.
# "within_15min_of_close", "atr_too_high_0.0821", "stale_price_5s") collapse
# into a single bucket per failure mode without colliding on the first word.
_GATE_REASON_BUCKET_RE = re.compile(r"_\d.*$")


def _summarize_gate_rejections(rejections: list) -> dict:
    """Group gate rejections by reason prefix for an at-a-glance summary.

    Bucket key construction
    ------------------------
    Strip any "_<digit>..." suffix so variable numerics don't split buckets.
    Reasons without any numeric suffix keep their full text.

    Examples
    --------
    "incomplete_data_cot_empty" → "incomplete_data_cot_empty"
    "too_close_to_close"        → "too_close_to_close"
    "within_15min_of_close"     → "within"
    "atr_too_high_0.0821"       → "atr_too_high"
    "stale_price_5s"            → "stale_price"
    """
    summary: dict[str, int] = {}
    for r in rejections:
        reason = r.get("gate_reason") or "unknown"
        bucket = _GATE_REASON_BUCKET_RE.sub("", reason)
        summary[bucket] = summary.get(bucket, 0) + 1
    return summary


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

    with _cycle_lock:
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
                "gate_evaluations": [],
                "gate_rejections": [],
                "gate_rejection_breakdown": {},
            }
            _last_cycle_result = result
            _cycle_history.insert(0, result)
            _save_history()
            logger.warning("Cycle skipped: IBKR not connected")
            return result

        # ── Market-open history reset ──
        # First cycle after 9:30 AM ET wipes previous day's history.
        # This keeps all intraday cycles visible while preventing stale
        # data from carrying across trading days.
        now_et = now_ny()
        market_open_minutes = 9 * 60 + 30
        current_et_minutes = now_et.hour * 60 + now_et.minute
        if current_et_minutes >= market_open_minutes and _cycle_history:
            last_ts = _cycle_history[0].get("timestamp", "")
            if last_ts and _is_new_trading_day(last_ts, now_et):
                _cycle_history.clear()
                logger.info("New trading day after market open: cleared previous day's history")

        from engine.ibkr_data_feed import (
            fetch_historical_bars, get_live_price, get_market_depth,
            fetch_option_chain_ibkr, subscribe_ticker,
        )
        from engine.v2.registry import V2StrategyRegistry
        from engine.v3.registry import get_strategies
        from engine.v3.gate import SignalQualityGate
        from engine.consensus_coordinator import compute_consensus
        from engine.real_time_data_feeds import RealTimeDataFeedsSkill
        from regime.detector import RegimeDetector
        from indicators.advanced_indicators import compute_all_advanced
        from engine.cot_fetcher import get_cot_for_ticker, _TICKER_TO_CFTC
        from engine.entry_exit import compute_entry_exit_levels

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
            incomplete = False
            missing = []

            try:
                subscribe_ticker(ticker)
            except Exception:
                pass

            # ── OHLCV (IBKR primary) ──
            ohlcv = []
            try:
                bars = fetch_historical_bars(ticker, "2 W", "1 day")
                if bars:
                    ohlcv = bars
            except Exception:
                pass

            # ── Live price with freshness tracking ──
            live_price = 0
            price_age = 999.0
            try:
                q = get_live_price(ticker)
                if q:
                    live_price = q.get("price", 0)
                    price_age = q.get("age_seconds", 999.0)
            except Exception:
                pass

            # ── Indicators ──
            indicators = {}
            if ohlcv:
                try:
                    indicators = compute_all_advanced(ohlcv)
                except Exception:
                    pass

            # ── Market depth (IBKR) ──
            depth_data = {}
            try:
                depth_data = get_market_depth(ticker)
            except Exception:
                pass

            # ── COT data (futures only, from CFTC.gov, zero IBKR calls) ──
            cot_data = {}
            if instr_type == "future":
                try:
                    cot_data = get_cot_for_ticker(ticker)
                except Exception:
                    pass
                if ticker in _TICKER_TO_CFTC:
                    total_oi = cot_data.get("total_open_interest", 0) if cot_data else 0
                    comm_long = cot_data.get("commercial_long", 0) if cot_data else 0
                    comm_short = cot_data.get("commercial_short", 0) if cot_data else 0
                    if total_oi == 0 and comm_long == 0 and comm_short == 0:
                        incomplete = True
                        missing.append("cot_empty")

            # ── Fundamentals, news, sentiment ──
            fundamentals = data_feeds.get_fundamentals(ticker)
            news = data_feeds.get_news(ticker, max_items=3)
            sentiment = data_feeds.get_sentiment(ticker)
            earnings = data_feeds.get_earnings(ticker)
            insider = data_feeds.get_insider_trades(ticker)

            # ── Data source: IBKR only ──
            data_source = "ibkr" if live_price > 0 else "ibkr_ohlcv"

            # ── Session context (VWAP, opening range) for market structure ──
            session_context = {}
            try:
                from engine.time_of_day import get_market_session_context
                session_context = get_market_session_context(ohlcv, live_price)
            except Exception:
                pass

            ticker_data_map[ticker] = {
                "ticker": ticker,
                "instrument_type": instr_type,
                "ohlcv": ohlcv,
                "current_price": live_price,
                "price_age_seconds": price_age,
                "depth": depth_data,
                "indicators": indicators,
                "cot": cot_data,
                "fundamentals": fundamentals,
                "earnings": earnings,
                "insider_trades": insider,
                "news": news,
                "sentiment": sentiment,
                "data_source": data_source,
                "incomplete_data": incomplete,
                "missing_fields": ",".join(missing) if missing else "",
                "session_context": session_context,
            }

        # ── Futures cumulative delta from tick engine (real Lee-Ready signed) ──
        # Primary: tick_engine.get_tick_stats() from live IBKR tape.
        # Fallback: compute_cumulative_delta from OHLCV proxy.
        from engine.tick_engine import get_tick_stats
        from engine.futures_data import compute_cumulative_delta
        for t, instr in all_tickers:
            if instr != "future":
                continue
            td = ticker_data_map.get(t, {})
            if not td:
                continue
            try:
                tick_stats = get_tick_stats(t)
                if tick_stats and tick_stats.get("cumulative_delta", 0) != 0:
                    td["cumulative_delta"] = tick_stats
                    td["vpin"] = tick_stats.get("vpin", 0)
                else:
                    ohlcv_1m = td.get("ohlcv_1m", td.get("ohlcv", []))
                    depth = td.get("depth", {})
                    cd = compute_cumulative_delta(ohlcv_1m, depth)
                    if cd:
                        td["cumulative_delta"] = cd
            except Exception:
                pass

        # ── Options processing (sequential full-chain mode) ──
        # Each underlying gets the full 83-line IBKR budget since only one
        # chain is live at a time.  fetch_live_option_prices cancels all
        # subscriptions before returning; we add a small safety delay and
        # force-cancel between underlyings for defense-in-depth.
        from engine.option_metrics import compute_option_metrics
        from engine.ibkr_data_feed import cancel_all_option_subscriptions
        for opt_underlying in ["SPY", "QQQ", "SPX", "NDX"]:
            try:
                underlying_data = ticker_data_map.get(opt_underlying, {})
                underlying_price = underlying_data.get("current_price", 0)
                if underlying_price <= 0:
                    continue
                opt_ctx = compute_option_metrics(opt_underlying, underlying_price, ticker_data_map)
                if opt_ctx:
                    # Propagate session context from underlying to option
                    opt_ctx["session_context"] = underlying_data.get("session_context", {})
                    ticker_data_map[opt_ctx["ticker"]] = opt_ctx
                    all_tickers.append((opt_ctx["ticker"], "option"))
            except Exception as exc:
                logger.warning("Option metrics for %s failed: %s", opt_underlying, exc)
            # Free IBKR market data lines before processing next underlying.
            # fetch_live_option_prices cancels its own contracts, but a safety
            # blanket + 1.5s delay ensures IBKR has processed the cancellations.
            cancel_all_option_subscriptions()
            time.sleep(1.5)

        # ── Market breadth computation ──
        # Compute market breadth context from existing data (ES, NQ, YM, RTY
        # futures + VIX + index performance). Inject into option contexts
        # so breadth confirmation strategy can detect divergences.
        breadth_ctx = {}
        try:
            from engine.market_breadth import compute_market_breadth
            breadth_ctx = compute_market_breadth(ticker_data_map)
            # Inject into all option contexts
            for key, data in ticker_data_map.items():
                if data.get("instrument_type") == "option":
                    data["market_breadth"] = breadth_ctx
        except Exception as exc:
            logger.warning("Market breadth computation failed: %s", exc)

        signals = []
        gate_evaluations: list[dict] = []  # per-ticker audit trail of gate decisions
        v2_registry = V2StrategyRegistry()
        gate = SignalQualityGate()

        for ticker, data in ticker_data_map.items():
            instr_type = data["instrument_type"]
            context = data.copy()

            # ── Run V2 strategies (skip for options — OHLCV on options is noise) ──
            v2_results = []
            if instr_type != "option":
                v2_results = v2_registry.run_all(context)

            # ── Run V3 strategies ──
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
                        # Capture per-strategy diagnostics (all non-core fields)
                        core_fields = {"direction", "confidence", "strategy", "name", "action", "reasoning"}
                        diagnostics = {k: v for k, v in result.items() if k not in core_fields}
                        if direction in ("long", "short") and confidence > 0:
                            v3_results_raw.append({
                                "name": strategy.name,
                                "strategy": strategy.name,
                                "direction": direction,
                                "confidence": float(confidence),
                                "source": "v3",
                                "reasoning": result.get("reasoning", ""),
                                "action": result.get("action", ""),
                                "diagnostics": diagnostics,
                            })
                except Exception:
                    logger.debug(
                        "V3 strategy %s failed on %s: %s",
                        strategy.name, ticker, traceback.format_exc(),
                    )

            # ── Regime detection ──
            regime = regime_detector.detect(
                data.get("ohlcv", []), data.get("indicators", {})
            )

            # ── Consensus ──
            atr = data.get("indicators", {}).get("atr_14", 0)
            sma_50 = data.get("indicators", {}).get("sma_50", 0)
            dte_val = data.get("dte", None)

            direction, conf, consensus_meta = compute_consensus(
                v2_results, v3_results_raw,
                regime.get("primary_regime", "ranging"),
                ticker, instr_type, data.get("current_price", 0), atr, sma_50,
                dte=dte_val,
            )

            all_strategy_votes = v2_results + v3_results_raw
            set_priority(ticker, instr_type, int(conf * 100))

            # ── 3-layer Quality Gate ──
            gate_result = gate.evaluate(
                ticker_data=data,
                signal_direction=direction,
                signal_confidence=conf,
                active_strategies=all_strategy_votes,
                regime=regime,
                consensus_meta=consensus_meta,
            )

            # ── Capture per-ticker gate decision for /api/run-cycle + cycle_history ──
            gate_eval_entry = {
                "ticker": ticker,
                "instrument_type": instr_type,
                "direction": direction,
                "consensus_confidence": round(conf, 4),
                "regime": regime.get("primary_regime", "unknown"),
                "gate_passed": gate_result.get("passed", False),
                "gate_reason": gate_result.get("reason", "unknown"),
                "time_window": gate_result.get("time_window", "unknown"),
                "vwap_position": gate_result.get("vwap_position", "unknown"),
                "strategy_votes": [
                    {
                        "name": s.get("name", s.get("strategy", "?")),
                        "direction": s.get("direction", "neutral"),
                        "confidence": round(float(s.get("confidence", 0)), 4),
                        "source": s.get("source", "?"),
                    }
                    for s in all_strategy_votes
                    if float(s.get("confidence", 0)) > 0
                ],
                "consensus_meta": {
                    "consensus_net_score": consensus_meta.get("net_score", 0),
                    "consensus_active_votes": consensus_meta.get("active_votes", 0),
                    "consensus_neutral_votes": consensus_meta.get("neutral_votes", 0),
                    "consensus_weighted_long": consensus_meta.get("weighted_long", 0),
                    "consensus_weighted_short": consensus_meta.get("weighted_short", 0),
                    "consensus_total_weight": consensus_meta.get("total_weight", 0),
                    "consensus_counter_trend": consensus_meta.get("counter_trend", "no"),
                    "consensus_regime_boost": consensus_meta.get("regime_boost", 0),
                    "consensus_tod_window": consensus_meta.get("tod_window", "—"),
                    "consensus_action": consensus_meta.get("action", "—"),
                },
            }
            gate_evaluations.append(gate_eval_entry)

            # ── Entry/Exit levels ──
            levels = {}
            if direction != "neutral" and gate_result.get("passed", False):
                try:
                    levels = compute_entry_exit_levels(
                        ticker, data, direction, conf, instr_type
                    )
                except Exception:
                    logger.debug("Entry/exit levels failed for %s: %s", ticker, traceback.format_exc())

            # ── Strike selection (options only) ──
            strike_rec = {}
            if instr_type == "option" and direction != "neutral" and gate_result.get("passed", False):
                try:
                    from engine.strike_selector import recommend_strike
                    chain = data.get("option_chain", {})
                    underlying_price = data.get("current_price", data.get("underlying_price", 0))
                    atm_iv = data.get("iv", 0)
                    daily_range_pct = data.get("daily_range", 0)
                    dte_s = data.get("dte", 0)
                    strike_rec = recommend_strike(
                        ticker.replace("_OPT", ""), chain, underlying_price,
                        direction, conf, atm_iv, daily_range_pct, dte_s,
                    )
                except Exception:
                    logger.debug("Strike selection failed for %s: %s", ticker, traceback.format_exc())

            # ── Position: always 1 contract (signal-only system, no portfolio) ──
            position_contracts = 1 if (instr_type == "option" and direction != "neutral") else 0

            # ── Market dashboard: aggregate metrics for option signal popups ──
            market_dashboard = {}
            if instr_type == "option":
                breadth = data.get("market_breadth", {})
                market_dashboard = {
                    "underlying": data.get("underlying", ""),
                    "underlying_price": data.get("underlying_price", 0),
                    "iv": round(data.get("iv", 0) * 100, 1) if data.get("iv", 0) else 0,
                    "hv_10": round(data.get("hv_10", 0) * 100, 1) if data.get("hv_10", 0) else 0,
                    "dte": data.get("dte", 0),
                    "expiry": data.get("expiry", ""),
                    "pc_ratio": round(data.get("pc_ratio", 0), 2),
                    "pc_ratio_5d": round(data.get("pc_ratio_5day_avg", 0), 2),
                    "gamma_flip": round(data.get("gamma_flip_level", 0), 1),
                    "gamma_walls_count": len(data.get("gamma_walls", [])),
                    "delta_positioning": int(data.get("delta_positioning", 0)),
                    "atm_straddle": round(data.get("atm_straddle_price", 0), 2),
                    "skew_1m": round(data.get("skew_term_1m", 0), 1),
                    "charm_direction": data.get("charm_direction", "neutral"),
                    "charm_magnitude": round(data.get("charm_magnitude", 0), 6),
                    "total_vanna": data.get("total_vanna", 0),
                    "total_charm": data.get("total_charm", 0),
                    "vix_spot": round(data.get("vix_spot", 0), 1),
                    "breadth_composite": round(breadth.get("composite", {}).get("composite_score", 0), 2),
                    "breadth_state": breadth.get("composite", {}).get("state", "n/a"),
                    "breadth_trend": breadth.get("breadth_trend", ""),
                    "vix_state": breadth.get("vix_confirmation", {}).get("state", ""),
                    "futures_alignment": round(breadth.get("futures_alignment", {}).get("alignment_ratio", 0) * 100),
                    "tech_divergence": round(breadth.get("tech_divergence", 0), 4),
                    "small_cap_participating": breadth.get("small_cap", {}).get("participating", False),
                    "breadth_thrust": breadth.get("breadth_thrust", {}).get("thrust_active", False),
                }

            signal = {
                "ticker": ticker,
                "instrument_type": instr_type,
                "direction": direction,
                "confidence": round(conf, 4),
                "composite_score": round(conf, 4),
                "strategy_count": len(all_strategy_votes),
                "agreeing_count": len([
                    s for s in all_strategy_votes
                    if s.get("direction") == direction
                ]),
                "regime": regime.get("primary_regime", "unknown"),
                "regime_confidence": round(regime.get("confidence", 0), 2),
                "current_price": data.get("current_price", 0),
                "gate_passed": gate_result.get("passed", False),
                "gate_reason": gate_result.get("reason", "unknown"),
                "entry_price": levels.get("entry_price", data.get("current_price", 0)),
                "stop_loss": levels.get("stop_loss", 0),
                "take_profit": levels.get("take_profit", 0),
                "risk_reward": levels.get("risk_reward", 0),
                "option_entry_premium": levels.get("option_entry_premium", 0),
                "option_sl_premium": levels.get("option_sl_premium", 0),
                "option_tp_premium": levels.get("option_tp_premium", 0),
                "premium_risk_reward": levels.get("premium_risk_reward", 0),
                "recommended_strike": strike_rec.get("recommended_strike", 0),
                "recommended_otm": strike_rec.get("otm_type", ""),
                "recommended_option_type": strike_rec.get("option_type", ""),
                "strike_rationale": strike_rec.get("rationale", ""),
                "estimated_win_rate": strike_rec.get("estimated_win_rate", 0),
                "estimated_payoff": strike_rec.get("estimated_payoff_ratio", 0),
                "position_contracts": position_contracts,
                "time_window": gate_result.get("time_window", "unknown"),
                "vwap_position": gate_result.get("vwap_position", "unknown"),
                "strategies": [
                    {
                        "name": s.get("name", s.get("strategy", "?")),
                        "direction": s.get("direction", "neutral"),
                        "confidence": round(float(s.get("confidence", 0)), 4),
                        "source": s.get("source", "?"),
                        "reasoning": s.get("reasoning", ""),
                        "diagnostics": s.get("diagnostics", {}),
                    }
                    for s in all_strategy_votes
                    if float(s.get("confidence", 0)) > 0
                ],
                "market_dashboard": market_dashboard,
                "news": data.get("news", []),
                "sentiment": data.get("sentiment", {}),
                "consensus_meta": consensus_meta,
            }

            if direction != "neutral" and gate_result.get("passed", False):
                signals.append(signal)
                set_priority(ticker, instr_type, 5000)

        signals.sort(key=lambda s: s["composite_score"], reverse=True)

        slot_refresh = refresh()

        elapsed = time.time() - start_time
        gate_rejections = [e for e in gate_evaluations if not e["gate_passed"]]
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
            "gate_evaluations": gate_evaluations,
            "gate_rejections": gate_rejections,
            "gate_rejection_breakdown": _summarize_gate_rejections(gate_rejections),
        }

        # ── Compute direction flips vs previous cycle ──
        # A "flip" is any change in consensus direction (long/short/neutral)
        # between consecutive cycles for the same ticker.  Flips persist in
        # cycle_history.json so they're visible in both live and history views.
        prev_cycle = _cycle_history[0] if _cycle_history else None
        flips: dict[str, dict] = {}
        if prev_cycle:
            prev_map: dict[str, str] = {
                e["ticker"]: e["direction"]
                for e in prev_cycle.get("gate_evaluations", [])
            }
            for e in gate_evaluations:
                prev_dir = prev_map.get(e["ticker"])
                if prev_dir and prev_dir != e["direction"]:
                    flips[e["ticker"]] = {
                        "from": prev_dir,
                        "to": e["direction"],
                        "instrument_type": e["instrument_type"],
                        "consensus_confidence": e["consensus_confidence"],
                        "gate_passed": e["gate_passed"],
                    }
        result["flip_count"] = len(flips)
        result["flips"] = flips

        _last_cycle_result = result
        _cycle_history.insert(0, result)
        _save_history()

        logger.info(
            f"Cycle #{cycle_id} done: {len(signals)} signals in {elapsed:.1f}s"
        )
        return result

    except Exception as e:
        logger.error(f"Cycle #{cycle_id} failed: {e}", exc_info=True)
        result = {
            "cycle_id": cycle_id,
            "status": "error",
            "reason": str(e),
            "timestamp": now_iso(),
            "signals": [],
            "gate_evaluations": [],
            "gate_rejections": [],
            "gate_rejection_breakdown": {},
        }
        _last_cycle_result = result
        _cycle_history.insert(0, result)
        _save_history()
        return result

    finally:
        with _cycle_lock:
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
        streamer = IBKRStreamer(
            host=IBKR_HOST, port=IBKR_PORT, client_id=IBKR_CLIENT_ID
        )
        from engine.ibkr_data_feed import set_streamer
        set_streamer(streamer)
        streamer.start()
        logger.info("IBKR streamer started")
    from engine.ibkr_connector import start_monitoring
    start_monitoring(interval=10)
