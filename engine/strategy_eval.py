"""
Strategy Evaluation Phase — Runs V2/V3 strategies per ticker, consensus, gate.

Extracted from engine/runner.py during the 3-way refactor.
Stateless: takes ticker_data_map, returns signals and gate evaluations.
"""
import time
import traceback
from typing import Any

from utils.logger import get_logger

logger = get_logger("engine.strategy_eval")

from engine.v2.registry import V2StrategyRegistry
from engine.v3.registry import get_strategies
from engine.v3.gate import SignalQualityGate
from engine.consensus_coordinator import compute_consensus
from regime.detector import RegimeDetector
from engine.subscription_manager import set_priority
from engine.signal_persistence import update_strategy_performance, evaluate_pending_predictions
from engine.account_monitor import check_signal_blockers
from engine.entry_exit import compute_entry_exit_levels
from engine.strike_selector import recommend_strike
from utils.settings_manager import get as get_setting, ODTE_STRATEGY_WHITELIST


def evaluate_tickers(
    ticker_data_map: dict[str, dict],
    all_tickers: list[tuple[str, str]],
    account_data: dict,
    positions_data: dict,
    cycle_id: int,
    start_time: float,
    cycle_max_seconds: int = 180,
) -> dict[str, Any]:
    """Run V2 + V3 + consensus + gate on every ticker.

    Returns a dict with:
      - signals: list of signal dicts (sorted by composite_score desc)
      - gate_evaluations: list of per-ticker gate decision dicts
    """
    regime_detector = RegimeDetector()
    v2_registry = V2StrategyRegistry()
    gate = SignalQualityGate()

    max_seconds = cycle_max_seconds

    signals: list[dict] = []
    gate_evaluations: list[dict] = []

    # ── Evaluate pending predictions from previous cycles ──
    # This checks whether price moved as predicted, updating EWMA authority.
    # Must happen BEFORE new strategies run to avoid feedback loop.
    try:
        evaluate_pending_predictions(ticker_data_map, cycle_id)
    except Exception:
        pass

    for ticker, data in ticker_data_map.items():
        # ── Watchdog: abort cycle if it's running too long ──
        if time.time() - start_time > max_seconds:
            logger.error(
                "Cycle #%d: ABORTING during ticker loop — exceeded %ds max",
                cycle_id, max_seconds,
            )
            raise TimeoutError(f"Cycle exceeded {max_seconds}s max duration")

        instr_type = data["instrument_type"]
        context = data.copy()

        # ── Run V2 strategies (skip for options) ──
        v2_results: list[dict] = []
        if instr_type != "option":
            v2_results = v2_registry.run_all(context)

        # ── Run V3 strategies ──
        v3_strategies: list = []
        if instr_type in ("stock", "future", "option"):
            v3_strategies = get_strategies(instr_type)
            # ── [0DTE Mode] Filter to ODTE-relevant strategies only ──
            if instr_type == "option" and get_setting("odte_mode", False):
                original_count = len(v3_strategies)
                v3_strategies = [
                    s for s in v3_strategies
                    if s.name in ODTE_STRATEGY_WHITELIST
                ]
                filtered_count = original_count - len(v3_strategies)
                if filtered_count > 0:
                    logger.info(
                        "[0DTE Mode] %s: filtered %d/%d strategies (keeping %d ODTE-relevant)",
                        ticker, filtered_count, original_count, len(v3_strategies),
                    )
        logger.debug(
            "Cycle #%d: processing %s (%s) with %d V3 strategies",
            cycle_id, ticker, instr_type, len(v3_strategies),
        )

        v3_results_raw: list[dict] = []
        for strategy in v3_strategies:
            try:
                result = strategy.compute(context)
                if isinstance(result, dict):
                    direction = result.get("direction", "neutral")
                    confidence = result.get("confidence", 0)
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
                            "default_weight": float(getattr(strategy, 'default_weight', 0.05) or 0.05),
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

        # ── Previous cycle price for velocity-based counter-trend override ──
        _prev_price = 0.0
        try:
            from engine.signal_persistence import get_ticker_state
            _st = get_ticker_state(ticker)
            _prev_snap = _st.get("current_signal") or {}
            _prev_price = float(_prev_snap.get("price", 0) or 0)
        except Exception:
            pass

        direction, conf, consensus_meta = compute_consensus(
            v2_results, v3_results_raw,
            regime.get("primary_regime", "ranging"),
            ticker, instr_type, data.get("current_price", 0), atr, sma_50,
            dte=dte_val,
            volume_profile=data.get("volume_profile_intraday", {}),
            prev_price=_prev_price,
        )

        all_strategy_votes = v2_results + v3_results_raw
        set_priority(ticker, instr_type, int(conf * 100))

        # ── Record strategy predictions for outcome-based EWMA evaluation ──
        # Predictions are evaluated in the NEXT cycle's evaluate_pending_predictions()
        # against actual price movement (not consensus direction — no circularity).
        if direction != "neutral":
            for sv in all_strategy_votes:
                try:
                    if sv.get("source") != "v3":
                        continue
                    sv_dir = sv.get("direction", "neutral")
                    sv_name = sv.get("name", sv.get("strategy", ""))
                    if sv_dir != "neutral" and sv_name:
                        update_strategy_performance(
                            ticker, sv_name, sv_dir,
                            data.get("current_price", 0), cycle_id,
                        )
                except Exception:
                    pass

        # ── 3-layer Quality Gate ──
        gate_result = gate.evaluate(
            ticker_data=data,
            signal_direction=direction,
            signal_confidence=conf,
            active_strategies=all_strategy_votes,
            regime=regime,
            consensus_meta=consensus_meta,
        )

        # ── [0DTE Mode] GOLD+ conviction gate ──
        # Only allow GOLD or PLATINUM tier signals to pass for 0DTE options.
        # This ensures maximum confluence before taking a multi-hour position.
        if (
            gate_result.get("passed", False)
            and instr_type == "option"
            and get_setting("odte_mode", False)
        ):
            tier = consensus_meta.get("consensus_conviction_tier", "bronze")
            if tier not in ("gold", "platinum"):
                gate_result["passed"] = False
                gate_result["reason"] = f"odte_mode_gold_required (tier={tier})"
                logger.info(
                    "[0DTE Mode] %s %s: blocked by GOLD+ gate (tier=%s)",
                    ticker, direction, tier,
                )

        # ── Account-based circuit breaker ──
        if gate_result.get("passed", False) and account_data is not None:
            blocked, block_reason = check_signal_blockers(
                ticker, direction, instr_type, account_data, positions_data,
            )
            if blocked:
                gate_result["passed"] = False
                gate_result["reason"] = f"account_blocked_{block_reason}"

        # ── Capture per-ticker gate decision ──
        gate_eval_entry = {
            "ticker": ticker,
            "instrument_type": instr_type,
            "direction": direction,
            "current_price": data.get("current_price", 0),
            "ohlcv_len": len(data.get("ohlcv", [])),
            "atr": atr,
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
                "consensus_net_score": consensus_meta.get("consensus_net_score", 0),
                "consensus_active_votes": consensus_meta.get("consensus_active_votes", 0),
                "consensus_neutral_votes": consensus_meta.get("consensus_neutral_votes", 0),
                "consensus_weighted_long": consensus_meta.get("consensus_weighted_long", 0),
                "consensus_weighted_short": consensus_meta.get("consensus_weighted_short", 0),
                "consensus_total_weight": consensus_meta.get("consensus_total_weight", 0),
                "consensus_counter_trend": consensus_meta.get("consensus_counter_trend", "no"),
                "consensus_regime_boost": consensus_meta.get("consensus_regime_boost", 0),
                "consensus_tod_window": consensus_meta.get("consensus_tod_window", "—"),
                "consensus_action": consensus_meta.get("consensus_action", "—"),
                "consensus_families": consensus_meta.get("consensus_families", {}),
            },
        }
        gate_evaluations.append(gate_eval_entry)

        # ── Entry/Exit levels — compute for ALL tickers with direction, not just gate-passed ──
        # Gate-rejected tickers with active thesis still need levels for dashboard display
        levels: dict = {}
        if direction != "neutral":
            try:
                levels = compute_entry_exit_levels(
                    ticker, data, direction, conf, instr_type
                )
            except Exception:
                logger.debug("Entry/exit levels failed for %s: %s", ticker, traceback.format_exc())

        # ── Late-entry protection: if price has already moved most of the way to SL/TP, penalize ──
        # This prevents entering when the trade is half-completed.
        # Check: if distance-to-TP < 50% of distance-to-SL, the good move has already happened.
        if levels and direction != "neutral":
            ep = levels.get("entry_price", 0)
            sl = levels.get("stop_loss", 0)
            tp = levels.get("take_profit", 0)
            if ep > 0 and sl > 0 and tp > 0 and abs(ep - sl) > 0.01:
                dist_to_sl = abs(ep - sl)
                dist_to_tp = abs(tp - ep)
                # Check if most of the move has already happened (price near TP already)
                current = data.get("current_price", 0)
                if current > 0:
                    remaining_to_tp = abs(tp - current)
                    pct_complete = 1.0 - (remaining_to_tp / max(dist_to_tp, 0.001))
                    if pct_complete > 0.50:
                        # Signal is >50% completed — penalize confidence heavily
                        logger.info(
                            "%s: late entry — %.0f%% of move already complete (SL=%.2f, entry=%.2f, current=%.2f, TP=%.2f). Penalizing.",
                            ticker, pct_complete * 100, sl, ep, current, tp,
                        )
                        conf *= (1.0 - pct_complete * 0.8)  # Aggressive decay
                        # Add warning to gate result
                        if levels.get("proximity_warning"):
                            levels["proximity_warning"] = f"late_entry_{pct_complete:.0%}complete_" + levels["proximity_warning"]
                        else:
                            levels["proximity_warning"] = f"late_entry_{pct_complete:.0%}complete"

        # ── Strike selection (options only) ──
        strike_rec: dict = {}
        if instr_type == "option" and direction != "neutral" and gate_result.get("passed", False):
            try:
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

        # ── Position: always 1 contract ──
        position_contracts = 1 if (instr_type == "option" and direction != "neutral") else 0

        # ── Market dashboard for option signals ──
        market_dashboard: dict = {}
        if instr_type == "option":
            breadth = data.get("market_breadth", {})
            market_dashboard = {
                "underlying": data.get("underlying", ""),
                "underlying_price": data.get("underlying_price", 0),
                "iv": round(data.get("iv", 0), 1) if data.get("iv", 0) else 0,
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
            "nearest_support": levels.get("nearest_support"),
            "nearest_resistance": levels.get("nearest_resistance"),
            "suggested_entry": levels.get("suggested_entry"),
            "suggested_entry_type": levels.get("suggested_entry_type", "market"),
            "proximity_warning": levels.get("proximity_warning"),
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
            "tpo_profile": data.get("tpo_profile", {}),
        }

        if direction != "neutral":
            signals.append(signal)
            set_priority(ticker, instr_type, 5000)

    signals.sort(key=lambda s: s["composite_score"], reverse=True)

    return {
        "signals": signals,
        "gate_evaluations": gate_evaluations,
    }
