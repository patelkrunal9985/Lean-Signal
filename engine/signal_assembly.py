"""
Signal Assembly Phase — Builds the cycle result dict and handles persistence.

Extracted from engine/runner.py during the 3-way refactor.
"""
import re
import time
from typing import Any

from utils.logger import get_logger
from utils.time_utils import now_iso

logger = get_logger("engine.signal_assembly")

# Strip "_<digit>..." suffixes from gate reasons so variable numerics (e.g.
# "within_15min_of_close", "atr_too_high_0.0821", "stale_price_5s") collapse
# into a single bucket per failure mode without colliding on the first word.
_GATE_REASON_BUCKET_RE = re.compile(r"_\d.*$")


def summarize_gate_rejections(rejections: list[dict]) -> dict[str, int]:
    """Group gate rejections by reason prefix for an at-a-glance summary.

    Bucket key construction:
    Strip any "_<digit>..." suffix so variable numerics don't split buckets.
    Reasons without any numeric suffix keep their full text.

    Examples:
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


# ── Verdict scoring weights ──
_VERDICT_STATE_BASE = {"confirmed": 2, "active": 1, "pending": 0}
_VERDICT_TIER_MOD = {"platinum": 1, "gold": 0, "silver": -1, "bronze": -2}
_VERDICT_HEALTH_MOD = {"robust": 0, "caution": -1, "fragile": -2, "terminal": -4}

from utils.settings_manager import get as _get_setting

def _get_verdict_threshold(level_name: str, default: int) -> int:
    """Get a verdict level threshold from settings, with fallback to default."""
    key = f"verdict_{level_name}_threshold"
    try:
        return int(_get_setting(key, default))
    except (ValueError, TypeError):
        return default

# ── Regime-direction alignment sets (module-level to avoid recreation per call) ──
_REGIME_BULLISH = {"strong_uptrend", "uptrend"}
_REGIME_BEARISH = {"strong_downtrend", "downtrend"}


def compute_verdict(signal: dict, health: dict | None, signal_states: dict | None) -> str:
    """Synthesize all quality indicators into a single actionable verdict.

    Uses a Base+Modifier scoring system with hard safety caps:
      3+ → STRONG BUY/SELL     1 → HOLD           -1 → EXIT
      2  → BUY/SELL            0 → REDUCE         -2 → AVOID

    Safety caps:
      - Fragile health caps max verdict at REDUCE
      - Gate-failed caps max verdict at HOLD
      - Terminal health → immediate EXIT/AVOID
    """
    state = "none"
    direction = signal.get("direction", "neutral")

    # Get signal state from persistence engine
    if signal_states and signal_states.get("by_ticker"):
        ticker_state = signal_states["by_ticker"].get(signal.get("ticker", ""), {})
        state = ticker_state.get("state", "none")

    # ── Sticky signal: state is ACTIVE/CONFIRMED but current cycle is neutral → HOLD ──
    # The signal is still valid per the persistence engine; we just aren't adding to it.
    if direction == "neutral" and state in ("active", "confirmed"):
        return "HOLD"

    if state in ("none", "watching") or direction == "neutral":
        return "NO ACTION"

    # ── Signal age decay (penalize old signals) ──
    age_decay = 1.0
    ticker_key = signal.get("ticker", "")
    if signal_states and signal_states.get("by_ticker"):
        ts = signal_states["by_ticker"].get(ticker_key, {})
        age_decay = ts.get("age_decay", 1.0)

    health_label = health.get("label", "caution") if health else "caution"
    health_score = health.get("health", 50) if health else 50
    tier = (signal.get("consensus_meta", {}).get("consensus_conviction_tier") or "bronze")
    regime = signal.get("regime", "ranging")
    gate_passed = signal.get("gate_passed", False)
    counter_trend = (signal.get("consensus_meta", {}).get("consensus_counter_trend") or "no") == "yes"

    # ── Non-actionable pre-filters ──
    if health_label == "terminal":
        return "EXIT" if state in ("active", "confirmed", "weakening") else "AVOID"

    if state == "weakening":
        if health_label in ("fragile", "terminal"):
            return "EXIT"
        if not gate_passed:
            return "EXIT"
        weakening_level = 1  # Base: REDUCE territory
        if counter_trend:
            weakening_level -= 1
        if age_decay < 0.7:
            weakening_level -= 1
        if weakening_level <= 0:
            return "EXIT"
        return "REDUCE"

    if state == "pending":
        if not gate_passed or health_label == "fragile":
            return "AVOID"
        return "WAIT"

    # ── Base level for ACTIVE/CONFIRMED directional signals ──
    level = _VERDICT_STATE_BASE.get(state, 1)

    # ── Tier modifier ──
    level += _VERDICT_TIER_MOD.get(tier, -2)

    # ── Health modifier ──
    level += _VERDICT_HEALTH_MOD.get(health_label, -2)

    # ── Regime alignment ──
    is_aligned = (
        (direction == "long" and regime in _REGIME_BULLISH) or
        (direction == "short" and regime in _REGIME_BEARISH)
    )
    if is_aligned:
        level += 1

    # ── Risk penalties ──
    if counter_trend:
        level -= 1
    if age_decay < 0.7:
        level -= 1
    if not gate_passed:
        level -= 2

    # ── Hard safety caps ──
    if health_label == "fragile" and level > 0:
        level = 0  # Max: REDUCE
    if not gate_passed and level > 1:
        level = 1  # Max: HOLD

    # ── Map level to verdict (thresholds from settings) ──
    strong_th = _get_verdict_threshold("strong", 3)
    buy_th = _get_verdict_threshold("buy", 2)
    hold_th = _get_verdict_threshold("hold", 1)
    reduce_th = _get_verdict_threshold("reduce", 0)
    exit_th = _get_verdict_threshold("exit", -1)

    if level >= strong_th:
        verdict = f"STRONG {direction.upper()}"
    elif level >= buy_th:
        verdict = direction.upper()
    elif level >= hold_th:
        verdict = "HOLD"
    elif level >= reduce_th:
        verdict = "REDUCE"
    elif level >= exit_th:
        verdict = "EXIT"
    else:
        verdict = "AVOID"

    return verdict


def assemble_cycle_result(
    signals: list[dict],
    gate_evaluations: list[dict],
    all_tickers: list[tuple[str, str]],
    cycle_id: int,
    start_time: float,
    daytype_prediction: dict,
    account_data: dict,
    positions_data: dict,
    slot_refresh: dict | None = None,
) -> dict[str, Any]:
    """Build the final cycle result dict and handle signal persistence.

    Returns the complete result dict ready for _cycle_history.
    """
    elapsed = time.time() - start_time
    gate_rejections = [e for e in gate_evaluations if not e["gate_passed"]]
    logger.info("Cycle #%d: building result dict (elapsed=%.1fs)", cycle_id, elapsed)

    # ── Enrich signals with cycle-level context BEFORE result dict ──
    for s in signals:
        s["daytype_prediction"] = daytype_prediction
        s["account"] = {
            "daily_pnl": account_data.get("daily_pnl", 0),
            "buying_power": account_data.get("buying_power", 0),
            "daily_loss_limit_hit": account_data.get("daily_loss_limit_hit", False),
            "position_count": positions_data.get("position_count", 0),
            "direction_skew": positions_data.get("direction_skew", 0) if positions_data else 0,
        } if account_data else {}

    result: dict[str, Any] = {
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
        "gate_rejection_breakdown": summarize_gate_rejections(gate_rejections),
        "daytype_prediction": daytype_prediction,
        "account_summary": {
            "daily_pnl": account_data.get("daily_pnl", 0) if account_data else 0,
            "buying_power": account_data.get("buying_power", 0) if account_data else 0,
            "position_count": positions_data.get("position_count", 0) if positions_data else 0,
        },
    }

    # ── Signal persistence ──
    from engine.signal_persistence import (
        update as update_signal_state,
        get_significant_flips,
        get_all_states,
        get_all_health_scores,
    )
    logger.info("Cycle #%d: signal persistence (%d evaluations)", cycle_id, len(gate_evaluations))
    for e in gate_evaluations:
        try:
            cm = e.get("consensus_meta", {})
            ns = float(cm.get("consensus_net_score", 0) or 0)
            strats = e.get("strategy_votes", [])
            logger.debug(
                "Cycle #%d: persisting state for %s (dir=%s, ns=%.3f)",
                cycle_id, e.get("ticker", "?"), e.get("direction", "?"), ns,
            )
            update_signal_state(
                ticker=e["ticker"],
                direction=e["direction"],
                confidence=e.get("consensus_confidence", 0),
                net_score=ns,
                consensus_meta=cm,
                strategy_votes=strats,
                cycle_id=cycle_id,
                current_price=e.get("current_price", 0),
                instrument_type=e.get("instrument_type", ""),
                regime=e.get("regime", "unknown"),
                atr=e.get("atr", 0),
            )
        except Exception as exc:
            logger.warning(
                "Cycle #%d: signal persistence failed for %s: %s",
                cycle_id, e.get("ticker", "?"), exc,
            )

    # ── Flips ──
    flip_data = get_significant_flips(min_score=0.0)
    real_flips = flip_data.get("real", [])
    potential_flips = flip_data.get("potential", [])
    all_states = get_all_states()

    result["flip_count"] = len(real_flips)
    result["flip_count_potential"] = len(potential_flips)
    result["flips"] = {}
    for f in real_flips:
        t = f["ticker"]
        result["flips"][t] = {
            "from": f["from"],
            "to": f["to"],
            "score": f["score"],
            "strength": f.get("streak", 1) * f["score"],
            "is_real": True,
        }
    result["flips_potential"] = {}
    for f in potential_flips:
        t = f["ticker"]
        result["flips_potential"][t] = {
            "from": f["from"],
            "to": f["to"],
            "score": f["score"],
            "needs_cycles": max(0, 3 - f.get("streak", 1)),
        }
    result["signal_states"] = all_states

    # ── Persist signal state to disk ──
    try:
        from engine.signal_persistence import _autosave
        _autosave()
    except Exception as exc:
        logger.warning("Cycle #%d: autosave failed: %s", cycle_id, exc)

    # ── Take-profit events ──
    try:
        from engine.signal_persistence import get_take_profit_events
        tp_events = get_take_profit_events()
        result["take_profit_events"] = tp_events[:10]
    except Exception:
        result["take_profit_events"] = []

    # ── Signal health scores ──
    try:
        health_scores = get_all_health_scores()
        result["health_scores"] = health_scores
    except Exception:
        result["health_scores"] = {}

    # ── Final verdicts (synthesize all indicators into one decision) ──
    try:
        for s in signals:
            ticker = s.get("ticker", "")
            health = health_scores.get(ticker) if isinstance(health_scores, dict) else None
            s["verdict"] = compute_verdict(s, health, all_states)
    except Exception as exc:
        logger.warning("Verdict computation failed: %s", exc)
        for s in signals:
            if "verdict" not in s:
                s["verdict"] = "NO ACTION"

    # ═══════════════════════════════════════════════════════════════════
    # Persistent Signal Slots (Architecture: keep ticker visible, update data)
    # ═══════════════════════════════════════════════════════════════════
    # Once a ticker reaches ACTIVE/CONFIRMED state, it gets a permanent
    # slot in the dashboard. Every cycle we update the slot with current
    # data from the gate evaluation: gate result, price, regime, direction.
    # The slot's DIRECTION comes from the state machine (persisted),
    # not from the current cycle (which may be neutral). The gate rejection
    # is shown as a BADGE with the real reason — the signal is never hidden.
    #
    # This replaces the old "sticky carryover" approach which created
    # synthetic entries with incomplete metadata.
    # ═══════════════════════════════════════════════════════════════════

    # Build a lookup of current-cycle gate evaluations by ticker
    gate_by_ticker: dict[str, dict] = {e["ticker"]: e for e in gate_evaluations}
    existing_tickers: set = {s["ticker"] for s in signals}

    # Track persistent slot count to enforce hard cap
    _PERSISTENT_SLOT_MAX = 30
    persistent_slot_count = 0

    if all_states and all_states.get("by_ticker"):
        for ticker, ts in all_states["by_ticker"].items():
            state = ts.get("state", "none")
            if state not in ("active", "confirmed", "weakening"):
                continue
            if ticker in existing_tickers:
                continue

            # ── Freshness check: skip if not scanned in 5+ cycles ──
            last_snap = ts.get("current_signal") or {}
            last_cycle = last_snap.get("cycle_id", 0) if isinstance(last_snap, dict) else 0
            if last_cycle > 0 and cycle_id - last_cycle > 5:
                continue
            if not last_snap:  # v1 disk format, no memory snapshot
                continue

            # ── Hard cap: max 30 persistent slots ──
            if persistent_slot_count >= _PERSISTENT_SLOT_MAX:
                continue
            persistent_slot_count += 1

            gate_eval = gate_by_ticker.get(ticker, {})

            # ── Instrument type (with fallback for old disk state) ──
            instr_type = (
                ts.get("instrument_type", "")
                or last_snap.get("instrument_type", "")
                or "stock"
            )

            # ── Direction: USE THE PERSISTED direction from state machine ──
            # NOT the current cycle's direction (which might be "neutral" even
            # though the signal is still ACTIVE/CONFIRMED).
            direction = ts.get("active_direction", "neutral")
            if direction == "neutral":
                direction = last_snap.get("direction", "neutral")

            # ── Conviction tier based on actual state ──
            conv_tier = "gold" if state == "confirmed" else "silver" if state == "active" else "bronze"

            # ── Current cycle data (from gate evaluation) ──
            # These are the REAL data from this cycle, not generic defaults.
            gate_passed = gate_eval.get("gate_passed", False)
            gate_reason = gate_eval.get("gate_reason", "not_scanned")
            current_price = gate_eval.get("current_price", 0)
            regime = gate_eval.get("regime", "unknown")
            current_direction = gate_eval.get("direction", "neutral")

            # ── Build the persistent slot signal ──
            slot_signal: dict[str, Any] = {
                # Identity (from persistence)
                "ticker": ticker,
                "instrument_type": instr_type,
                # Direction from state machine, not current cycle
                "direction": direction,
                "current_direction": current_direction,  # current cycle's direction (for display)
                "confidence": round(ts.get("decayed_confidence", 0), 4),
                "composite_score": round(abs(last_snap.get("net_score", 0)), 4),
                "strategy_count": last_snap.get("active_votes", 0),
                "agreeing_count": last_snap.get("active_votes", 0),
                # Current cycle data
                "regime": regime,
                "current_price": current_price,
                "gate_passed": gate_passed,
                "gate_reason": gate_reason,
                "entry_price": ts.get("state_entry_price", 0),
                # State machine info
                "state": state,
                "persistent_slot": True,  # Flag for frontend
                "consensus_meta": {
                    "consensus_conviction_tier": conv_tier,
                    "consensus_counter_trend": (
                        gate_eval.get("consensus_meta", {}).get("consensus_counter_trend")
                        or last_snap.get("counter_trend", "no")
                    ),
                },
                "strategies": [],
                "market_dashboard": {},
            }

            # ── Compute verdict ──
            # With gate_passed reflecting the real current-cycle result.
            # If the gate rejected this cycle, the verdict gets penalized
            # appropriately (e.g. HOLD instead of BUY).
            try:
                ticker_health = health_scores.get(ticker) if isinstance(health_scores, dict) else None
                slot_signal["verdict"] = compute_verdict(slot_signal, ticker_health, all_states)
            except Exception:
                slot_signal["verdict"] = "HOLD"

            signals.append(slot_signal)
            logger.info(
                "Cycle #%d: persistent slot for %s (state=%s, dir=%s, gate=%s, reason=%s, instr=%s)",
                cycle_id, ticker, state, direction,
                "PASSED" if gate_passed else "REJECTED", gate_reason, instr_type,
            )

        # ── Re-sort and regroup now that persistent slot signals are added ──
        signals.sort(key=lambda s: s.get("composite_score", 0), reverse=True)
        result["signals_count"] = len(signals)
        result["signals"] = {
            "stock": [s for s in signals if s.get("instrument_type", "") == "stock"],
            "future": [s for s in signals if s.get("instrument_type", "") == "future"],
            "option": [s for s in signals if s.get("instrument_type", "") == "option"],
        }

    return result
