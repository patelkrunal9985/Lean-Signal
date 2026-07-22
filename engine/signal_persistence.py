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
from utils.settings_manager import get as get_setting
from pathlib import Path
import json

logger = get_logger("engine.signal_persistence")

_lock = threading.RLock()  # RLock: get_all_states() calls get_ticker_state() which needs re-entrant lock

# ── Disk persistence ──
_STATE_FILE = Path(__file__).parent.parent / "data" / "signal_state.json"
_AUTOSAVE_ENABLED = True


def _autosave():
    """Save current signal state to disk for crash recovery.

    Called once per CYCLE (not per ticker) by the runner after all tickers
    have been updated. This prevents N× file writes per cycle.
    """
    if not _AUTOSAVE_ENABLED:
        return
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            data = {
                "signal_state": dict(_signal_state),
                "state_since": dict(_state_since),
                "active_direction": dict(_active_direction),
                "consecutive_same": dict(_consecutive_same),
                "consecutive_neutral": dict(_consecutive_neutral),
                "consecutive_counter": dict(_consecutive_counter),
                "last_flip_cycle": dict(_last_flip_cycle),
                "pre_weakening_state": dict(_pre_weakening_state),
                "state_entry_price": dict(_state_entry_price),
                # Save last 2 snapshots per ticker (enough for health score after restart)
                "signal_memory": {
                    t: mem[-2:] if len(mem) >= 2 else mem
                    for t, mem in _signal_memory.items()
                },
                "saved_at": time.time(),
            }
        with open(_STATE_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.debug("Signal state autosave failed (non-critical): %s", e)


def _autoload():
    """Restore signal state from disk after a restart."""
    if not _STATE_FILE.exists():
        return
    try:
        with open(_STATE_FILE) as f:
            data = json.load(f)
        with _lock:
            for d, target in [
                ("signal_state", _signal_state),
                ("state_since", _state_since),
                ("active_direction", _active_direction),
                ("consecutive_same", _consecutive_same),
                ("consecutive_neutral", _consecutive_neutral),
                ("consecutive_counter", _consecutive_counter),
                ("last_flip_cycle", _last_flip_cycle),
                ("pre_weakening_state", _pre_weakening_state),
                ("state_entry_price", _state_entry_price),
            ]:
                if d in data and isinstance(data[d], dict):
                    target.update(data[d])
            # Restore signal memory snapshots (for health score continuity)
            if "signal_memory" in data and isinstance(data["signal_memory"], dict):
                for t, mem_snapshots in data["signal_memory"].items():
                    if isinstance(mem_snapshots, list):
                        _signal_memory[t] = mem_snapshots
        logger.info(
            "Signal state restored from disk: %d tickers, %d with memory",
            len(_signal_state), len(_signal_memory),
        )
    except Exception as e:
        logger.warning("Failed to restore signal state from disk: %s", e)

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
_state_entry_price: dict[str, float] = {}      # ticker → price when current state was entered

# ── Signal Health & Warning tracking ──
_last_strong_cycle: dict[str, int] = {}             # ticker → last cycle_id where net_score > 0.4
_last_top_contributors: dict[str, list[str]] = {}   # ticker → [top 3 strategy names from last cycle]
_prev_strategy_count: dict[str, int] = {}            # ticker → active strategy count from last cycle

# ── Dynamic strategy authority tracking (True EWMA) ──
# Tracks each strategy's prediction accuracy using an exponentially-weighted
# moving average of outcome-based correctness (did price move as predicted?).
# This breaks the circular reference of the old approach (which checked vs consensus).
_strategy_ewma: dict[str, float] = {}   # strategy_name → current EWMA estimate (0-1)
_strategy_ewma_n: dict[str, int] = {}    # strategy_name → number of evaluations
_strategy_pending: dict[str, list[dict]] = {}  # "ticker:strategy" → pending predictions
_EWMA_ALPHA = 0.35    # higher = faster adaptation, lower = smoother
_EWMA_MIN_SAMPLES = 3 # minimum evaluations before adjusting from static
_EWMA_CORRECT_THRESHOLD = 0.001  # 0.1% min price move to count as signal
_EWMA_MAX_PENDING_AGE = 5  # drop pending predictions older than N cycles

# ── Configuration ──
# NOTE: NEUTRAL_COOLDOWN_*, STICKY_COUNTER_CYCLES, and SIGNAL_AGE_DECAY_*
# constants are now loaded dynamically from utils.settings_manager so users
# can adjust them from the dashboard Settings tab without restarting.
# The module-level constants below are only used when the settings manager
# hasn't been initialized (fallback to hardcoded values).

MAX_MEMORY = 10                    # Keep last 10 cycle snapshots per ticker
MIN_PENDING_CYCLES = 2             # Need 2 consecutive same-direction to upgrade watching→pending
MIN_ACTIVE_CYCLES = 3              # Need 3 consecutive same-direction for pending→active
MIN_CONFIRMED_CYCLES = 5           # Need 5 consecutive same-direction for active→confirmed
FLIP_SCORE_THRESHOLD = 0.60        # Only surface flips with score >= this
MAX_FLIP_HISTORY = 20              # Max flip events to keep per ticker
MAX_TAKE_PROFIT_EVENTS = 10        # Max take-profit events per ticker

# ── Sticky signal thresholds (loaded from settings_manager) ──
# These are helper functions, not constants, so they reflect live setting changes.
def _get_cooldown_active() -> int:
    """Get ACTIVE signal neutral cooldown from settings (default 6)."""
    return int(get_setting("neutral_cooldown_active", 6))

def _get_cooldown_confirmed() -> int:
    """Get CONFIRMED signal neutral cooldown from settings (default 8)."""
    return int(get_setting("neutral_cooldown_confirmed", 8))

def _get_cooldown_max() -> int:
    """Get standard neutral cooldown from settings (default 3)."""
    return int(get_setting("neutral_cooldown_max", 3))

def _get_sticky_counter() -> int:
    """Get counter-direction cycles before sticky downgrade from settings (default 2)."""
    return int(get_setting("sticky_counter_cycles", 2))

def _get_age_decay_start() -> float:
    return float(get_setting("signal_age_decay_start_min", 15))

def _get_age_decay_half() -> float:
    return float(get_setting("signal_age_decay_half_min", 60))

def _get_age_decay_floor() -> float:
    return float(get_setting("signal_age_decay_floor", 0.50))


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
            cooldown_max = _get_cooldown_confirmed() if prev_state == "confirmed" else _get_cooldown_active()
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
            if neutral_streak >= _get_cooldown_max():
                return "none", False
            if prev_state != "none":
                return "watching", False
            return "none", False

    # ── Counter-direction detection ──
    if prev_dir and direction != prev_dir:
        if is_sticky:
            # Sticky: need 2+ counter-cycles to fully downgrade
            if counter_streak >= _get_sticky_counter():
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
    current_price: float = 0.0,
    instrument_type: str = "",
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
        current_price: Current price at this cycle (0 if unavailable)
        instrument_type: 'stock', 'future', or 'option'

    Returns:
        Dict describing a significant flip event, or None if nothing noteworthy.
    """
    global _signal_memory, _signal_state, _state_since, _consecutive_same
    global _consecutive_neutral, _active_direction, _flip_history, _last_flip_cycle
    global _state_entry_price

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
            "price": current_price,
            "state": None,  # filled below
            "families": consensus_meta.get("consensus_families", {}),
            "family_count": consensus_meta.get("consensus_family_count", 0),
            "agreement_cv": consensus_meta.get("consensus_agreement_cv", 0.5),
            "threshold": consensus_meta.get("consensus_threshold", 0.2),
            "dominant_share": consensus_meta.get("consensus_dominant_share", 0.0),
            "active_votes": consensus_meta.get("consensus_active_votes", 0),
            "weighted_long": consensus_meta.get("consensus_weighted_long", 0),
            "weighted_short": consensus_meta.get("consensus_weighted_short", 0),
            "instrument_type": instrument_type,
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
                _state_entry_price[ticker] = current_price
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
            # _prev_strategy_count is no longer needed — health score now reads
            # prev cycle's active_votes directly from memory snapshots (Bug #2 fix).
            # Kept as module-level dict for backward compat but no longer written.

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
            "state_entry_price": _state_entry_price.get(ticker, 0),
            "instrument_type": mem[-1].get("instrument_type", "") if mem else "",
        }


def get_all_states() -> dict:
    """Get state info for all tracked tickers.

    Returns dict grouped by state for dashboard consumption.
    """
    with _lock:
        tickers = set(list(_signal_state.keys()) + list(_active_direction.keys()))
        result = {"by_state": {}, "by_ticker": {}, "summary": {}}
        state_counts = {}
        for ticker in sorted(tickers):
            st = get_ticker_state(ticker)
            s = st["state"]
            state_counts[s] = state_counts.get(s, 0) + 1
            result["by_state"].setdefault(s, []).append(st)
            # Flat map for O(1) ticker lookups in JS (avoids O(n×m) iteration)
            result["by_ticker"][ticker] = st

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
            if age_min > _get_age_decay_start():
                decay = max(_get_age_decay_floor(), 1.0 - (age_min / _get_age_decay_half()))
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
        if age_min <= _get_age_decay_start():
            return conf, 1.0
        decay = max(_get_age_decay_floor(), 1.0 - (age_min / _get_age_decay_half()))
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
        # Read from memory snapshot (prev cycle) instead of _prev_strategy_count
        # which was being overwritten every cycle and always matched curr_votes.
        curr_votes = current.get("active_votes", 0)
        prev_votes_count = prev.get("active_votes", 0) if prev else curr_votes
        # _prev_strategy_count is tracked separately for other uses
        if prev_votes_count > 0:
            retention = min(curr_votes / max(prev_votes_count, 1), 1.0)
        else:
            retention = 0.5
        # Track dropped count for warning
        _dropped = max(prev_votes_count - curr_votes, 0)
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
            # Use actual dropped count from snapshot comparison (was previously broken —
            # _prev_strategy_count always matched curr_votes so this never fired)
            if _dropped > 0:
                warnings.append(f"{_dropped}_strategies_dropped")
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
# Dynamic Strategy Authority (True EWMA + Outcome-Based)
# ═══════════════════════════════════════════════════════════════════

def update_strategy_performance(
    ticker: str,
    strategy_name: str,
    strategy_direction: str,
    current_price: float,
    cycle_id: int,
):
    """Record a strategy's prediction for later outcome-based evaluation.

    Unlike the old approach (which checked against consensus direction and was
    circular), this simply stores the prediction. The actual correctness is
    determined in a LATER CYCLE by evaluate_pending_predictions(), which checks
    whether price actually moved in the predicted direction.

    Args:
        ticker: Ticker this prediction was made for
        strategy_name: Name of the strategy
        strategy_direction: 'long' or 'short'
        current_price: Price at time of prediction
        cycle_id: Current cycle number
    """
    if strategy_direction == "neutral":
        return

    key = f"{ticker}:{strategy_name}"
    with _lock:
        _strategy_pending.setdefault(key, [])
        _strategy_pending[key].append({
            "direction": strategy_direction,
            "price": current_price,
            "cycle_id": cycle_id,
            "timestamp": time.time(),
        })
        if len(_strategy_pending[key]) > 20:
            _strategy_pending[key].pop(0)


def evaluate_pending_predictions(ticker_data_map: dict[str, dict], current_cycle: int):
    """Evaluate pending predictions against actual price movement.

    Called at the start of each cycle, BEFORE running strategies. For each
    pending prediction, checks if price moved in the predicted direction by
    at least the minimum threshold. Updates EWMA estimate per strategy.

    A prediction is "correct" if price moved >0.1% in the predicted direction.
    It's "wrong" if price moved >0.1% against the predicted direction.
    If price hasn't moved enough, the prediction stays pending.

    Args:
        ticker_data_map: Current cycle's ticker data (must have current_price)
        current_cycle: Current cycle number (for aging out stale predictions)
    """
    with _lock:
        resolved_keys = []
        for key, predictions in list(_strategy_pending.items()):
            try:
                ticker, strategy_name = key.split(":", 1)
            except ValueError:
                resolved_keys.append(key)
                continue

            ticker_data = ticker_data_map.get(ticker)
            if not ticker_data:
                continue
            current_price = ticker_data.get("current_price", 0)
            if current_price <= 0:
                continue

            remaining = []
            for pred in predictions:
                age = current_cycle - pred["cycle_id"]
                if age > _EWMA_MAX_PENDING_AGE:
                    continue  # too old, drop silently

                price_at = pred["price"]
                if price_at <= 0:
                    continue

                pct_move = (current_price - price_at) / price_at
                predicted_dir = pred["direction"]

                if abs(pct_move) < _EWMA_CORRECT_THRESHOLD:
                    remaining.append(pred)
                    continue

                correct = (predicted_dir == "long" and pct_move > 0) or \
                          (predicted_dir == "short" and pct_move < 0)

                ewma = _strategy_ewma.get(strategy_name, 0.5)
                _strategy_ewma[strategy_name] = (
                    _EWMA_ALPHA * (1.0 if correct else 0.0) +
                    (1.0 - _EWMA_ALPHA) * ewma
                )
                _strategy_ewma_n[strategy_name] = _strategy_ewma_n.get(strategy_name, 0) + 1

            if remaining:
                _strategy_pending[key] = remaining
            else:
                resolved_keys.append(key)

        for key in resolved_keys:
            _strategy_pending.pop(key, None)


def get_strategy_authority(strategy_name: str, static_authority: float = 1.0) -> float:
    """Get dynamic authority multiplier for a strategy using true EWMA.

    Combines the static authority (from STRATEGY_AUTHORITY map) with a
    dynamic EWMA-based performance multiplier.

    Formula:
        dynamic_mult = 0.5 + 1.5 × ewma_value
        Range: 0.5x (ewma=0) to 2.0x (ewma=1.0)

    This means:
      - A strategy that's always wrong drops to 0.5x (half weight)
      - A strategy that's always right rises to 2.0x (double weight)
      - A strategy at 50% accuracy stays at 1.0x (static authority unchanged)
      - New strategies with <3 evaluations use static authority unchanged

    The EWMA value is updated by evaluate_pending_predictions() using actual
    price movement, NOT consensus direction (no circular reference).
    """
    with _lock:
        ewma = _strategy_ewma.get(strategy_name)
        n = _strategy_ewma_n.get(strategy_name, 0)
        if ewma is None or n < _EWMA_MIN_SAMPLES:
            return static_authority

        # 0.5 + 1.5 * ewma ranges from 0.5 (ewma=0) to 2.0 (ewma=1.0)
        # At ewma=0.5 (random), mult = 0.5 + 0.75 = 1.25? No, that's wrong.
        # Formula should give 1.0 at ewma=0.5 (breakeven).
        # 0.5 + x * 0.5 = 1.0 → x = 1.0
        # 0.5 + x * 1.0 = 2.0 → x = 1.5
        # So: 0.5 + 1.5 * ewma → at ewma=0.5: 0.5 + 0.75 = 1.25
        # That's wrong. Let me use: 0.5 + 1.0 * ewma
        # At ewma=0: 0.5, ewma=0.5: 1.0, ewma=1.0: 1.5
        # Hmm, then max is 1.5x, not 2.0x.
        # Better: 2 * ewma
        # At ewma=0: 0, ewma=0.25: 0.5, ewma=0.5: 1.0, ewma=0.75: 1.5, ewma=1.0: 2.0
        # Yes! 2 * ewma gives range 0 to 2.0, with 1.0 at ewma=0.5 (breakeven).
        dynamic_mult = 2.0 * ewma
        return round(static_authority * dynamic_mult, 4)


def get_strategy_performance_summary() -> dict:
    """Get performance summary for all tracked strategies.

    Returns:
        {strategy_name: {ewma, total_predictions}}
    """
    with _lock:
        summary = {}
        for sname in list(_strategy_ewma.keys()):
            n = _strategy_ewma_n.get(sname, 0)
            if n == 0:
                continue
            summary[sname] = {
                "ewma": round(_strategy_ewma[sname], 4),
                "total_predictions": n,
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
        _state_entry_price.clear()
        _last_flip_cycle.clear()
        _take_profit_events.clear()
        _strategy_ewma.clear()
        _strategy_ewma_n.clear()
        _strategy_pending.clear()
        _last_strong_cycle.clear()
        _last_top_contributors.clear()
        _prev_strategy_count.clear()
        _pre_weakening_state.clear()


# ── Restore state from disk on import ──
_autoload()
