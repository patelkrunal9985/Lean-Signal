"""
Market Breadth Computation Pipeline.

Computes market breadth context using EXISTING data (no new IBKR subscriptions):
  - Futures alignment: ES, NQ, YM, RTY direction agreement
  - Small-cap participation: RTY confirming or diverging from ES
  - Tech divergence: QQQ/NQ vs SPY/ES spread
  - VIX confirmation: VX movement vs SPX/ES direction
  - Breadth thrust: rapid, synchronized moves across all index futures
  - Volume breadth: SPY/QQQ volume patterns

This is designed for index option strategies (SPX, SPY, QQQ, NDX) where
market breadth directly impacts the probability of the underlying's
directional move persisting or reversing.

Key signals:
  - BREADTH CONFIRMATION:   Futures agree with index direction → trend is real
  - BREADTH DIVERGENCE:     Futures contradict index direction → reversal likely
  - BREADTH THRUST:         All indices surge together → momentum ignition
  - SMALL-CAP DIVERGENCE:   RTY lags ES → narrow breadth, fragile move
  - VIX DIVERGENCE:         VIX rises while SPX rises → fear under the surface
"""
from __future__ import annotations

from utils.logger import get_logger

logger = get_logger("engine.market_breadth")

# ── Breadth proxy tickers ──
# Large-cap equity index futures (broad market)
BREADTH_FUTURES = ["ES=F", "NQ=F", "YM=F", "RTY=F"]
# Underlying indices/ETFs for tech vs broad comparison
BREADTH_INDICES = ["SPY", "QQQ", "SPX", "NDX"]
# VIX futures
VIX_TICKER = "VX=F"

# ── Thresholds ──
ALIGNMENT_MIN = 0.60        # 3 out of 4 futures aligned = confirmed
ALIGNMENT_STRONG = 0.80     # All 4 aligned = strong thrust
TECH_DIVERGENCE_MIN = 0.003 # 0.3% spread between QQQ/NDX and SPY/SPX = meaningful
SMALL_CAP_LAG_MIN = 0.005   # 0.5% RTY underperformance = meaningful
VIX_DIVERGENCE_MIN = 0.02   # VIX +2% while SPX + = divergence
BREADTH_HISTORY_SIZE = 10   # Rolling window for breadth trend detection

# ── Rolling history (module-level, cleared each cycle) ──
_breadth_history: list[dict] = []


def _compute_price_changes(ticker_data_map: dict, tickers: list[str]) -> dict[str, float]:
    """Extract 1-day price change (%) for each ticker."""
    changes = {}
    for ticker in tickers:
        data = ticker_data_map.get(ticker, {})
        ohlcv = data.get("ohlcv", [])
        if not ohlcv or len(ohlcv) < 2:
            continue
        prev_close = ohlcv[-2].get("close", 0) if isinstance(ohlcv[-2], dict) else 0
        curr_price = data.get("current_price", 0)
        if prev_close > 0 and curr_price > 0:
            changes[ticker] = (curr_price - prev_close) / prev_close
        elif curr_price > 0:
            # Fallback: use today's close vs open
            today = ohlcv[-1] if isinstance(ohlcv[-1], dict) else {}
            today_open = today.get("open", 0)
            if today_open > 0:
                changes[ticker] = (curr_price - today_open) / today_open
    return changes


def _compute_futures_alignment(changes: dict[str, float]) -> dict:
    """Compute how aligned the 4 main index futures are.

    Returns:
        alignment_ratio: 0-1 (1.0 = all moving same direction)
        direction: 'up' or 'down' (majority direction)
        aligned_tickers: list of tickers in majority direction
        diverging_tickers: list of tickers opposing
        spread: max change - min change (dispersion)
    """
    up_count = 0
    down_count = 0
    up_tickers = []
    down_tickers = []
    changes_list = []

    for ticker in BREADTH_FUTURES:
        chg = changes.get(ticker, 0)
        changes_list.append(chg)
        if chg > 0:
            up_count += 1
            up_tickers.append(ticker)
        elif chg < 0:
            down_count += 1
            down_tickers.append(ticker)

    total = up_count + down_count
    if total == 0:
        return {
            "alignment_ratio": 0.0,
            "direction": "neutral",
            "aligned_tickers": [],
            "diverging_tickers": [],
            "spread": 0.0,
        }

    if up_count >= down_count:
        alignment = up_count / total
        direction = "up"
        aligned = up_tickers
        diverging = down_tickers
    else:
        alignment = down_count / total
        direction = "down"
        aligned = down_tickers
        diverging = up_tickers

    spread = max(changes_list) - min(changes_list) if changes_list else 0.0

    return {
        "alignment_ratio": round(alignment, 2),
        "direction": direction,
        "aligned_tickers": aligned,
        "diverging_tickers": diverging,
        "spread": round(spread, 4),
    }


def _compute_tech_divergence(changes: dict[str, float]) -> float:
    """Compute the performance spread between tech (QQQ/NDX/NQ) and broad market (SPY/SPX/ES).

    Positive = tech outperforming (bullish for QQQ/NDX options)
    Negative = tech underperforming (bearish/rotation)
    """
    tech_changes = []
    broad_changes = []

    for t in ["QQQ", "NDX", "NQ=F"]:
        if t in changes:
            tech_changes.append(changes[t])
    for t in ["SPY", "SPX", "ES=F"]:
        if t in changes:
            broad_changes.append(changes[t])

    tech_avg = sum(tech_changes) / len(tech_changes) if tech_changes else 0.0
    broad_avg = sum(broad_changes) / len(broad_changes) if broad_changes else 0.0

    if not tech_changes or not broad_changes:
        return 0.0

    return round(tech_avg - broad_avg, 4)


def _compute_small_cap_participation(changes: dict[str, float]) -> dict:
    """Check if RTY (small caps) is participating in the move.

    Small caps lagging = narrow breadth (large caps only).
    Small caps leading = broad participation, healthier move.
    """
    rty_chg = changes.get("RTY=F", 0)
    es_chg = changes.get("ES=F", 0)
    nq_chg = changes.get("NQ=F", 0)

    # Average large-cap futures change
    large_cap_avg = 0.0
    count = 0
    for t in ["ES=F", "NQ=F", "YM=F"]:
        if t in changes:
            large_cap_avg += changes[t]
            count += 1
    if count > 0:
        large_cap_avg /= count

    rty_spread = rty_chg - large_cap_avg

    return {
        "rty_change": round(rty_chg, 4),
        "large_cap_avg": round(large_cap_avg, 4),
        "rty_spread": round(rty_spread, 4),
        "participating": rty_spread > -SMALL_CAP_LAG_MIN,
        "leading": rty_spread > SMALL_CAP_LAG_MIN,
        "lagging": rty_spread < -SMALL_CAP_LAG_MIN,
    }


def _compute_vix_confirmation(changes: dict[str, float], ticker_data_map: dict) -> dict:
    """Check if VIX is confirming or diverging from SPX/ES.

    Normal: VIX falls when market rises, rises when market falls (confirmation)
    Divergence: VIX rises while market rises (fear under surface → bearish)
                VIX falls while market falls (complacency → could be trap)
    """
    vx_data = ticker_data_map.get(VIX_TICKER, {})
    vx_chg = changes.get(VIX_TICKER, 0)

    # Get SPX/ES direction
    spx_changes = []
    for t in ["SPX", "ES=F", "SPY"]:
        if t in changes:
            spx_changes.append(changes[t])
    market_chg = sum(spx_changes) / len(spx_changes) if spx_changes else 0.0

    # Confirmation: VIX and market move inversely
    if market_chg > 0 and vx_chg < 0:
        state = "confirmed_bullish"
    elif market_chg < 0 and vx_chg > 0:
        state = "confirmed_bearish"
    elif market_chg > 0 and vx_chg > VIX_DIVERGENCE_MIN:
        state = "bearish_divergence"  # VIX rising with market = fear
    elif market_chg < 0 and vx_chg < -VIX_DIVERGENCE_MIN:
        state = "bullish_divergence"  # VIX falling with market down = complacency/capitulation?
    elif abs(market_chg) < 0.001 and abs(vx_chg) < 0.01:
        state = "neutral_low_vol"
    else:
        state = "mixed"

    return {
        "vx_change": round(vx_chg, 4),
        "market_change": round(market_chg, 4),
        "state": state,
        "vx_price": vx_data.get("current_price", 0),
    }


def _compute_breadth_thrust(
    alignment: dict, vix_confirm: dict, changes: dict[str, float]
) -> dict:
    """Detect breadth thrust: sudden synchronized moves across all indices.

    Breadth thrust = all 4 futures strongly aligned + VIX confirming + tech aligned.
    This is a rare but very high-conviction signal for momentum continuation.
    """
    is_thrust = (
        alignment["alignment_ratio"] >= ALIGNMENT_STRONG  # All 4 aligned
        and vix_confirm["state"] in ("confirmed_bullish", "confirmed_bearish")
        and abs(alignment["spread"]) < 0.005  # Tight dispersion = synchronized
    )

    thrust_direction = alignment["direction"] if is_thrust else "none"

    return {
        "thrust_active": is_thrust,
        "thrust_direction": thrust_direction,
        "thrust_score": alignment["alignment_ratio"] if is_thrust else 0.0,
    }


def _compute_composite_score(
    alignment: dict,
    tech_div: float,
    small_cap: dict,
    vix_confirm: dict,
    thrust: dict,
    changes: dict[str, float],
) -> dict:
    """Aggregate all breadth metrics into a composite -1.0 to +1.0 score.

    Positive = bullish breadth (broad participation, VIX confirming, etc.)
    Negative = bearish breadth
    Zero = neutral/conflicting
    """
    score = 0.0

    # ── Futures alignment (weight: 0.35) ──
    if alignment["direction"] == "up":
        score += alignment["alignment_ratio"] * 0.35
    elif alignment["direction"] == "down":
        score -= alignment["alignment_ratio"] * 0.35

    # ── Small-cap participation (weight: 0.25) ──
    if small_cap["participating"]:
        if small_cap["rty_change"] > 0:
            score += 0.25
        else:
            score -= 0.25
    elif small_cap["lagging"]:
        # Penalize: even if markets are up, narrow breadth is fragile
        # Don't shift score, but reduce confidence later
        score *= 0.85

    # ── VIX confirmation (weight: 0.25) ──
    if vix_confirm["state"] == "confirmed_bullish":
        score += 0.25
    elif vix_confirm["state"] == "confirmed_bearish":
        score -= 0.25
    elif vix_confirm["state"] == "bearish_divergence":
        score -= 0.20
    elif vix_confirm["state"] == "bullish_divergence":
        score += 0.10  # weaker signal

    # ── Breadth thrust bonus (weight: 0.15) ──
    if thrust["thrust_active"]:
        if thrust["thrust_direction"] == "up":
            score += 0.15
        else:
            score -= 0.15

    # ── Clamp ──
    score = max(-1.0, min(1.0, score))

    # ── Determine state ──
    if score >= 0.30:
        state = "bullish"
    elif score <= -0.30:
        state = "bearish"
    elif abs(score) < 0.15:
        state = "neutral"
    elif score > 0:
        state = "slightly_bullish"
    else:
        state = "slightly_bearish"

    return {
        "composite_score": round(score, 4),
        "state": state,
    }


def compute_market_breadth(ticker_data_map: dict) -> dict:
    """Compute market breadth context from existing ticker data.

    Called once per cycle after all ticker data is built.
    Returns a dict to be injected into option strategy contexts.

    Args:
        ticker_data_map: Dict of ticker → data (from runner cycle)

    Returns:
        Breadth context dict with all metrics for option strategies.
    """
    # ── 1. Extract price changes ──
    all_tickers = BREADTH_FUTURES + BREADTH_INDICES + [VIX_TICKER]
    changes = _compute_price_changes(ticker_data_map, all_tickers)

    if not changes:
        logger.debug("compute_market_breadth: no price changes available")
        return {"available": False}

    # ── 2. Futures alignment ──
    futures_alignment = _compute_futures_alignment(changes)

    # ── 3. Tech divergence ──
    tech_divergence = _compute_tech_divergence(changes)

    # ── 4. Small-cap participation ──
    small_cap = _compute_small_cap_participation(changes)

    # ── 5. VIX confirmation ──
    vix_confirm = _compute_vix_confirmation(changes, ticker_data_map)

    # ── 6. Breadth thrust ──
    thrust = _compute_breadth_thrust(futures_alignment, vix_confirm, changes)

    # ── 7. Composite score ──
    composite = _compute_composite_score(
        futures_alignment, tech_divergence, small_cap, vix_confirm, thrust, changes
    )

    # ── 8. Build context dict ──
    breadth_ctx = {
        "available": True,
        "futures_alignment": futures_alignment,
        "tech_divergence": tech_divergence,
        "small_cap": small_cap,
        "vix_confirmation": vix_confirm,
        "breadth_thrust": thrust,
        "composite": composite,
        "changes": {k: round(v, 4) for k, v in changes.items()},
    }

    # ── 9. Update rolling history ──
    global _breadth_history
    _breadth_history.append({
        "composite_score": composite["composite_score"],
        "alignment": futures_alignment["alignment_ratio"],
        "thrust": thrust["thrust_active"],
    })
    if len(_breadth_history) > BREADTH_HISTORY_SIZE:
        _breadth_history.pop(0)

    # ── 10. Add trend context ──
    breadth_ctx["breadth_trend"] = _compute_breadth_trend()
    breadth_ctx["breadth_improving"] = _is_breadth_improving()

    logger.info(
        "Market breadth: composite=%.2f state=%s alignment=%.0f%% vix=%s thrust=%s",
        composite["composite_score"], composite["state"],
        futures_alignment["alignment_ratio"] * 100,
        vix_confirm["state"],
        "yes" if thrust["thrust_active"] else "no",
    )

    return breadth_ctx


def _compute_breadth_trend() -> str:
    """Determine breadth trend from rolling history."""
    if len(_breadth_history) < 3:
        return "neutral"
    recent = [h["composite_score"] for h in _breadth_history[-5:]]
    if len(recent) >= 3:
        first_half = sum(recent[:len(recent)//2]) / max(len(recent)//2, 1)
        second_half = sum(recent[len(recent)//2:]) / max(len(recent) - len(recent)//2, 1)
        diff = second_half - first_half
        if diff > 0.10:
            return "improving"
        elif diff < -0.10:
            return "deteriorating"
    return "stable"


def _is_breadth_improving() -> bool:
    """Check if breadth has been improving over recent readings."""
    if len(_breadth_history) < 3:
        return False
    recent = [h["composite_score"] for h in _breadth_history[-3:]]
    return recent[-1] > recent[0]


def get_breadth_history() -> list[dict]:
    """Return the rolling breadth history."""
    return list(_breadth_history)


def reset_breadth_history():
    """Reset rolling breadth history (for testing)."""
    global _breadth_history
    _breadth_history = []
