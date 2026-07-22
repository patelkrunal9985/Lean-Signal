"""
Signal Persistence Engine — Thesis Validation + Conviction Scoring.

Every ticker has a persistent signal state driven by continuous conviction
(0.0-1.0) rather than discrete cycle counters. Conviction is computed from:

  1. Edge Remaining — fraction of entry-thesis authority that still agrees
  2. PnL Efficiency — are we making or losing money since entry?
  3. Regime Compatibility — does current regime validate the entry thesis?
  4. Time Decay — conviction drifts down naturally over time

States: NONE (0.00) → WATCHING (0.10) → PENDING (0.25) → ACTIVE (0.45) → CONFIRMED (0.70)

PnL Guardian overrides:
  - Profit > +2.0 ATR → conviction floor at 0.20 (can't drop below watching)
  - Profit > +3.0 ATR → force conviction >= 0.80 (market proved the thesis)
  - Drawdown > -2.5 ATR → force NONE (capital preservation)
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
                "last_flip_cycle": dict(_last_flip_cycle),
                "state_entry_price": dict(_state_entry_price),
                "conviction_score": dict(_conviction_score),
                "conviction_peak": dict(_conviction_peak),
                "thesis_strategies": dict(_thesis_strategies),
                "entry_cycle": dict(_entry_cycle),
                "entry_regime": dict(_entry_regime),
                # Save last 2 snapshots per ticker (enough for health score after restart)
                "signal_memory": {
                    t: mem[-2:] if len(mem) >= 2 else mem
                    for t, mem in _signal_memory.items()
                },
                "version": 2,
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
            version = data.get("version", 1)
            for d, target in [
                ("signal_state", _signal_state),
                ("state_since", _state_since),
                ("active_direction", _active_direction),
                ("last_flip_cycle", _last_flip_cycle),
                ("state_entry_price", _state_entry_price),
            ]:
                if d in data and isinstance(data[d], dict):
                    target.update(data[d])
            # New v2 format: restore conviction data
            if version >= 2:
                for d, target in [
                    ("conviction_score", _conviction_score),
                    ("conviction_peak", _conviction_peak),
                    ("entry_cycle", _entry_cycle),
                    ("entry_regime", _entry_regime),
                ]:
                    if d in data and isinstance(data[d], dict):
                        target.update(data[d])
                if "thesis_strategies" in data and isinstance(data["thesis_strategies"], dict):
                    _thesis_strategies.update(data["thesis_strategies"])
            # Restore signal memory snapshots
            if "signal_memory" in data and isinstance(data["signal_memory"], dict):
                for t, mem_snapshots in data["signal_memory"].items():
                    if isinstance(mem_snapshots, list):
                        _signal_memory[t] = mem_snapshots
        logger.info(
            "Signal state restored from disk (v%d): %d tickers, %d with memory",
            version, len(_signal_state), len(_signal_memory),
        )
    except Exception as e:
        logger.warning("Failed to restore signal state from disk: %s", e)

# ── Conviction thresholds (mapped from state names ──
SIGNAL_STATES = {
    "none": 0,
    "watching": 1,
    "weakening": 2,
    "pending": 3,
    "active": 4,
    "confirmed": 5,
}
CONVICTION_THRESHOLDS = {
    "confirmed": 0.70,
    "active": 0.45,
    "pending": 0.25,
    "watching": 0.10,
    "none": 0.00,
}

SIGNAL_STATE_DISPLAY_ORDER = ["confirmed", "active", "pending", "weakening", "watching", "none"]

# ── Module-level persistence state ──
_signal_memory: dict[str, list[dict]] = {}       # ticker → list of recent cycle snapshots
_signal_state: dict[str, str] = {}                # ticker → current state
_state_since: dict[str, float] = {}               # ticker → time when current state started
_flip_history: dict[str, list[dict]] = {}         # ticker → list of significant flip events
_active_direction: dict[str, str] = {}            # ticker → the direction we're committed to
_last_flip_cycle: dict[str, int] = {}             # ticker → cycle_id of last significant flip
_take_profit_events: dict[str, list[dict]] = {}   # ticker → list of take-profit notifications
_state_entry_price: dict[str, float] = {}         # ticker → price when current state was entered

# ── Thesis Validation ──
_conviction_score: dict[str, float] = {}          # ticker → current conviction 0.0-1.0
_conviction_peak: dict[str, float] = {}           # ticker → peak conviction since last reset
_conviction_weakening_counter: dict[str, int] = {} # ticker → cycles conviction has been dropping
_thesis_strategies: dict[str, list[dict]] = {}    # ticker → snapshot of strategy votes at entry
_entry_cycle: dict[str, int] = {}                 # ticker → cycle_id when thesis was entered
_entry_regime: dict[str, str] = {}                # ticker → primary regime at entry time

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
MAX_FLIP_HISTORY = 20              # Max flip events to keep per ticker
MAX_TAKE_PROFIT_EVENTS = 10        # Max take-profit events per ticker

# ── Conviction settings (loaded from settings_manager) ──
def _get_conviction_decay(instr_type: str = "") -> float:
    """Conviction decay per cycle (leak rate). Higher = faster decay."""
    if instr_type:
        val = get_setting(f"{instr_type}_conviction_decay", None)
        if val is not None:
            return float(val)
    return float(get_setting("conviction_decay", 0.97))

def _get_min_conviction(instr_type: str = "") -> float:
    """Minimum conviction to enter a thesis (0-1). Higher = harder to trigger."""
    if instr_type:
        val = get_setting(f"{instr_type}_min_conviction", None)
        if val is not None:
            return float(val)
    return float(get_setting("min_conviction", 0.25))

def _get_thesis_horizon(instr_type: str = "") -> int:
    """Number of cycles before a thesis is considered stale."""
    if instr_type:
        val = get_setting(f"{instr_type}_thesis_horizon", None)
        if val is not None:
            return int(val)
    return int(get_setting("thesis_horizon", 30))

def _get_pnl_profit_floor_atr() -> float:
    return float(get_setting("pnl_profit_floor_atr", 2.0))

def _get_pnl_profit_confirm_atr() -> float:
    return float(get_setting("pnl_profit_confirm_atr", 3.0))

def _get_pnl_force_exit_atr() -> float:
    return float(get_setting("pnl_force_exit_atr", 2.5))

def _get_regime_invalidation_factor() -> float:
    return float(get_setting("regime_invalidation_factor", 0.60))

def _get_velocity_spike_atr() -> float:
    """ATR/cycle threshold for instant conviction crash (0.30x)."""
    return float(get_setting("velocity_spike_atr", 0.8))

def _get_velocity_penalty_atr() -> float:
    """ATR/cycle threshold for heavy conviction penalty (0.55x)."""
    return float(get_setting("velocity_penalty_atr", 0.4))

def _get_velocity_building_max() -> float:
    """Max conviction cap when velocity supports the new thesis direction."""
    return float(get_setting("velocity_building_max", 0.70))

def _get_age_decay_start() -> float:
    return float(get_setting("signal_age_decay_start_min", 15))

def _get_age_decay_half() -> float:
    return float(get_setting("signal_age_decay_half_min", 60))

def _get_age_decay_floor() -> float:
    return float(get_setting("signal_age_decay_floor", 0.50))

def _get_flip_score_threshold() -> float:
    return float(get_setting("flip_score_threshold", 0.60))


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

    # ── Factor 3: Conviction level (weight: 0.20) ──
    conviction = _conviction_score.get(ticker, 0.0)
    score += min(conviction * 0.3, 0.20)

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


def _compute_conviction(
    ticker: str,
    direction: str,
    net_score: float,
    strategy_votes: list | None,
    prev_direction: str | None,
    current_price: float,
    cycle_id: int,
    regime: str = "",
    atr: float = 0.0,
) -> float:
    """Compute conviction 0.0-1.0 from edge remaining + building + PnL + velocity."""
    prev_state = _signal_state.get(ticker, "none")
    has_thesis = prev_state in ("pending", "active", "confirmed", "weakening")
    prev_conviction = _conviction_score.get(ticker, 0.0)
    entry_price = _state_entry_price.get(ticker, 0)

    # ── Velocity detection (ATR/cycle) ──
    velocity_atr = 0.0
    price_dir = 0.0
    if atr > 0 and current_price > 0:
        mem = _signal_memory.get(ticker, [])
        if mem:
            prev_price = mem[-1].get("price", 0)
            if prev_price > 0:
                price_dir = current_price - prev_price
                velocity_atr = abs(price_dir) / max(atr, 0.01)

    if has_thesis and _entry_cycle.get(ticker) is not None:
        # ── Active thesis: validate using edge, time, regime, PnL, velocity ──
        entry_strategies = _thesis_strategies.get(ticker, [])
        curr_votes = strategy_votes or []
        entry_dir = _active_direction.get(ticker, "long")
        elapsed = max(cycle_id - _entry_cycle.get(ticker, cycle_id), 0)

        # Edge remaining
        current = {}
        for cv in curr_votes:
            name = cv.get("name", cv.get("strategy", ""))
            current[name] = cv.get("direction", "neutral")

        if entry_strategies:
            total_auth = 0.0
            support_auth = 0.0
            for ev in entry_strategies:
                name = ev.get("name", ev.get("strategy", ""))
                vote_dir = ev.get("direction", "neutral")
                weight = float(ev.get("weight", ev.get("authority", 1.0)))
                if vote_dir != entry_dir:
                    continue
                total_auth += weight
                curr_dir = current.get(name, "neutral")
                if curr_dir == entry_dir:
                    support_auth += weight
                elif curr_dir == "neutral":
                    support_auth += weight * 0.3
            edge = (support_auth / total_auth) if total_auth > 0 else 0.5
        else:
            if direction == "neutral":
                edge = 0.3
            elif direction == entry_dir:
                edge = 0.5 + min(net_score * 0.4, 0.5)
            else:
                edge = 0.3

        maturity = min((elapsed + 2) / 4.0, 1.0)
        horizon = _get_thesis_horizon()
        time_factor = max(0.3, 1.0 - (elapsed / horizon))
        entry_reg = _entry_regime.get(ticker, "")
        regime_factor = _get_regime_invalidation_factor() if (entry_reg and regime and entry_reg != regime) else 1.0

        pnl_factor = 0.5
        if entry_price > 0 and current_price > 0:
            dir_sign = 1.0 if entry_dir == "long" else -1.0
            pnl_pct = (current_price - entry_price) / entry_price * dir_sign
            pnl_factor = max(0.0, min(1.0, 0.5 + pnl_pct * 5.0))

        conviction = edge * 0.50 + time_factor * 0.20 + regime_factor * 0.10 + pnl_factor * 0.20
        conviction *= maturity

        if direction not in ("neutral", entry_dir):
            if prev_state == "weakening":
                conviction *= 0.4
            else:
                conviction *= 0.6
        elif direction == "neutral" and prev_state == "weakening":
            conviction *= 0.5

        # ── Velocity penalty (adverse = against thesis direction) ──
        is_adverse = (entry_dir == "long" and price_dir < 0) or (entry_dir == "short" and price_dir > 0)
        if velocity_atr > 0 and is_adverse:
            spike = _get_velocity_spike_atr()
            penalty = _get_velocity_penalty_atr()
            if velocity_atr >= spike:
                conviction *= 0.30
            elif velocity_atr >= penalty:
                conviction *= 0.55
            extra_decay = min(velocity_atr * 0.12, 0.15)
            conviction *= max(_get_conviction_decay() - extra_decay, 0.80)
        else:
            if velocity_atr >= _get_velocity_penalty_atr() and not is_adverse:
                conviction = min(conviction * 1.05, 0.95)
            conviction *= _get_conviction_decay()

        # PnL Guardian overrides
        if entry_price > 0 and current_price > 0:
            price_move = (current_price - entry_price) / entry_price
            if direction == "short":
                price_move = -price_move
            if price_move > _get_pnl_profit_confirm_atr() / 100.0:
                conviction = max(conviction, 0.80)
            elif price_move > _get_pnl_profit_floor_atr() / 100.0:
                conviction = max(conviction, 0.20)
            if price_move < -_get_pnl_force_exit_atr() / 100.0:
                conviction = min(conviction, 0.05)
    else:
        # ── No active thesis: build conviction with warmup + velocity boost ──
        net_mag = abs(net_score)
        diversity = 1.0
        if strategy_votes:
            families = set(s.get("family", "") for s in strategy_votes if s.get("family"))
            diversity = min(len(families) / 3.0, 1.0)

        cycles_seen = len(_signal_memory.get(ticker, []))
        building_factor = min((cycles_seen + 2) / 5.0, 1.0)

        # ── Velocity boost: if price surging in same direction as new thesis ──
        velocity_supports = False
        if velocity_atr > 0 and direction in ("long", "short"):
            velocity_supports = (direction == "long" and price_dir > 0) or (direction == "short" and price_dir < 0)
            if velocity_supports:
                max_boost = _get_velocity_building_max() - 0.40
                v_boost = min(velocity_atr * 0.25, max_boost)
                building_factor = min(building_factor + v_boost, 1.0)

        base = net_mag * 0.8 * building_factor
        if direction == prev_direction and prev_direction not in (None, "neutral"):
            base = max(base, prev_conviction * 0.95)
        elif direction == "neutral":
            base = prev_conviction * 0.92
        elif prev_direction not in (None, "neutral"):
            base = prev_conviction * 0.5

        max_conv = _get_velocity_building_max() if velocity_supports else 0.40
        conviction = min(base * diversity, max_conv)

    conviction = max(0.0, min(1.0, conviction))
    old_peak = _conviction_peak.get(ticker, 0.0)
    if conviction >= old_peak:
        _conviction_peak[ticker] = conviction
        _conviction_weakening_counter[ticker] = 0
    _conviction_score[ticker] = conviction
    return conviction


def _compute_state(
    ticker: str,
    conviction: float,
    direction: str,
) -> tuple[str, bool]:
    """Map conviction 0.0-1.0 to a signal state.

    Returns (new_state, significant_transition).

    Strengthening → escalates through: watching → pending → active → confirmed
    Weakening → catches via: drop from peak > 0.20 triggers weakening state
    Death → conviction < 0.10 → none
    """
    prev_state = _signal_state.get(ticker, "none")
    prev_dir = _active_direction.get(ticker)

    # ── Threshold-based state selection ──
    if conviction >= 0.70:
        new_state = "confirmed"
    elif conviction >= 0.45:
        new_state = "active"
    elif conviction >= 0.25:
        new_state = "pending"
    elif conviction >= 0.10:
        new_state = "watching"
    else:
        new_state = "none"

    # ── Weakening: conviction dropped significantly from peak ──
    if prev_state in ("active", "confirmed", "weakening"):
        peak = _conviction_peak.get(ticker, conviction)
        if peak - conviction >= 0.15 and new_state != "confirmed":
            if prev_state in ("active", "confirmed"):
                new_state = "weakening"  # enter weakening
            elif conviction >= 0.10:
                new_state = "weakening"  # stay in weakening (not yet decayed)
            # else conviction < 0.10 → let threshold give "none"

    # ── Significant transition detection ──
    significant = new_state != prev_state
    if significant and new_state in ("weakening", "none") and prev_state in ("active", "confirmed"):
        significant = True
    if new_state == "pending" and prev_state in ("watching", "none"):
        significant = True

    return new_state, significant


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
    regime: str = "",
    atr: float = 0.0,
) -> dict | None:
    """Update signal state for a ticker with the latest cycle data.

    Uses conviction scoring (0-1) instead of cycle counting to determine
    signal states. Conviction is computed from thesis validation, edge
    remaining, PnL efficiency, regime compatibility, time decay, and velocity.

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
        regime: Current primary market regime
        atr: ATR in price units (e.g. 15.0 for SPX) for velocity detection. 0 = skip.

    Returns:
        Dict describing a significant flip event, or None if nothing noteworthy.
    """
    global _signal_memory, _signal_state, _state_since
    global _active_direction, _flip_history, _last_flip_cycle
    global _state_entry_price, _conviction_score, _conviction_peak
    global _conviction_weakening_counter

    with _lock:
        # ── Determine previous direction ──
        current_state = _signal_state.get(ticker, "none")
        if current_state == "none" or _active_direction.get(ticker) is None:
            prev_direction = None
        else:
            prev_direction = _active_direction.get(ticker, "neutral")
        prev_confidence = _get_prev_confidence(ticker)
        prev_net = _get_prev_net(ticker)

        # ── Compute conviction ──
        conviction = _compute_conviction(
            ticker, direction, net_score, strategy_votes,
            prev_direction, current_price, cycle_id, regime, atr,
        )

        # ── Update memory ──
        snapshot = {
            "direction": direction,
            "confidence": confidence,
            "net_score": net_score,
            "cycle_id": cycle_id,
            "timestamp": time.time(),
            "price": current_price,
            "state": None,
            "families": consensus_meta.get("consensus_families", {}),
            "family_count": consensus_meta.get("consensus_family_count", 0),
            "agreement_cv": consensus_meta.get("consensus_agreement_cv", 0.5),
            "threshold": consensus_meta.get("consensus_threshold", 0.2),
            "dominant_share": consensus_meta.get("consensus_dominant_share", 0.0),
            "active_votes": consensus_meta.get("consensus_active_votes", 0),
            "weighted_long": consensus_meta.get("consensus_weighted_long", 0),
            "weighted_short": consensus_meta.get("consensus_weighted_short", 0),
            "instrument_type": instrument_type,
            "conviction": round(conviction, 4),
        }
        _signal_memory.setdefault(ticker, [])
        _signal_memory[ticker].append(snapshot)
        if len(_signal_memory[ticker]) > MAX_MEMORY:
            _signal_memory[ticker].pop(0)

        # ── Compute new state from conviction ──
        new_state, significant = _compute_state(ticker, conviction, direction)
        snapshot["state"] = new_state

        old_state = _signal_state.get(ticker, "none")
        if new_state != old_state:
            _signal_state[ticker] = new_state
            _state_since[ticker] = time.time()
            was_thesis = old_state in ("pending", "active", "confirmed")

            if new_state in ("pending", "active", "confirmed"):
                _active_direction[ticker] = direction
                _state_entry_price[ticker] = current_price
                # Snapshot thesis on entry (first time entering thesis state)
                if not was_thesis:
                    _thesis_strategies[ticker] = list(strategy_votes or [])
                    _entry_cycle[ticker] = cycle_id
                    _entry_regime[ticker] = regime
                    _conviction_peak[ticker] = conviction
                    _conviction_weakening_counter[ticker] = 0
                    logger.info(
                        "%s: THESIS ENTRY — state=%s dir=%s conviction=%.3f",
                        ticker, new_state, direction, conviction,
                    )
            elif new_state == "weakening":
                if prev_direction and prev_direction != "neutral":
                    _active_direction[ticker] = prev_direction
            elif new_state == "watching":
                if direction != "neutral":
                    _active_direction[ticker] = direction
            elif new_state == "none":
                _active_direction.pop(ticker, None)
                _thesis_strategies.pop(ticker, None)
                _entry_cycle.pop(ticker, None)
                _entry_regime.pop(ticker, None)
                _conviction_peak.pop(ticker, None)
                _conviction_weakening_counter.pop(ticker, None)
                _conviction_score.pop(ticker, None)

                logger.info(
                    "%s: THESIS EXIT — prev_state=%s prev_dir=%s",
                    ticker, old_state, prev_direction,
                )

            # ── Take-profit event on weakening ──
            if old_state in ("active", "confirmed") and new_state == "weakening":
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
                    "conviction": round(conviction, 4),
                }
                _take_profit_events.setdefault(ticker, [])
                _take_profit_events[ticker].append(tp_event)
                if len(_take_profit_events[ticker]) > MAX_TAKE_PROFIT_EVENTS:
                    _take_profit_events[ticker].pop(0)
                logger.info(
                    "%s: TAKE PROFIT — %s → %s (conviction=%.3f)",
                    ticker, old_state.upper(), new_state.upper(), conviction,
                )
        else:
            if direction != "neutral":
                _active_direction[ticker] = direction

        # ── Detect significant flips ──
        flip_event = None
        is_direction_change = (
            prev_direction not in ("neutral", None)
            and direction not in ("neutral", "")
            and direction != prev_direction
        )

        if is_direction_change and significant:
            flip_score = _score_flip_significance(
                ticker, direction, confidence, net_score,
                prev_direction, prev_confidence, prev_net,
                consensus_meta,
            )
            is_real_flip = flip_score >= _get_flip_score_threshold()
            flip_event = {
                "ticker": ticker,
                "from": prev_direction,
                "to": direction,
                "confidence": confidence,
                "net_score": net_score,
                "score": round(flip_score, 4),
                "is_real": is_real_flip,
                "state": new_state,
                "conviction": round(conviction, 4),
                "cycle_id": cycle_id,
                "timestamp": time.time(),
                "reasons": _build_flip_reasons(
                    flip_score, net_score - prev_net,
                    consensus_meta, new_state,
                ),
            }
            _flip_history.setdefault(ticker, [])
            _flip_history[ticker].append(flip_event)
            if len(_flip_history[ticker]) > MAX_FLIP_HISTORY:
                _flip_history[ticker].pop(0)
            if is_real_flip:
                _last_flip_cycle[ticker] = cycle_id

        # ── Track top contributors ──
        if strategy_votes and len(strategy_votes) > 0:
            sorted_votes = sorted(
                strategy_votes,
                key=lambda s: float(s.get("confidence", 0)) * float(s.get("weight", 0)),
                reverse=True,
            )
            top3 = [s.get("name", s.get("strategy", "?")) for s in sorted_votes[:3]]
            _last_top_contributors[ticker] = top3

        # ── Track last strong confirmation cycle ──
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
            "conviction": round(_conviction_score.get(ticker, 0.0), 4),
            "conviction_peak": round(_conviction_peak.get(ticker, 0.0), 4),
            "memory_depth": len(mem),
            "last_flip_cycle": _last_flip_cycle.get(ticker, 0),
            "current_signal": mem[-1] if mem else None,
            "trend": _get_trend_direction(mem) if len(mem) >= 3 else "flat",
            "age_decay": decay,
            "decayed_confidence": round(decayed_conf, 4),
            "state_entry_price": _state_entry_price.get(ticker, 0),
            "instrument_type": mem[-1].get("instrument_type", "") if mem else "",
            "has_thesis": _entry_cycle.get(ticker) is not None,
            "entry_cycle": _entry_cycle.get(ticker, 0),
            "entry_regime": _entry_regime.get(ticker, ""),
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

    Uses conviction as the primary signal, blended with confidence and age.
    """
    with _lock:
        conviction = _conviction_score.get(ticker, 0.0)
        mem = _signal_memory.get(ticker, [])
        latest_conf = mem[-1].get("confidence", 0) if mem else 0
        age_sec = time.time() - mem[-1].get("timestamp", time.time()) if mem else 0
        age_min = age_sec / 60.0
        if age_min > _get_age_decay_start():
            decay = max(_get_age_decay_floor(), 1.0 - (age_min / _get_age_decay_half()))
            latest_conf *= decay
        return min(conviction * 0.7 + latest_conf * 0.3, 1.0)


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
        _conviction_score.clear()
        _conviction_peak.clear()
        _conviction_weakening_counter.clear()
        _thesis_strategies.clear()
        _entry_cycle.clear()
        _entry_regime.clear()


# ── Restore state from disk on import ──
_autoload()
