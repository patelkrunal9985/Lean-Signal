"""
Consensus Coordinator — Regime-Weighted Ensemble Direction Engine.

Replaces the naive "V3 can boost confidence" merge with a regime-aware
weighted consensus across ALL strategies (V2 + V3). Counter-trend signals
are heavily penalized unless supported by overwhelming evidence.
"""
from __future__ import annotations
from typing import Any

STRATEGY_TAXONOMY: dict[str, str] = {
    # V2 strategies
    "vw_momentum": "trend",
    "mtf_confluence": "trend",
    "market_structure": "trend",
    "advanced_momentum": "trend",
    "trend_momentum": "trend",
    "sector_relative_zscore": "trend",
    "vwap_deviation": "reversion",
    "pullback": "reversion",
    "opening_range": "reversion",
    "sr_levels": "reversion",
    "mean_reversion": "reversion",
    "order_flow_delta": "flow",
    "volume_momentum_surge": "flow",
    "rvol_absorption": "flow",
    "volume_profile": "flow",
    "spread_compression": "flow",
    "stop_hunt": "flow",
    "volatility_regime": "volatility",
    "volatility_breakout": "volatility",
    "ml_ensemble": "v3_ml",
    # V3 stock strategies  regime-aware taxonomy
    "premarket_gapper": "flow",
    "sector_rotation": "trend",
    "short_squeeze": "volatility",
    "insider_flow": "flow",
    "earnings_momentum": "volatility",
    "dark_pool_proxy": "flow",
    "pairs_trading": "reversion",
    # V3 futures strategies  regime-aware taxonomy
    "order_imbalance": "flow",
    "cot_sentiment": "trend",
    "carry_yield": "reversion",
    "calendar_spread": "reversion",
    "vix_term_structure": "volatility",
    "momentum_cross": "trend",
    "volume_spike": "flow",
    "vwap_reversion": "reversion",
    "high_low_breakout": "trend",
    "order_flow_burst": "flow",
    "delta_absorption": "flow",
    "profile_poison": "volatility",
    "session_continuation": "trend",
    "gamma_pin": "volatility",
    "order_book_velocity": "flow",
    "delta_divergence": "flow",
    "opening_range_breakout": "trend",
    "spread_reversion": "reversion",
    "session_regime": "volatility",
    "vwap_anchored": "reversion",
    "iceberg_detection": "flow",
    "time_of_day_momentum": "flow",
    "gamma_flip": "volatility",
    "volume_profile_decay": "volatility",
    "fvg_liquidity_sweep": "flow",
    "mean_reversion": "reversion",
    # V3 options strategies  regime-aware taxonomy
    "gamma_exposure": "volatility",
    "put_call_divergence": "flow",
    "iv_skew": "volatility",
    "expected_vs_actual": "reversion",
    "oi_concentration": "trend",
    "max_pain": "reversion",
    "large_option_flow": "flow",
    "theta_decay": "volatility",
    "iv_rv_spread": "volatility",
    "delta_positioning": "reversion",
    "option_volume_flow": "flow",
    "gamma_flip_levels": "volatility",
    "skew_term_structure": "volatility",
    "earnings_vol_arbitrage": "volatility",
    "zero_dte_gamma": "volatility",
    "opening_drive": "flow",
    "delta_hedging_imbalance": "reversion",
    "vwap_option_flow": "flow",
    "call_put_wall_breakout": "trend",
    "unusual_whale_flow": "flow",
    "gamma_flip_acceleration": "volatility",
    "strike_volume_surge": "flow",
    "sector_etf_option_rotation": "flow",
    "vix_spx_convexity": "volatility",
    "vanna_charm_flow": "volatility",
    "prior_hl_magnetism": "trend",
    "oi_change_rate": "flow",
    "vol_smile_curvature": "volatility",
    "delta_gamma_imbalance": "volatility",
    "breadth_confirmation": "flow",
}

REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    # Volatility bumped from 0.5→0.65 in strong trends: GEX, VIX structure,
    # and squeeze mechanics are heavily trend-confirming at extremes.
    "strong_uptrend":  {"trend": 1.2, "reversion": 0.15, "flow": 0.5, "volatility": 0.65, "v3_ml": 1.3},
    "uptrend":         {"trend": 1.1, "reversion": 0.25, "flow": 0.6, "volatility": 0.6, "v3_ml": 1.2},
    # Trend bumped from 0.5→0.65 in ranging: macro/fundamental trend
    # strategies (cot_sentiment, oi_concentration, sector_rotation)
    # remain valid structural signals in sideways markets.
    "ranging":         {"trend": 0.65, "reversion": 0.8,  "flow": 0.8, "volatility": 0.7, "v3_ml": 1.2},
    "downtrend":       {"trend": 1.1, "reversion": 0.25, "flow": 0.6, "volatility": 0.6, "v3_ml": 1.2},
    "strong_downtrend":{"trend": 1.2, "reversion": 0.15, "flow": 0.5, "volatility": 0.65, "v3_ml": 1.3},
}

V3_TAXONOMY = "v3_ml"  # legacy; voting uses per-strategy taxonomy

REGIME_TREND_DIR: dict[str, str | None] = {
    "strong_uptrend": "long",
    "uptrend": "long",
    "ranging": None,
    "downtrend": "short",
    "strong_downtrend": "short",
}

COUNTER_TREND_PENALTY = 0.25
# Base threshold: requires meaningful directional tilt
# Options get stricter threshold (0.40) — 0DTE gamma whipsaw demands strong confluence
CONSENSUS_THRESHOLD = 0.20
CONSENSUS_THRESHOLD_OPTION = 0.40
COUNTER_TREND_CONSENSUS_THRESHOLD = 0.50

# Minimum total weight required for a signal to pass.
# Prevents a single low-weight vote (e.g., reversion at 0.15 × 0.25 = 0.0375)
# from producing net = ±1.0.
MIN_PARTICIPATION_WEIGHT = 0.25

# Per-instrument-type V3 weight multipliers.
# Futures V3 strategies get 2.0x because V2 strategies are halved
# on futures (V2 strategies designed for stocks, not futures).
# V3 futures strategies (15 total) use proper futures data.
INSTR_TYPE_V3_MULTIPLIER: dict[str, float] = {
    "stock": 1.0,
    "future": 2.0,
    "option": 1.0,
}

# ── Conviction Tiers ──
# Each tier has minimum requirements. A signal must meet ALL criteria
# for its tier to be classified at that level.
CONVICTION_TIERS = {
    "bronze": {
        "min_confidence": 0.20,
        "min_strategies": 1,
        "min_families": 1,
    },
    "silver": {
        "min_confidence": 0.40,
        "min_strategies": 2,
        "min_families": 2,
        "regime_required": False,
    },
    "gold": {
        "min_confidence": 0.55,
        "min_strategies": 3,
        "min_families": 3,
        "regime_required": True,
        "counter_trend_blocked": True,
    },
    "platinum": {
        "min_confidence": 0.70,
        "min_strategies": 5,
        "min_families": 4,
        "regime_required": True,
        "counter_trend_blocked": True,
        "mtf_required": True,
        "description": "95% target: 5+ strategies from 4+ families, regime aligned, MTF confirmed",
    },
}

# Family diversity bonus: how much extra confidence for wide agreement.
FAMILY_DIVERSITY_BONUS = {
    1: 1.0,
    2: 1.10,
    3: 1.20,
    4: 1.30,
    5: 1.40,
}
# Maximum per-family contribution to prevent one family from dominating.
MAX_FAMILY_WEIGHT_SHARE = 0.40


def _get_family(strategy_name: str) -> str:
    from engine.v3.registry import STRATEGY_FAMILY_MAP
    return STRATEGY_FAMILY_MAP.get(strategy_name, "technical")


def _compute_family_diversity(votes: list[dict]) -> dict:
    families = {}
    for v in votes:
        fam = v.get("family", _get_family(v.get("name", v.get("strategy", ""))))
        families.setdefault(fam, 0)
        families[fam] += v.get("confidence", 0) * v.get("weight", 0.5)
    total = sum(families.values()) or 1
    shares = {f: s / total for f, s in families.items()}
    # Dominant family share — if >40%, penalize
    dominant = max(shares.values()) if shares else 0
    diversity_penalty = 1.0
    if dominant > MAX_FAMILY_WEIGHT_SHARE:
        diversity_penalty = MAX_FAMILY_WEIGHT_SHARE / dominant
    return {
        "families": families,
        "family_count": len(families),
        "dominant_share": round(dominant, 4),
        "diversity_penalty": round(diversity_penalty, 4),
    }


def _classify_conviction_tier(
    direction: str,
    confidence: float,
    active_votes: int,
    family_count: int,
    regime: str,
    trend_dir: str | None,
    mtf_direction: str | None,
) -> dict:
    is_counter = trend_dir is not None and direction not in ("neutral", trend_dir)
    families_ok = family_count
    votes_ok = active_votes
    conf_ok = confidence

    # Test tiers from highest to lowest
    for tier_name in ("platinum", "gold", "silver", "bronze"):
        tier = CONVICTION_TIERS[tier_name]
        if conf_ok < tier["min_confidence"]:
            continue
        if votes_ok < tier["min_strategies"]:
            continue
        if families_ok < tier["min_families"]:
            continue
        if tier.get("regime_required") and regime == "ranging" and direction == "neutral":
            continue
        if tier.get("counter_trend_blocked") and is_counter:
            continue
        if tier.get("mtf_required") and mtf_direction != direction:
            continue
        return {"tier": tier_name, "met_all": True}
    return {"tier": "bronze", "met_all": True}


def _get_taxonomy(strategy_name: str) -> str:
    return STRATEGY_TAXONOMY.get(strategy_name, "reversion")


def _get_tod_window_name() -> str:
    try:
        from engine.time_of_day import get_time_window
        return get_time_window()
    except Exception:
        return "unknown"


def compute_consensus(
    v2_results: list[dict],
    v3_results: list[dict],
    regime: str,
    ticker: str,
    instr_type: str,
    current_price: float = 0,
    atr: float = 0,
    sma_50: float = 0,
    dte: int | None = None,
) -> tuple[str, float, dict[str, Any]]:
    meta: dict[str, Any] = {}
    regime_weights = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["ranging"]).copy()
    trend_dir = REGIME_TREND_DIR.get(regime)  # None if "ranging"

    # ── Time-of-day strategy weight adjustments (0DTE-aware) ──
    from engine.time_of_day import get_strategy_time_weight
    tod_weights: dict[str, float] = {}

    # ── Regime extreme stiffening (Layer 2) ──
    # If price is extremely far from SMA_50 in the trend direction,
    # dampen trend-following weights to prevent chasing extended moves.
    regime_quality = 1.0
    if current_price > 0 and atr > 0 and sma_50 > 0 and trend_dir is not None:
        dist_atr = abs(current_price - sma_50) / max(atr, 0.01)
        if trend_dir == "short" and current_price < sma_50 and dist_atr > 3.0:
            regime_quality = 0.40
        elif trend_dir == "short" and current_price < sma_50 and dist_atr > 2.0:
            regime_quality = 0.60
        elif trend_dir == "short" and current_price < sma_50 and dist_atr > 1.5:
            regime_quality = 0.80
        elif trend_dir == "long" and current_price > sma_50 and dist_atr > 3.0:
            regime_quality = 0.40
        elif trend_dir == "long" and current_price > sma_50 and dist_atr > 2.0:
            regime_quality = 0.60
        elif trend_dir == "long" and current_price > sma_50 and dist_atr > 1.5:
            regime_quality = 0.80
        if regime_quality < 1.0:
            for k in regime_weights:
                regime_weights[k] = round(regime_weights[k] * regime_quality, 4)
            meta["consensus_regime_quality"] = round(regime_quality, 4)
    meta["consensus_regime"] = regime

    # ── Collect all votes with family info ──
    all_votes = []

    weighted_long = 0.0
    weighted_short = 0.0
    total_weight = 0.0
    active_votes = 0

    total_votes = len(v2_results) + len(v3_results)
    neutral_count = 0

    # ── V2 votes ──
    for r in v2_results:
        direction = r.get("direction", "neutral")
        confidence = r.get("confidence", 0)
        if confidence <= 0 or direction == "neutral":
            neutral_count += 1
            continue
        sname = r.get("strategy", r.get("name", "unknown"))
        taxo = _get_taxonomy(sname)
        fam = _get_family(sname)
        w = regime_weights.get(taxo, 0.5)
        if instr_type == "future":
            w *= 0.5
        tod_mult = get_strategy_time_weight(sname)
        tod_weights[sname] = tod_mult
        w *= tod_mult
        if trend_dir is not None and direction != trend_dir:
            w *= COUNTER_TREND_PENALTY
        weighted_long += confidence * w if direction == "long" else 0
        weighted_short += confidence * w if direction == "short" else 0
        total_weight += w
        active_votes += 1
        all_votes.append({
            "name": sname, "direction": direction, "confidence": confidence,
            "weight": w, "family": fam, "type": "V2",
        })

    # ── V3 votes (with per-instrument-type multiplier) ──
    v3_mult = INSTR_TYPE_V3_MULTIPLIER.get(instr_type, 1.0)
    for r in v3_results:
        direction = r.get("direction", "neutral")
        confidence = r.get("confidence", 0)
        if confidence <= 0 or direction == "neutral":
            neutral_count += 1
            continue
        sname = r.get("strategy", r.get("name", "unknown"))
        taxo = _get_taxonomy(sname)
        fam = _get_family(sname)
        w = regime_weights.get(taxo, 0.5) * v3_mult
        tod_mult = get_strategy_time_weight(sname)
        tod_weights[sname] = tod_mult
        w *= tod_mult
        if trend_dir is not None and direction != trend_dir:
            w *= COUNTER_TREND_PENALTY
        weighted_long += confidence * w if direction == "long" else 0
        weighted_short += confidence * w if direction == "short" else 0
        total_weight += w
        active_votes += 1
        all_votes.append({
            "name": sname, "direction": direction, "confidence": confidence,
            "weight": w, "family": fam, "type": "V3",
        })

    # ── Family diversity computation ──
    fam_div = _compute_family_diversity(all_votes)
    meta["consensus_families"] = fam_div["families"]
    meta["consensus_family_count"] = fam_div["family_count"]
    meta["consensus_dominant_share"] = fam_div["dominant_share"]
    meta["consensus_diversity_penalty"] = fam_div["diversity_penalty"]

    # Apply diversity penalty/bonus to total_weight
    total_weight *= fam_div["diversity_penalty"]
    meta["consensus_total_weight"] = round(total_weight, 4)

    meta["consensus_active_votes"] = active_votes
    meta["consensus_neutral_votes"] = neutral_count
    meta["consensus_weighted_long"] = round(weighted_long, 4)
    meta["consensus_weighted_short"] = round(weighted_short, 4)
    meta["consensus_tod_window"] = _get_tod_window_name()
    meta["consensus_tod_weights"] = {k: round(v, 2) for k, v in tod_weights.items() if v != 1.0}

    # ── Per-strategy vote breakdown for UI detail panel ──
    meta["consensus_votes"] = []
    for v in all_votes:
        meta["consensus_votes"].append({
            "name": v["name"], "type": v["type"], "direction": v["direction"],
            "confidence": round(v["confidence"], 4), "weight": round(v["weight"], 4),
            "contribution": round(v["confidence"] * v["weight"], 4), "family": v["family"],
        })
    meta["consensus_votes"].sort(key=lambda x: -x["contribution"])

    # ── Net score: -1 (strong short) to +1 (strong long) ──
    participation = max(total_weight, MIN_PARTICIPATION_WEIGHT)
    # Only V3 neutral votes count against participation. V2 strategies were designed
    # for a different system and most return "neutral" because they can't load
    # (missing engine.core/engine.indicators modules). Including them inflates
    # the denominator and kills stock/option consensus.
    if neutral_count > 0:
        v2_neutral = sum(1 for r in v2_results if r.get("confidence", 0) <= 0 or r.get("direction", "neutral") == "neutral")
        v3_neutral = neutral_count - v2_neutral
        participation = max(participation, max(v3_neutral, 0) * 0.25)
    net = (weighted_long - weighted_short) / max(participation, 0.01) if total_weight > 0 else 0.0

    net = max(-1.0, min(1.0, net))
    meta["consensus_net_score"] = round(net, 4)

    # ── Determine direction with adaptive threshold ──
    threshold = CONSENSUS_THRESHOLD_OPTION if instr_type == "option" else CONSENSUS_THRESHOLD
    if trend_dir is not None:
        inferred = "long" if net > 0 else "short"
        if inferred != trend_dir:
            threshold = COUNTER_TREND_CONSENSUS_THRESHOLD

    if net > threshold:
        direction = "long"
    elif net < -threshold:
        direction = "short"
    else:
        direction = "neutral"

    # ── MTF alignment check ──
    mtf_direction = None
    for r in v3_results:
        if r.get("strategy") == "mtf_core" and r.get("direction") != "neutral":
            mtf_direction = r["direction"]
            break
    meta["consensus_mtf_direction"] = mtf_direction

    # ── Determine base confidence ──
    if direction == "neutral":
        base_confidence = 0.0
    else:
        base_confidence = min(abs(net) * 1.2, 0.95)
        if trend_dir is not None and direction != trend_dir:
            base_confidence = max(base_confidence, 0.75)

    # ── Conviction tier classification ──
    tier_info = _classify_conviction_tier(
        direction, base_confidence, active_votes, fam_div["family_count"],
        regime, trend_dir, mtf_direction,
    )
    meta["consensus_conviction_tier"] = tier_info["tier"]
    meta["consensus_tier_met"] = tier_info["met_all"]

    # ── Apply family diversity bonus to confidence ──
    family_bonus = FAMILY_DIVERSITY_BONUS.get(fam_div["family_count"], 1.0)
    confidence = min(base_confidence * family_bonus, 0.95)

    meta["consensus_direction"] = direction
    meta["consensus_action"] = _decide_consensus_action(
        v2_results, v3_results, direction, regime, instr_type
    )
    meta["consensus_confidence"] = round(confidence, 4)
    meta["consensus_counter_trend"] = (
        "yes" if (trend_dir is not None and direction not in ("neutral", trend_dir)) else "no"
    )
    meta["consensus_regime_boost"] = 0.15 if (direction != "neutral" and trend_dir is not None and direction == trend_dir) else 0.0
    meta["consensus_family_bonus"] = round(family_bonus, 4)
    meta["consensus_reasons"] = _build_reasons(
        direction, net, regime, active_votes, total_weight, threshold,
        weighted_long=weighted_long, weighted_short=weighted_short,
    )

    return direction, confidence, meta


def _decide_consensus_action(
    v2_results: list[dict],
    v3_results: list[dict],
    direction: str,
    regime: str,
    instr_type: str,
) -> str:
    """Decide buy vs sell for options based on strategy consensus + regime.

    Only applies to options. For stocks/futures, returns "buy" (default).

    Decision logic:
    - Count explicit action votes from V3 strategies ("buy" or "sell")
    - If strong consensus for sell → sell
    - If ranging regime → sell (collect theta)
    - If strong trend → buy (directional exposure)

    Conservative: only sell puts (cash-secured). Naked sell-calls deferred.
    """
    if instr_type not in ("option",):
        return "buy"

    buy_votes = 0.0
    sell_votes = 0.0
    for r in v3_results:
        act = r.get("action", "")
        conf = r.get("confidence", 0)
        if act == "sell":
            sell_votes += conf
        elif act == "buy":
            buy_votes += conf

    # Strategy consensus overrides regime
    if sell_votes > buy_votes * 1.5:
        return "sell"
    if buy_votes > sell_votes * 1.5:
        return "buy"

    # Regime-based default
    if regime in ("ranging",):
        return "sell"
    if regime in ("strong_uptrend", "strong_downtrend", "uptrend", "downtrend"):
        return "buy"

    return "buy"


def _build_reasons(
    direction: str, net: float, regime: str, active_votes: int, total_weight: float,
    threshold: float,
    weighted_long: float = 0.0, weighted_short: float = 0.0,
) -> list[str]:
    reasons = []
    if direction != "neutral":
        reasons.append(f"net_score={net:.2f}")
        reasons.append(f"regime={regime}")
        reasons.append(f"active_strategies={active_votes}")
        reasons.append(f"weighted_mass={total_weight:.2f}")
    else:
        reasons.append("insufficient_consensus")
        reasons.append(f"net={net:.3f}_threshold={threshold:.3f}")
        reasons.append(f"weighted_long={weighted_long:.3f}_short={weighted_short:.3f}")
        reasons.append(f"active_votes={active_votes}")
        reasons.append(f"regime={regime}")
    return reasons
