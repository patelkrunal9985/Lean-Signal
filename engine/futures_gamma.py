"""
SPX/SPY gamma exposure → ES futures flip levels.
Maps option chain GEX data to key support/resistance levels for ES trading.
IBKR primary, RealTimeDataFeedsSkill fallback for SPY option chain.
"""


import math as _math
import time as _time
from datetime import datetime as _dt

# ── Module-level cache for SPY/SPX ratio (5-min TTL) ──
_spx_ratio_cache = {"ratio": 10.0, "ts": 0}
_SPX_RATIO_TTL = 300  # 5 minutes


def _get_spx_ratio() -> float:
    """Get SPX/SPY ratio with 5-min caching to avoid per-ticker blocking API calls."""
    now = _time.time()
    if now - _spx_ratio_cache["ts"] < _SPX_RATIO_TTL:
        return _spx_ratio_cache["ratio"]
    ratio = 10.0  # fallback: SPX ≈ 10× SPY
    try:
        from kronos.skills.ibkr_data_feed import get_live_price
        spy_price = get_live_price("SPY")
        spx_price = get_live_price("SPX")
        if spy_price and spy_price.get("price", 0) > 0 and spx_price and spx_price.get("price", 0) > 0:
            ratio = spx_price["price"] / spy_price["price"]
    except Exception:
        pass
    if ratio <= 0 or ratio > 100:
        ratio = 10.0
    _spx_ratio_cache["ratio"] = ratio
    _spx_ratio_cache["ts"] = now
    return ratio


def get_es_gamma_levels() -> dict:
    """Fetch SPY option chain via IBKR, compute gamma profile for ES mapping.

    Returns dict with flip levels (zero gamma cross points), total GEX,
    max pain price, and nearest flip to current ES price.
IBKR primary, RealTimeDataFeedsSkill fallback for SPY option chain.
    """
    prices = None
    nearest_expiry = None
    try:
        from kronos.skills.ibkr_data_feed import fetch_option_chain_ibkr
        chain = fetch_option_chain_ibkr("SPY")
        if chain:
            expirations = chain.get("expirations", [])
            if expirations:
                nearest_expiry = expirations[0]
                try:
                    from kronos.skills.ibkr_data_feed import fetch_live_option_prices
                    strikes = chain.get("strikes", [])
                    if strikes:
                        prices = fetch_live_option_prices("SPY", nearest_expiry, strikes)
                except Exception:
                    pass
        # Fallback through canonical RealTimeDataFeedsSkill.get_option_chain() (IBKR primary + yfinance OI backfill)
        if not nearest_expiry or not prices:
            from kronos.skills.real_time_data_feeds import RealTimeDataFeedsSkill
            feeds = RealTimeDataFeedsSkill()
            chain_result = feeds.get_option_chain("SPY")
            if chain_result and chain_result.get("expirations"):
                nearest_expiry = chain_result.get("selected_expiration", chain_result["expirations"][0])
                calls = chain_result.get("calls", [])
                puts = chain_result.get("puts", [])
                prices = {}
                for rec in calls:
                    stk = str(rec["strike"])
                    prices.setdefault(stk, {})
                    prices[stk]["c"] = {
                        "openInterest": rec.get("openInterest", 0) or 0,
                        "impliedVolatility": rec.get("impliedVolatility", 0) or 0,
                    }
                for rec in puts:
                    stk = str(rec["strike"])
                    prices.setdefault(stk, {})
                    prices[stk]["p"] = {
                        "openInterest": rec.get("openInterest", 0) or 0,
                        "impliedVolatility": rec.get("impliedVolatility", 0) or 0,
                    }
    except Exception:
        pass

    if not nearest_expiry or not prices:
        return {}

    spx_ratio = _get_spx_ratio()
    total_call_gex = 0.0
    total_put_gex = 0.0
    flip_levels = []
    strike_data = []
    try:
        exp_date = _dt.strptime(nearest_expiry, "%Y-%m-%d")
        dte = max((exp_date - _dt.now()).days, 1)
    except Exception:
        dte = 7
    t = dte / 365.0
    es_price = None
    try:
        from kronos.skills.ibkr_data_feed import get_live_price
        es_price = get_live_price("ES=F")
    except Exception:
        pass
    if es_price is None:
        try:
            from kronos.skills.ibkr_data_feed import fetch_historical_bars
            bars = fetch_historical_bars("ES=F", "1 D", "1 day")
            if bars:
                es_price = bars[-1]["close"]
        except Exception:
            pass
    s = es_price * spx_ratio if es_price else 500.0
    for strike_key, data in (prices or {}).items():
        strike = float(strike_key)
        if strike <= 0:
            continue
        for right_key, item in data.items():
            oi = item.get("openInterest", 0)
            if oi < 25:
                continue
            iv_raw = item.get("impliedVolatility", 0)
            sigma = (iv_raw / 100.0) if iv_raw > 5 else 0.3
            opt_type = right_key.lower()
            d1 = (_math.log(s / strike) +
                  (0.02 + sigma ** 2 / 2) * t) / (sigma * t ** 0.5) if t > 0 else 0
            gamma_val = _math.exp(-d1 ** 2 / 2) / (
                s * sigma * (2 * _math.pi * t) ** 0.5
            ) if t > 0 and s > 0 else 0
            gex = gamma_val * oi * 100 * s
            if opt_type == "c":
                total_call_gex += gex
            else:
                total_put_gex += gex
                gex = -gex
            strike_data.append({"strike": strike, "net_gex": gex if opt_type == "c" else -gex,
                                "total_gex_val": gex})
    strike_data.sort(key=lambda x: x["strike"])
    cum_gex = 0.0
    prev_cum = 0.0
    for sd in strike_data:
        prev_cum = cum_gex
        cum_gex += sd["net_gex"]
        if (prev_cum > 0 and cum_gex < 0) or (prev_cum < 0 and cum_gex > 0):
            flip_levels.append(sd["strike"])
    total_net_gex = total_call_gex + (-total_put_gex)
    max_pain_price = _compute_max_pain(strike_data)
    nearest_flip = None
    if flip_levels and es_price:
        es_to_spx = es_price * spx_ratio
        nearest_flip = min(flip_levels,
                           key=lambda f: abs(f - es_to_spx))
    return {
        "flip_levels": [round(f, 2) for f in flip_levels],
        "nearest_flip": round(nearest_flip, 2) if nearest_flip else None,
        "total_call_gex": round(total_call_gex, 0),
        "total_put_gex": round(total_put_gex, 0),
        "total_net_gex": round(total_net_gex, 0),
        "max_pain": round(max_pain_price, 2) if max_pain_price else None,
        "flip_distance_pct": round(abs(es_to_spx - nearest_flip) / es_to_spx, 4)
        if nearest_flip and es_price else None,
    }


def _compute_max_pain(strike_data: list) -> float:
    """Compute max pain (strike with minimum OI-weighted payout) from strike data."""
    if not strike_data:
        return 0.0
    strikes = sorted(set(s["strike"] for s in strike_data))
    if len(strikes) < 3:
        return 0.0
    total_oi = {}
    for s in strike_data:
        total_oi[s["strike"]] = abs(s["total_gex_val"])
    min_pain = float("inf")
    max_pain_strike = strikes[0]
    for k in strikes:
        pain = 0.0
        for k2 in strikes:
            oi = total_oi.get(k2, 0)
            if oi == 0:
                continue
            diff = abs(k - k2)
            for s in strike_data:
                if s["strike"] == k2 and diff > 0:
                    pain += s["net_gex"] * diff
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = k
    return float(max_pain_strike)
