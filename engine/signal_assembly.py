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
        return "REDUCE" if health_label in ("robust", "caution") else "EXIT"

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

    # ── Map level to verdict ──
    if level >= 3:
        verdict = f"STRONG {direction.upper()}"
    elif level == 2:
        verdict = direction.upper()
    elif level == 1:
        verdict = "HOLD"
    elif level == 0:
        verdict = "REDUCE"
    elif level == -1:
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
            "direction_skew": positions_data.get("direction_skew", 0),
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
    except Exception:
        pass

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

    return result
