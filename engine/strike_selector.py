"""
Strike Selector -- Recommends optimal OTM strike for 0DTE options.

Given an option chain and a directional signal, determines:
  1. Which OTM distance (ATM, 1-wide, 2-wide, 3-wide) has the best risk/reward
  2. The specific strike to trade
  3. Estimated win probability and payoff ratio

Decision drivers (in priority order):
  1. ATM gamma magnitude -- higher gamma = can stretch further OTM
  2. IV percentile -- higher IV = more premium, stay closer to ATM
  3. Daily range vs expected move -- wide range = more room to run
  4. Confidence from consensus -- higher confidence = more aggressive OTM
  5. Time of day -- power hour = ATM (gamma), morning = 1-2 OTM (directional)

SPX has 5-point strikes, SPY has 1-point, QQQ has 1-point.
"""
from __future__ import annotations
import math
from typing import Any

from utils.logger import get_logger

logger = get_logger("engine.strike_selector")

# -- Strike spacing per underlying --
STRIKE_SPACING: dict[str, float] = {
    "SPX": 5.0,
    "SPY": 1.0,
    "QQQ": 1.0,
    "NDX": 25.0,
}

# -- OTM distance presets (in number of strikes) --
OTM_PRESETS = {
    "atm":    {"strikes_otm": 0, "label": "ATM",   "est_win_rate": 0.48, "est_payoff": 1.5},
    "1_wide": {"strikes_otm": 1, "label": "1-Wide", "est_win_rate": 0.35, "est_payoff": 2.5},
    "2_wide": {"strikes_otm": 2, "label": "2-Wide", "est_win_rate": 0.22, "est_payoff": 4.0},
    "3_wide": {"strikes_otm": 3, "label": "3-Wide", "est_win_rate": 0.10, "est_payoff": 7.0},
}


def _get_atm_strike(chain: dict, underlying: float) -> float:
    """Find the strike closest to the underlying price."""
    calls = chain.get("calls", [])
    if not calls:
        return underlying
    atm = min(calls, key=lambda c: abs((c.get("strike", 0) or 0) - underlying))
    return atm.get("strike", underlying) or underlying


def _get_atm_gamma(chain: dict, atm_strike: float) -> float:
    """Get the gamma at the ATM strike."""
    calls = chain.get("calls", [])
    for c in calls:
        if abs((c.get("strike", 0) or 0) - atm_strike) < 0.01:
            return float(c.get("gamma", 0) or 0)
    return 0.0


def _get_otm_strike(underlying: float, spacing: float, strikes_otm: int,
                    direction: str) -> float:
    """Get the strike N strikes OTM in the given direction."""
    if direction == "long":
        target = underlying + strikes_otm * spacing
    else:
        target = underlying - strikes_otm * spacing
    # Round to nearest valid strike
    return round(target / spacing) * spacing


def _get_option_at_strike(chain: dict, strike: float, right: str) -> dict:
    """Get the option contract at a specific strike and right."""
    side = "calls" if right == "call" else "puts"
    for contract in chain.get(side, []):
        if abs((contract.get("strike", 0) or 0) - strike) < 0.01:
            return contract
    return {}


def recommend_strike(ticker: str, chain: dict, underlying: float,
                     direction: str, confidence: float,
                     atm_iv: float, daily_range_pct: float,
                     dte: int = 0) -> dict:
    """Recommend the optimal OTM strike for a 0DTE directional trade.

    Args:
        ticker: Underlying ticker (SPY, QQQ, SPX)
        chain: Option chain dict with 'calls' and 'puts'
        underlying: Current underlying price
        direction: 'long' (buy calls) or 'short' (buy puts)
        confidence: Consensus confidence (0-1)
        atm_iv: ATM implied volatility (decimal, e.g., 0.18)
        daily_range_pct: Today's range as decimal (e.g., 0.01 = 1%)
        dte: Days to expiration (0 for 0DTE)

    Returns:
        Dict with recommended strike, OTM distance, premium estimate,
        estimated win rate, payoff ratio, and rationale.
    """
    if underlying <= 0:
        return {"recommended_strike": 0, "otm_type": "unknown",
                "rationale": "no_underlying_price"}

    spacing = STRIKE_SPACING.get(ticker, 1.0)

    # -- Score each OTM preset --
    atm_gamma = _get_atm_gamma(chain, _get_atm_strike(chain, underlying))
    iv_pct = atm_iv * 100  # convert to percentage

    # Gamma score: higher gamma = can go further OTM (0-1)
    gamma_score = min(atm_gamma * 50, 1.0) if atm_gamma > 0 else 0.3

    # IV score: higher IV = stay closer to ATM (more premium decay risk)
    # IV 15% = score 1.0, IV 50% = score 0.2
    iv_score = max(0.2, 1.0 - (iv_pct - 15) / 50)

    # Range score: wider daily range = more room (0-1)
    range_score = min(daily_range_pct / 0.02, 1.0) if daily_range_pct > 0 else 0.3

    # Confidence score: higher confidence = more aggressive (0-1)
    conf_score = confidence

    # Composite aggressiveness (0-1): higher = further OTM
    aggressiveness = (gamma_score * 0.35 + iv_score * 0.20 +
                      range_score * 0.20 + conf_score * 0.25)

    # For 0DTE, be slightly more conservative
    if dte == 0:
        aggressiveness *= 0.85

    # Map aggressiveness to OTM preset
    if aggressiveness > 0.75:
        preset = OTM_PRESETS["3_wide"]
    elif aggressiveness > 0.55:
        preset = OTM_PRESETS["2_wide"]
    elif aggressiveness > 0.30:
        preset = OTM_PRESETS["1_wide"]
    else:
        preset = OTM_PRESETS["atm"]

    strikes_otm = preset["strikes_otm"]
    right = "call" if direction == "long" else "put"
    recommended_strike = _get_otm_strike(underlying, spacing, strikes_otm, direction)

    # -- Get premium estimate at recommended strike --
    contract = _get_option_at_strike(chain, recommended_strike, right)
    bid = float(contract.get("bid", 0) or 0)
    ask = float(contract.get("ask", 0) or 0)
    last = float(contract.get("last", 0) or 0)
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last

    # -- Adjust win rate estimate based on actual data --
    est_win_rate = preset["est_win_rate"]
    est_payoff = preset["est_payoff"]

    # Scale win rate by confidence and gamma
    est_win_rate = min(est_win_rate * (0.8 + conf_score * 0.4), 0.65)

    # Scale payoff by gamma (higher gamma = bigger moves)
    est_payoff = min(est_payoff * (0.7 + gamma_score * 0.6), 12.0)

    # -- Rationale --
    rationale_parts = []
    if atm_gamma > 0.01:
        rationale_parts.append(f"gamma={atm_gamma:.4f}(high)")
    else:
        rationale_parts.append(f"gamma={atm_gamma:.4f}")
    rationale_parts.append(f"IV={iv_pct:.0f}%")
    rationale_parts.append(f"range={daily_range_pct:.2%}")
    rationale_parts.append(f"conf={confidence:.0%}")

    logger.debug(
        "Strike selector %s: %s %s agg=%.2f -> %s strike=%.1f prem=%.2f",
        ticker, direction, preset["label"], aggressiveness,
        right, recommended_strike, mid,
    )

    return {
        "recommended_strike": round(recommended_strike, 2),
        "otm_type": preset["label"],
        "strikes_otm": strikes_otm,
        "option_type": right,
        "estimated_premium": round(mid, 2),
        "estimated_win_rate": round(est_win_rate, 2),
        "estimated_payoff_ratio": round(est_payoff, 1),
        "aggressiveness": round(aggressiveness, 2),
        "rationale": " | ".join(rationale_parts),
        "atm_gamma": round(atm_gamma, 6),
    }
