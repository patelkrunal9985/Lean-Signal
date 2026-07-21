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

    return result
