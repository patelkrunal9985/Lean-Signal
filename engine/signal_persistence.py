"""
Signal Persistence Engine — State Machine + Hysteresis + Flip Scoring.

Every ticker has a persistent signal state that builds conviction over time
instead of flipping on every single cycle change.

States: NONE → WATCHING → PENDING → ACTIVE → CONFIRMED

Key design decisions:
- A direction change requires 2+ consecutive cycles before triggering a flip
- Neutral is treated as a cooldown state, not a direction
- Flip significance is scored (0-1) based on magnitude, agreement, regime alignment
- Only flips with score >= 0.6 are surfaced as "real flips" to the dashboard
- Lower-scoring flips are shown as "potential flips" in a separate list
"""
from __future__ import annotations
import time
import threading
from typing import Any

_lock = threading.RLock()  # RLock: get_all_states() calls get_ticker_state() which needs re-entrant lock

# ── All possible signal states in conviction order ──
SIGNAL_STATES = {
    "none": 0,
    "watching": 1,
    "pending": 2,
    "active": 3,
    "confirmed": 4,
}

# ── Module-level persistence state ──
# Thread-safe: all mutating operations acquire _lock
_signal_memory: dict[str, list[dict]] = {}       # ticker → list of recent cycle snapshots
_signal_state: dict[str, str] = {}                # ticker → current state
_state_since: dict[str, float] = {}               # ticker → time when current state started
_flip_history: dict[str, list[dict]] = {}         # ticker → list of significant flip events
_consecutive_same: dict[str, int] = {}            # ticker → consecutive cycles in same direction
_consecutive_neutral: dict[str, int] = {}         # ticker → consecutive neutral cycles
_active_direction: dict[str, str] = {}            # ticker → the direction we're committed to
_last_flip_cycle: dict[str, int] = {}             # ticker → cycle_id of last significant flip

# ── Dynamic strategy authority tracking ──
# Tracks each strategy's recent prediction accuracy vs final consensus direction.
# Used to adjust authority multipliers: strategies that predict well gain authority,
# strategies that predict poorly lose authority.
_strategy_performance: dict[str, list[bool]] = {}  # strategy_name → [correct, ...]
_STRATEGY_PERF_MAX = 20  # Rolling window: keep last 20 predictions per strategy

# ── Configuration ──
MAX_MEMORY = 10                    # Keep last 10 cycle snapshots per ticker
MIN_PENDING_CYCLES = 2             # Need 2 consecutive same-direction to upgrade watching→pending
MIN_ACTIVE_CYCLES = 3              # Need 3 consecutive same-direction for pending→active
MIN_CONFIRMED_CYCLES = 5           # Need 5 consecutive same-direction for active→confirmed
FLIP_SCORE_THRESHOLD = 0.60        # Only surface flips with score >= this
MAX_FLIP_HISTORY = 20              # Max flip events to keep per ticker
NEUTRAL_COOLDOWN_MAX = 3           # After this many consecutive neutrals, reset to NONE


def _get_prev_direction(ticker: str) -> str | None:
    """Get the direction from the most recent cycle snapshot for this ticker."""
    mem = _signal_memory.get(ticker, [])
    if mem:
        return mem[-1].get("direction", "neutral")
    return None


def _get_prev_confidence(ticker: str) -> float:
    mem = _signal_memory.get(ticker, [])
    if mem:
        return mem[-1].get("confidence", 0.0)
    return 0.0


def _get_prev_net(ticker: str) -> float:
    mem = _signal_memory.get(ticker, [])
    if mem:
        return mem[-1].get("net_score", 0.0)
    return 0.0


def _get_trend_direction(memory: list[dict], window: int = 3) -> str:
    """Simple trend: is net_score consistently trending up or down over last N cycles?"""
    if len(memory) < window:
        return "flat"
    recent = memory[-window:]
    scores = [m.get("net_score", 0) for m in recent]
    first_half = sum(scores[:len(scores)//2]) / max(len(scores)//2, 1)
    second_half = sum(scores[len(scores)//2:]) / max(len(scores) - len(scores)//2, 1)
    diff = second_half - first_half
    if diff > 0.15:
        return "improving"
    elif diff < -0.15:
        return "deteriorating"
    return "flat"


def _score_flip_significance(
    ticker: str,
    direction: str,
    confidence: float,
    net_score: float,
    prev_direction: str,
    prev_confidence: float,
    prev_net: float,
    consensus_meta: dict,
) -> float:
    """Score how significant a flip is, from 0 (noise) to 1 (major reversal).

    Factors:
    1. Net score magnitude change (0-0.35): Bigger change = more significant
    2. Confidence in new direction (0-0.25): Higher confidence = more real
    3. Strategy agreement flip (0-0.20): More strategies flipping = more real
    4. Regime alignment (0-0.10): Regime confirming new direction = bonus
    5. Family diversity (0-0.10): Multiple families agreeing = stronger
    """
    score = 0.0

    # ── Factor 1: Net score magnitude change (weight: 0.35) ──
    net_change = abs(net_score - prev_net)
    score += min(net_change * 0.7, 0.35)

    # ── Factor 2: Confidence in new direction (weight: 0.25) ──
    score += min(confidence * 0.4, 0.25)

    # ── Factor 3: Consecutive cycles supporting new direction (weight: 0.20) ──
    streak = _consecutive_same.get(ticker, 0)
    score += min(streak * 0.07, 0.20)

    # ── Factor 4: Regime alignment (weight: 0.10) ──
    counter_trend = consensus_meta.get("consensus_counter_trend", "no")
    if counter_trend == "no":
        score += 0.10
    else:
        score += 0.02  # Counter-trend flips are risky

    # ── Factor 5: Active vote diversity (weight: 0.10) ──
    family_count = consensus_meta.get("consensus_family_count", 0)
    score += min(family_count * 0.03, 0.10)

    # ── Clamp ──
    return min(score, 1.0)


def _compute_state(direction: str, ticker: str) -> tuple[str, bool]:
    """Determine the next state for a ticker given its direction and history.

    Returns (new_state, significant_transition).
    """
    prev_state = _signal_state.get(ticker, "none")
    prev_dir = _active_direction.get(ticker)
    streak = _consecutive_same.get(ticker, 0)
    neutral_streak = _consecutive_neutral.get(ticker, 0)

    # ── Neutral cooldown logic ──
    if direction == "neutral":
        if neutral_streak >= NEUTRAL_COOLDOWN_MAX:
            return "none", False
        if prev_state != "none":
            return "watching", False
        return "none", False

    # ── New direction, different from active direction ──
    if prev_dir and direction != prev_dir:
        # Direction changed! Start watching, don't flip yet
        return "watching", False

    # ── Same direction, escalate based on streak ──
    if streak >= MIN_CONFIRMED_CYCLES:
        return "confirmed", prev_state in ("active", "pending")
    if streak >= MIN_ACTIVE_CYCLES:
        return "active", prev_state == "pending"
    if streak >= MIN_PENDING_CYCLES:
        return "pending", True  # Transition from watching→pending is noteworthy
    return "watching", False


def update(
    ticker: str,
    direction: str,
    confidence: float,
    net_score: float,
    consensus_meta: dict,
    strategy_votes: list | None = None,
    cycle_id: int = 0,
) -> dict | None:
    """Update signal state for a ticker with the latest cycle data.

    Args:
        ticker: Ticker symbol
        direction: 'long', 'short', or 'neutral'
        confidence: Consensus confidence (0-1)
        net_score: Net score (-1 to +1)
        consensus_meta: Full consensus metadata dict
        strategy_votes: List of strategy vote dicts
        cycle_id: Current cycle number

    Returns:
        Dict describing a significant flip event, or None if nothing noteworthy.
    """
    global _signal_memory, _signal_state, _state_since, _consecutive_same
    global _consecutive_neutral, _active_direction, _flip_history, _last_flip_cycle

    with _lock:
        # Read previous direction from active_direction. If current state is 'none'
        # (e.g. after neutral cooldown), treat prev_direction as None to prevent
        # stale direction from triggering false direction-change detection.
        current_state = _signal_state.get(ticker, "none")
        if current_state == "none" or _active_direction.get(ticker) is None:
            prev_direction = None
        else:
            prev_direction = _active_direction.get(ticker, "neutral")
        prev_confidence = _get_prev_confidence(ticker)
        prev_net = _get_prev_net(ticker)

        # ── Update memory ──
        snapshot = {
            "direction": direction,
            "confidence": confidence,
            "net_score": net_score,
            "cycle_id": cycle_id,
            "timestamp": time.time(),
            "state": None,  # filled below
            "families": consensus_meta.get("consensus_families", {}),
            "family_count": consensus_meta.get("consensus_family_count", 0),
        }
        _signal_memory.setdefault(ticker, [])
        _signal_memory[ticker].append(snapshot)
        if len(_signal_memory[ticker]) > MAX_MEMORY:
            _signal_memory[ticker].pop(0)

        # ── Update consecutive counters ──
        if direction == "neutral":
            _consecutive_neutral[ticker] = _consecutive_neutral.get(ticker, 0) + 1
            _consecutive_same[ticker] = 0
        elif direction == prev_direction:
            _consecutive_same[ticker] = _consecutive_same.get(ticker, 0) + 1
            _consecutive_neutral[ticker] = 0
        else:
            _consecutive_same[ticker] = 1  # Starting new streak
            _consecutive_neutral[ticker] = 0

        # ── Compute new state ──
        new_state, significant = _compute_state(direction, ticker)
        snapshot["state"] = new_state

        if new_state != _signal_state.get(ticker):
            # State changed
            _signal_state[ticker] = new_state
            _state_since[ticker] = time.time()
            if new_state in ("pending", "active", "confirmed"):
                _active_direction[ticker] = direction
            elif new_state == "none":
                # Clear active direction when state resets to none (after neutral cooldown)
                _active_direction.pop(ticker, None)
        else:
            # Same state, just update direction tracking
            if direction != "neutral":
                _active_direction[ticker] = direction

        # ── Detect significant flips ──
        flip_event = None

        # A "potential flip" is when direction changes from a non-neutral
        # active state to a different non-neutral direction
        is_direction_change = (
            prev_direction not in ("neutral", None)
            and direction not in ("neutral", "")
            and direction != prev_direction
        )

        if is_direction_change and significant:
            # Score the flip
            flip_score = _score_flip_significance(
                ticker, direction, confidence, net_score,
                prev_direction, prev_confidence, prev_net,
                consensus_meta,
            )

            is_real_flip = flip_score >= FLIP_SCORE_THRESHOLD
            streak = _consecutive_same.get(ticker, 1)

            flip_event = {
                "ticker": ticker,
                "from": prev_direction,
                "to": direction,
                "confidence": confidence,
                "net_score": net_score,
                "score": round(flip_score, 4),
                "is_real": is_real_flip,
                "state": new_state,
                "streak": streak,
                "cycle_id": cycle_id,
                "timestamp": time.time(),
                "reasons": _build_flip_reasons(
                    flip_score, net_score - prev_net,
                    consensus_meta, streak, new_state,
                ),
            }

            # Store in flip history
            _flip_history.setdefault(ticker, [])
            _flip_history[ticker].append(flip_event)
            if len(_flip_history[ticker]) > MAX_FLIP_HISTORY:
                _flip_history[ticker].pop(0)

            if is_real_flip:
                _last_flip_cycle[ticker] = cycle_id

        return flip_event


def _build_flip_reasons(
    score: float,
    net_score_change: float,
    meta: dict,
    streak: int,
    state: str,
) -> str:
    """Build a readable reason string for the flip."""
    parts = []
    if score >= 0.8:
        parts.append("major_reversal")
    elif score >= 0.6:
        parts.append("significant_reversal")
    else:
        parts.append("minor_reversal")
    parts.append(f"score={score:.2f}")
    if abs(net_score_change) > 0.3:
        parts.append("large_magnitude")
    parts.append(f"streak={streak}")
    parts.append(f"state={state}")
    return "|".join(parts)


def get_ticker_state(ticker: str) -> dict:
    """Get the current state info for a single ticker."""
    with _lock:
        mem = _signal_memory.get(ticker, [])
        return {
            "ticker": ticker,
            "state": _signal_state.get(ticker, "none"),
            "state_since": _state_since.get(ticker, 0),
            "active_direction": _active_direction.get(ticker, "neutral"),
            "consecutive_same": _consecutive_same.get(ticker, 0),
            "consecutive_neutral": _consecutive_neutral.get(ticker, 0),
            "memory_depth": len(mem),
            "last_flip_cycle": _last_flip_cycle.get(ticker, 0),
            "current_signal": mem[-1] if mem else None,
            "trend": _get_trend_direction(mem) if len(mem) >= 3 else "flat",
        }


def get_all_states() -> dict:
    """Get state info for all tracked tickers.

    Returns dict grouped by state for dashboard consumption.
    """
    with _lock:
        tickers = set(list(_signal_state.keys()) + list(_active_direction.keys()))
        result = {"by_state": {}, "summary": {}}
        state_counts = {}
        for ticker in sorted(tickers):
            st = get_ticker_state(ticker)
            s = st["state"]
            state_counts[s] = state_counts.get(s, 0) + 1
            result["by_state"].setdefault(s, []).append(st)

        result["summary"] = {
            "total_tracked": len(tickers),
            "confirmed": state_counts.get("confirmed", 0),
            "active": state_counts.get("active", 0),
            "pending": state_counts.get("pending", 0),
            "watching": state_counts.get("watching", 0),
            "none": state_counts.get("none", 0),
        }
        return result


def get_flip_history(ticker: str | None = None, min_score: float = 0.0) -> list[dict]:
    """Get flip history, optionally filtered by ticker and minimum score.

    Args:
        ticker: If set, return flips only for this ticker
        min_score: Minimum flip significance score (0-1). Only flips >= this returned.

    Returns:
        List of flip events sorted by time (most recent first).
    """
    with _lock:
        all_flips = []
        if ticker:
            flips = _flip_history.get(ticker, [])
            for f in flips:
                if f.get("score", 0) >= min_score:
                    all_flips.append(f)
        else:
            for t, flips in _flip_history.items():
                for f in flips:
                    if f.get("score", 0) >= min_score:
                        all_flips.append(f)
        all_flips.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return all_flips


def get_significant_flips(min_score: float = 0.60) -> dict:
    """Get only significant flips, grouped by is_real.

    Returns:
        {"real": [...], "potential": [...]}
    """
    with _lock:
        real = []
        potential = []
        for t, flips in _flip_history.items():
            for f in flips:
                if f.get("score", 0) >= min_score:
                    if f.get("is_real", False):
                        real.append(f)
                    else:
                        potential.append(f)
        real.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        potential.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return {"real": real, "potential": potential}


def get_signal_strength(ticker: str) -> float:
    """Compute current signal strength for a ticker (0-1).

    Combines:
    - State conviction weight
    - Consecutive same-direction bonus
    - Confidence from last cycle
    """
    with _lock:
        state = _signal_state.get(ticker, "none")
        state_weight = SIGNAL_STATES.get(state, 0) / 4.0  # 0-1
        streak = _consecutive_same.get(ticker, 0)
        streak_bonus = min(streak * 0.1, 0.3)
        mem = _signal_memory.get(ticker, [])
        latest_conf = mem[-1].get("confidence", 0) if mem else 0
        return min(state_weight * 0.5 + streak_bonus * 0.3 + latest_conf * 0.2, 1.0)


# ═══════════════════════════════════════════════════════════════════
# Dynamic Strategy Authority (Gap 2)
# ═══════════════════════════════════════════════════════════════════

def update_strategy_performance(
    strategy_name: str,
    strategy_direction: str,
    consensus_direction: str,
):
    """Record whether a strategy's prediction was correct (matched consensus direction).

    Args:
        strategy_name: Name of the strategy
        strategy_direction: The direction the strategy predicted ('long', 'short', 'neutral')
        consensus_direction: The final consensus direction ('long', 'short', 'neutral')

    Strategies that predict 'neutral' are not tracked (no directional bet made).
    """
    if strategy_direction == "neutral" or consensus_direction == "neutral":
        return  # No clear directional bet — skip performance tracking

    was_correct = strategy_direction == consensus_direction

    with _lock:
        _strategy_performance.setdefault(strategy_name, [])
        _strategy_performance[strategy_name].append(was_correct)
        if len(_strategy_performance[strategy_name]) > _STRATEGY_PERF_MAX:
            _strategy_performance[strategy_name].pop(0)


def get_strategy_authority(strategy_name: str, static_authority: float = 1.0) -> float:
    """Get dynamic authority multiplier for a strategy.

    Combines the static authority (from STRATEGY_AUTHORITY map) with a
    dynamic performance multiplier based on recent win rate.

    Formula:
        dynamic_authority = static_authority × (0.5 + 0.5 × rolling_win_rate)

    Range: static_authority × 0.5 (if win rate = 0) to static_authority × 1.0 (if win rate = 1)
    This means poor performance can halve a strategy's authority, but good performance
    can't exceed the static cap (prevents runaway amplification).

    If no performance data exists yet (new strategy), returns exactly the static authority.
    """
    with _lock:
        perf = _strategy_performance.get(strategy_name)
        if not perf or len(perf) < 3:  # Need at least 3 data points
            return static_authority

        win_rate = sum(perf) / len(perf)
        # (0.5 + 0.5 × win_rate) ranges from 0.5 to 1.0
        dynamic_mult = 0.5 + 0.5 * win_rate
        return round(static_authority * dynamic_mult, 4)


def get_strategy_performance_summary() -> dict:
    """Get performance summary for all tracked strategies.

    Returns:
        {strategy_name: {win_rate, total_predictions, recent_streak}}
    """
    with _lock:
        summary = {}
        for sname, perf in _strategy_performance.items():
            if not perf:
                continue
            total = len(perf)
            wins = sum(perf)
            recent = perf[-5:] if len(perf) >= 5 else perf
            recent_wins = sum(recent)
            summary[sname] = {
                "win_rate": round(wins / total, 4),
                "total_predictions": total,
                "recent_win_rate": round(recent_wins / len(recent), 4),
                "recent_window": len(recent),
            }
        return summary


def reset():
    """Clear all state (for testing/cleanup)."""
    with _lock:
        _signal_memory.clear()
        _signal_state.clear()
        _state_since.clear()
        _flip_history.clear()
        _consecutive_same.clear()
        _consecutive_neutral.clear()
        _active_direction.clear()
        _last_flip_cycle.clear()
        _strategy_performance.clear()
