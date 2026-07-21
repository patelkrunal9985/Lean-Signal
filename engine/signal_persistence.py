"""
Signal Persistence Engine — State Machine + Hysteresis + Flip Scoring.

Every ticker has a persistent signal state that builds conviction over time
instead of flipping on every single cycle change.

States: NONE → WATCHING → PENDING → ACTIVE → CONFIRMED
         ↑                  ↓
         └── WEAKENING ←────┘  (sticky: ACTIVE/CONFIRMED don't vanish silently)

Key design decisions:
- A direction change requires 2+ consecutive cycles before triggering a flip
- Neutral is treated as a cooldown state, not a direction
- Flip significance is scored (0-1) based on magnitude, agreement, regime alignment
- Only flips with score >= 0.6 are surfaced as "real flips" to the dashboard
- Lower-scoring flips are shown as "potential flips" in a separate list
- ACTIVE/CONFIRMED signals are "sticky" — they don't disappear on one neutral cycle.
  Instead they enter WEAKENING state, firing a "take profit" notification.
- Signal age decay: older signals lose confidence over time.
"""
from __future__ import annotations
import time
import threading
from typing import Any

from utils.logger import get_logger

logger = get_logger("engine.signal_persistence")

_lock = threading.RLock()  # RLock: get_all_states() calls get_ticker_state() which needs re-entrant lock

# Track pre-weakening state for proper recovery
_pre_weakening_state: dict[str, str] = {}  # ticker → state before weakening

# ── All possible signal states in conviction order ──
SIGNAL_STATES = {
    "none": 0,
    "watching": 1,
    "weakening": 2,
    "pending": 3,
    "active": 4,
    "confirmed": 5,
}

# Display order for UI (highest conviction first)
SIGNAL_STATE_DISPLAY_ORDER = ["confirmed", "active", "pending", "weakening", "watching", "none"]

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
_take_profit_events: dict[str, list[dict]] = {}   # ticker → list of take-profit notifications
_consecutive_counter: dict[str, int] = {}         # ticker → consecutive cycles in opposite direction (for sticky downgrade)

# ── Signal Health & Warning tracking ──
_last_strong_cycle: dict[str, int] = {}             # ticker → last cycle_id where net_score > 0.4
_last_top_contributors: dict[str, list[str]] = {}   # ticker → [top 3 strategy names from last cycle]
_prev_strategy_count: dict[str, int] = {}            # ticker → active strategy count from last cycle

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
MAX_TAKE_PROFIT_EVENTS = 10        # Max take-profit events per ticker

# ── Sticky signal thresholds ──
# ACTIVE/CONFIRMED signals don't reset on a few neutral cycles.
# They enter WEAKENING first, then only reset after sustained counter-evidence.
NEUTRAL_COOLDOWN_MAX = 3           # Standard: after this many neutrals, reset to NONE
NEUTRAL_COOLDOWN_ACTIVE = 6        # For ACTIVE signals: 6 neutrals before full reset
NEUTRAL_COOLDOWN_CONFIRMED = 8     # For CONFIRMED signals: 8 neutrals before full reset
STICKY_COUNTER_CYCLES = 2          # Need 2 consecutive counter-direction cycles to downgrade from ACTIVE/CONFIRMED

# ── Signal age decay ──
# Older signals (last confirmed 30+ min ago) get their confidence decayed.
# decayed_score = score × max(0.5, 1.0 - (age_minutes / 60))
SIGNAL_AGE_DECAY_START_MIN = 15    # Start decaying after 15 minutes
SIGNAL_AGE_DECAY_HALF_MIN = 60     # 50% decay after 60 minutes
SIGNAL_AGE_DECAY_FLOOR = 0.50      # Never decay below 50%


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

    Sticky behavior for ACTIVE/CONFIRMED:
    - One neutral cycle → WEAKENING (not WATCHING)
    - One counter-direction cycle → WEAKENING
    - 2+ consecutive counter-cycles → downgrade to WATCHING
    - Extended neutrals → gradual downgrade (weakening → watching → none)
    """
    prev_state = _signal_state.get(ticker, "none")
    prev_dir = _active_direction.get(ticker)
    streak = _consecutive_same.get(ticker, 0)
    neutral_streak = _consecutive_neutral.get(ticker, 0)
    counter_streak = _consecutive_counter.get(ticker, 0)
    is_sticky = prev_state in ("active", "confirmed")

    # ── Neutral cooldown logic (extended for sticky states) ──
    if direction == "neutral":
        if is_sticky:
            cooldown_max = NEUTRAL_COOLDOWN_CONFIRMED if prev_state == "confirmed" else NEUTRAL_COOLDOWN_ACTIVE
            if neutral_streak >= cooldown_max:
                return "none", True  # Significant: CONFIRMED → NONE after extended neutrals
            # Don't weaken on the first neutral — sticky signals survive one neutral
            if neutral_streak >= 3:
                return "weakening", True  # 3rd neutral: fire take-profit
            if neutral_streak >= 2:
                return "weakening", False  # 2nd neutral: enter weakening silently
            # 1st neutral: stay in current sticky state (stick!)
            return prev_state, False
        else:
            if neutral_streak >= NEUTRAL_COOLDOWN_MAX:
                return "none", False
            if prev_state != "none":
                return "watching", False
            return "none", False

    # ── Counter-direction detection ──
    if prev_dir and direction != prev_dir:
        if is_sticky:
            # Sticky: need 2+ counter-cycles to fully downgrade
            if counter_streak >= STICKY_COUNTER_CYCLES:
                return "watching", True  # Significant: downgrade from sticky → watching
            # First counter-cycle → WEAKENING (fire take-profit)
            return "weakening", True  # Significant! Take-profit notification
        else:
            # Non-sticky: immediate downgrade to watching
            return "watching", False

    # ── Recovery from weakening (same direction again) ──
    if prev_state == "weakening" and prev_dir and direction == prev_dir:
        # Restore to one level below the pre-weakening state
        # Track what state we weakened from via a module-level dict
        pre_weaken = _pre_weakening_state.get(ticker, "pending")
        if pre_weaken == "confirmed":
            return "active", True  # CONFIRMED→WEAKENING→ACTIVE
        elif pre_weaken == "active":
            return "pending", True  # ACTIVE→WEAKENING→PENDING
        else:
            return "pending", True

    # ── Same direction, escalate based on streak ──
    if streak >= MIN_CONFIRMED_CYCLES:
        return "confirmed", prev_state not in ("confirmed",)
    if streak >= MIN_ACTIVE_CYCLES:
        return "active", prev_state not in ("active", "confirmed")
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
            "agreement_cv": consensus_meta.get("consensus_agreement_cv", 0.5),
            "threshold": consensus_meta.get("consensus_threshold", 0.2),
            "dominant_share": consensus_meta.get("consensus_dominant_share", 0.0),
            "active_votes": consensus_meta.get("consensus_active_votes", 0),
            "weighted_long": consensus_meta.get("consensus_weighted_long", 0),
            "weighted_short": consensus_meta.get("consensus_weighted_short", 0),
        }
        _signal_memory.setdefault(ticker, [])
        _signal_memory[ticker].append(snapshot)
        if len(_signal_memory[ticker]) > MAX_MEMORY:
            _signal_memory[ticker].pop(0)

        # ── Update consecutive counters ──
        if direction == "neutral":
            _consecutive_neutral[ticker] = _consecutive_neutral.get(ticker, 0) + 1
            _consecutive_same[ticker] = 0
            _consecutive_counter[ticker] = 0
        elif direction == prev_direction:
            _consecutive_same[ticker] = _consecutive_same.get(ticker, 0) + 1
            _consecutive_neutral[ticker] = 0
            _consecutive_counter[ticker] = 0
        elif prev_direction is not None and direction != prev_direction:
            # Opposite direction from committed direction
            _consecutive_counter[ticker] = _consecutive_counter.get(ticker, 0) + 1
            _consecutive_same[ticker] = 1  # Starting new streak in current direction
            _consecutive_neutral[ticker] = 0
        else:
            _consecutive_same[ticker] = 1  # Starting new streak
            _consecutive_neutral[ticker] = 0
            _consecutive_counter[ticker] = 0

        # ── Compute new state ──
        new_state, significant = _compute_state(direction, ticker)
        snapshot["state"] = new_state

        old_state = _signal_state.get(ticker, "none")
        if new_state != old_state:
            # State changed
            _signal_state[ticker] = new_state
            _state_since[ticker] = time.time()
            if new_state in ("pending", "active", "confirmed"):
                _active_direction[ticker] = direction
            elif new_state == "weakening":
                # Keep the active direction — it's weakening, not flipped
                if prev_direction and prev_direction != "neutral":
                    _active_direction[ticker] = prev_direction
            elif new_state == "none":
                # Clear active direction when state resets to none
                _active_direction.pop(ticker, None)
                _consecutive_counter.pop(ticker, None)

            # ── Generate take-profit event when sticky signal weakens ──
            if old_state in ("active", "confirmed") and new_state == "weakening":
                _pre_weakening_state[ticker] = old_state  # Remember for recovery
                tp_event = {
                    "ticker": ticker,
                    "type": "take_profit",
                    "from_state": old_state,
                    "to_state": new_state,
                    "direction": _active_direction.get(ticker, "neutral"),
                    "reason": _build_take_profit_reason(direction, old_state),
                    "cycle_id": cycle_id,
                    "timestamp": time.time(),
                    "confidence": confidence,
                }
                _take_profit_events.setdefault(ticker, [])
                _take_profit_events[ticker].append(tp_event)
                if len(_take_profit_events[ticker]) > MAX_TAKE_PROFIT_EVENTS:
                    _take_profit_events[ticker].pop(0)
                logger.info(
                    "%s: TAKE PROFIT — %s → %s (%s)",
                    ticker, old_state.upper(), new_state.upper(), tp_event["reason"],
                )
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

        # ── Track top contributors for key strategy exit detection ──
        if strategy_votes and len(strategy_votes) > 0:
            # Sort by contribution (confidence × weight) and keep top 3
            sorted_votes = sorted(
                strategy_votes,
                key=lambda s: float(s.get("confidence", 0)) * float(s.get("weight", 0)),
                reverse=True,
            )
            top3 = [s.get("name", s.get("strategy", "?")) for s in sorted_votes[:3]]
            _last_top_contributors[ticker] = top3
            _prev_strategy_count[ticker] = consensus_meta.get("consensus_active_votes", 0)

        # ── Track last strong confirmation cycle (for stale detection) ──
        if direction != "neutral" and abs(net_score) > 0.4:
            _last_strong_cycle[ticker] = cycle_id
            snapshot["strong_cycle"] = True
        else:
            snapshot["strong_cycle"] = False

        return flip_event


def _build_take_profit_reason(current_direction: str, from_state: str) -> str:
    """Build a readable reason for a take-profit event."""
    parts = []
    if current_direction == "neutral":
        parts.append("signal_gone_neutral")
    else:
        parts.append("counter_direction_detected")
    parts.append(f"from_{from_state}")
    return "|".join(parts)


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
        decayed_conf, decay = get_age_decayed_confidence(ticker)
        return {
            "ticker": ticker,
            "state": _signal_state.get(ticker, "none"),
            "state_since": _state_since.get(ticker, 0),
            "active_direction": _active_direction.get(ticker, "neutral"),
            "consecutive_same": _consecutive_same.get(ticker, 0),
            "consecutive_neutral": _consecutive_neutral.get(ticker, 0),
            "consecutive_counter": _consecutive_counter.get(ticker, 0),
            "memory_depth": len(mem),
            "last_flip_cycle": _last_flip_cycle.get(ticker, 0),
            "current_signal": mem[-1] if mem else None,
            "trend": _get_trend_direction(mem) if len(mem) >= 3 else "flat",
            "age_decay": decay,
            "decayed_confidence": round(decayed_conf, 4),
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
            "weakening": state_counts.get("weakening", 0),
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
    - Age decay factor
    """
    with _lock:
        state = _signal_state.get(ticker, "none")
        state_weight = SIGNAL_STATES.get(state, 0) / 5.0  # 0-1 (now 5 states)
        streak = _consecutive_same.get(ticker, 0)
        streak_bonus = min(streak * 0.1, 0.3)
        mem = _signal_memory.get(ticker, [])
        if mem:
            latest = mem[-1]
            latest_conf = latest.get("confidence", 0)
            # Apply age decay
            age_sec = time.time() - latest.get("timestamp", time.time())
            age_min = age_sec / 60.0
            if age_min > SIGNAL_AGE_DECAY_START_MIN:
                decay = max(SIGNAL_AGE_DECAY_FLOOR, 1.0 - (age_min / SIGNAL_AGE_DECAY_HALF_MIN))
                latest_conf *= decay
        else:
            latest_conf = 0
        return min(state_weight * 0.5 + streak_bonus * 0.3 + latest_conf * 0.2, 1.0)


def get_age_decayed_confidence(ticker: str) -> tuple[float, float]:
    """Get the age-decayed confidence and decay factor for a ticker.

    Returns (decayed_confidence, decay_factor).
    decay_factor = 1.0 means no decay; 0.5 means fully decayed.
    """
    with _lock:
        mem = _signal_memory.get(ticker, [])
        if not mem:
            return 0.0, 1.0
        latest = mem[-1]
        conf = latest.get("confidence", 0)
        age_sec = time.time() - latest.get("timestamp", time.time())
        age_min = age_sec / 60.0
        if age_min <= SIGNAL_AGE_DECAY_START_MIN:
            return conf, 1.0
        decay = max(SIGNAL_AGE_DECAY_FLOOR, 1.0 - (age_min / SIGNAL_AGE_DECAY_HALF_MIN))
        return conf * decay, round(decay, 2)


def get_signal_timeline(ticker: str, max_cycles: int = 10) -> list[dict]:
    """Get the signal timeline (state transitions) for a ticker.

    Returns the last N cycle snapshots with states.
    Useful for rendering a compact state-transition timeline in the dashboard.
    """
    with _lock:
        mem = _signal_memory.get(ticker, [])
        if not mem:
            return []
        timeline = []
        for snap in mem[-max_cycles:]:
            timeline.append({
                "direction": snap.get("direction", "neutral"),
                "confidence": round(snap.get("confidence", 0), 4),
                "state": snap.get("state", "none"),
                "cycle_id": snap.get("cycle_id", 0),
                "timestamp": snap.get("timestamp", 0),
            })
        return timeline


def get_take_profit_events(ticker: str | None = None) -> list[dict]:
    """Get take-profit events, optionally filtered by ticker.

    Returns most recent events first.
    """
    with _lock:
        all_events = []
        if ticker:
            events = _take_profit_events.get(ticker, [])
            all_events.extend(events)
        else:
            for t, events in _take_profit_events.items():
                all_events.extend(events)
        all_events.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return all_events


# ═══════════════════════════════════════════════════════════════════
# Signal Health Score — 5-Factor Composite (Leading Quality Metric)
# ═══════════════════════════════════════════════════════════════════

def get_signal_health_score(ticker: str) -> dict:
    """Compute a 5-factor health score for a ticker's current signal.

    Runs alongside the state machine with zero lag. Detects rotting signals
    before the state changes. Each factor is 0-1, weighted and summed to 0-100.

    Factors:
      1. Net Score Momentum (30%): Is net_score building or fading?
      2. Strategy Retention (25%): How many strategies stayed vs last cycle?
      3. Confidence Tightness (20%): Is strategy agreement tight or scattered?
      4. Family Concentration (15%): Signal dependent on single family?
      5. Threshold Margin (10%): How close to flipping neutral?

    Returns:
        {
            "health": int (0-100),
            "label": "robust" | "caution" | "fragile" | "terminal",
            "factors": {name: score},
            "warnings": [str],
        }
    """
    with _lock:
        mem = _signal_memory.get(ticker, [])
        if len(mem) < 2:
            return {"health": 50, "label": "caution", "factors": {}, "warnings": ["insufficient_data"]}

        current = mem[-1]
        prev = mem[-2]
        state = _signal_state.get(ticker, "none")
        direction = current.get("direction", "neutral")

        if direction == "neutral" or state == "none":
            return {"health": 0, "label": "terminal", "factors": {}, "warnings": ["no_active_signal"]}

        # ── Factor 1: Net Score Momentum (30%) ──
        net_now = abs(current.get("net_score", 0))
        net_prev = abs(prev.get("net_score", 0))
        if len(mem) >= 3:
            net_prev2 = abs(mem[-3].get("net_score", 0))
            # Trend: (now - oldest) / max change
            net_trend = (net_now - net_prev2) / max(abs(net_now - net_prev2), 0.01)
            net_trend = max(-1.0, min(1.0, net_trend))
            net_momentum = 0.5 + (net_trend * 0.5)  # 0 = crashing, 0.5 = flat, 1 = surging
        else:
            net_momentum = 0.5  # Neutral if not enough data

        # ── Factor 2: Strategy Retention (25%) ──
        curr_votes = current.get("active_votes", 0)
        prev_votes_count = _prev_strategy_count.get(ticker, curr_votes)
        if prev_votes_count > 0:
            retention = min(curr_votes / max(prev_votes_count, 1), 1.0)
        else:
            retention = 0.5
        # Count how many families are in current vs previous
        curr_families = set(current.get("families", {}).keys())
        prev_families = set(prev.get("families", {}).keys())
        if len(prev_families) > 0:
            family_retention = len(curr_families & prev_families) / len(prev_families)
        else:
            family_retention = 1.0
        # Blend: 60% count retention, 40% family retention
        retention_score = retention * 0.6 + family_retention * 0.4

        # ── Factor 3: Confidence Tightness (20%) ──
        cv = current.get("agreement_cv", 0.5)
        # CV < 0.3 = tight (good), CV > 0.6 = scattered (bad)
        if cv <= 0.3:
            tightness = 1.0
        elif cv >= 0.8:
            tightness = 0.0
        else:
            tightness = 1.0 - ((cv - 0.3) / 0.5)  # Linear from 1.0 to 0.0

        # ── Factor 4: Family Concentration (15%) ──
        dominant = current.get("dominant_share", 0.0)
        # > 60% dominant = risky concentration
        if dominant <= 0.40:
            diversity = 1.0
        elif dominant >= 0.70:
            diversity = 0.0
        else:
            diversity = 1.0 - ((dominant - 0.40) / 0.30)

        # ── Factor 5: Threshold Margin (10%) ──
        threshold = current.get("threshold", 0.2)
        margin = net_now - threshold
        # margin 0 = at threshold, margin 0.3+ = comfortable
        if margin <= 0.02:
            margin_score = 0.0
        elif margin >= 0.30:
            margin_score = 1.0
        else:
            margin_score = margin / 0.30

        # ── Weighted Composite ──
        health = (
            net_momentum * 0.30 +
            retention_score * 0.25 +
            tightness * 0.20 +
            diversity * 0.15 +
            margin_score * 0.10
        ) * 100
        health = max(0, min(100, int(health)))

        # ── Classification ──
        if health >= 75:
            label = "robust"
        elif health >= 50:
            label = "caution"
        elif health >= 25:
            label = "fragile"
        else:
            label = "terminal"

        # ── Collect warnings from individual factors ──
        warnings = []
        if net_momentum < 0.35:
            warnings.append("net_score_fading")
        if retention_score < 0.5:
            dropped = prev_votes_count - curr_votes
            if dropped > 0:
                warnings.append(f"{dropped}_strategies_dropped")
        if tightness < 0.4:
            warnings.append("confidence_scattered")
        if diversity < 0.4:
            warnings.append("single_family_dominant")
        if margin_score < 0.2:
            warnings.append("near_threshold")

        # ── Additional warnings ──
        # Key strategy exit detection
        prev_top3 = _last_top_contributors.get(ticker, [])
        if prev_top3 and curr_votes > 0:
            # Check if top contributors dropped out (compute from memory families)
            # Simple heuristic: if family count dropped by 2+ and dominant share rose
            if len(curr_families) < len(prev_families) - 1 and dominant > 0.55:
                warnings.append("key_families_exiting")

        # Stale confirmation warning
        last_strong = _last_strong_cycle.get(ticker, 0)
        curr_cycle = current.get("cycle_id", 0)
        stale_gap = curr_cycle - last_strong if last_strong > 0 else 0
        if stale_gap >= 5 and state in ("active", "confirmed"):
            warnings.append(f"stale_confirmation_{stale_gap}_cycles")

        return {
            "health": health,
            "label": label,
            "factors": {
                "net_momentum": round(net_momentum, 3),
                "retention": round(retention_score, 3),
                "tightness": round(tightness, 3),
                "diversity": round(diversity, 3),
                "margin": round(margin_score, 3),
            },
            "warnings": warnings,
        }


def get_price_signal_divergence(ticker: str, current_price: float, entry_price: float, atr: float) -> dict:
    """Detect when price is moving against the signal direction.

    Useful when the signal is still CONFIRMED but price already broke against it.

    Returns:
        {diverged: bool, atr_distance: float, warning: str}
    """
    with _lock:
        direction = _active_direction.get(ticker, "neutral")
        if direction == "neutral" or current_price <= 0 or entry_price <= 0 or atr <= 0:
            return {"diverged": False, "atr_distance": 0, "warning": ""}

        price_move = current_price - entry_price
        atr_dist = price_move / max(atr, 0.01)

        if direction == "long" and atr_dist < -1.5:
            return {"diverged": True, "atr_distance": round(atr_dist, 1), "warning": f"price_{abs(atr_dist):.1f}_ATR_against_long"}
        if direction == "short" and atr_dist > 1.5:
            return {"diverged": True, "atr_distance": round(atr_dist, 1), "warning": f"price_{atr_dist:.1f}_ATR_against_short"}

        if direction == "long" and atr_dist < -0.8:
            return {"diverged": False, "atr_distance": round(atr_dist, 1), "warning": f"approaching_against_long"}
        if direction == "short" and atr_dist > 0.8:
            return {"diverged": False, "atr_distance": round(atr_dist, 1), "warning": f"approaching_against_short"}

        return {"diverged": False, "atr_distance": round(atr_dist, 1), "warning": ""}


def get_all_health_scores() -> dict:
    """Get health scores for all tracked tickers with active signals."""
    with _lock:
        result = {}
        for ticker in _signal_memory:
            state = _signal_state.get(ticker, "none")
            if state in ("none",):
                continue
            result[ticker] = get_signal_health_score(ticker)
        return result


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
        _consecutive_counter.clear()
        _active_direction.clear()
        _last_flip_cycle.clear()
        _take_profit_events.clear()
        _strategy_performance.clear()
        _last_strong_cycle.clear()
        _last_top_contributors.clear()
        _prev_strategy_count.clear()
        _pre_weakening_state.clear()
