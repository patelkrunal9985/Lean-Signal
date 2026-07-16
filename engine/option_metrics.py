"""Fetch option chains and compute aggregate metrics for V3 option strategies."""

import time as _time
import math
import threading
from datetime import datetime, date
from typing import Optional

from utils.logger import get_logger

logger = get_logger("engine.option_metrics")

_chain_cache = {}
_pcr_history = {}  # ticker -> list of pc_ratio values (last 5)
_chain_snapshots = {}  # ticker -> previous chain snapshot


def _normalize_expiry(exp: str) -> str:
    return exp.replace("-", "").replace("/", "")[:8]


def _dte(expiry_str: str) -> int:
    try:
        exp_date = datetime.strptime(_normalize_expiry(expiry_str), "%Y%m%d").date()
        return (exp_date - date.today()).days
    except Exception:
        return 30


def compute_option_metrics(ticker: str, underlying_price: float, ticker_data_map: dict) -> Optional[dict]:
    """Compute all option aggregate metrics for a single underlying.

    Args:
        ticker: Stock ticker (SPY, QQQ, SPX)
        underlying_price: Current price from stock data
        ticker_data_map: Full ticker data map (for OHLCV, VIX data, etc.)

    Returns:
        Context dict with all option keys for V3 strategies, or None on failure.
    """
    from engine.ibkr_data_feed import fetch_option_chain_ibkr, fetch_live_option_prices

    chain_struct = fetch_option_chain_ibkr(ticker)
    if not chain_struct or not chain_struct.get("expirations") or not chain_struct.get("strikes"):
        logger.warning("compute_option_metrics(%s): no chain returned", ticker)
        return None

    expirations = chain_struct["expirations"]
    strikes = chain_struct["strikes"]

    # Allow 0DTE expiries — use nearest expiry even if DTE=0
    # Previously skipped DTE<1 but 0DTE gamma scalping requires expiry-day data
    nearest_exp = min(expirations, key=lambda e: _dte(e))
    dte = max(_dte(nearest_exp), 0)  # 0 for expiry day
    logger.debug("compute_option_metrics(%s): using expiry %s (DTE=%d)", ticker, nearest_exp, dte)

    # Strike window: SEQUENTIAL full-budget allocation
    # Each underlying gets the entire 83-line IBKR budget since only one
    # option chain is live at a time (subscriptions cancelled between underlyings).
    # Budget: 4 stocks + 13 futures = 17 lines, 83 remaining for ONE option chain.
    # half=20 → 41 strikes → 82 lines (1 spare)
    # Override via OPTION_STRIKE_HALF env var if needed.
    import os
    half = int(os.getenv("OPTION_STRIKE_HALF", "20"))
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - underlying_price))
    strikes = strikes[max(0, atm_idx - half):min(len(strikes), atm_idx + half + 1)]
    logger.debug("compute_option_metrics(%s): %d strikes ATM+-%d (budget: %d lines)", ticker, len(strikes), half, len(strikes) * 2)

    live_prices = fetch_live_option_prices(ticker, nearest_exp, strikes, max_strikes=len(strikes))
    if not live_prices:
        logger.warning("compute_option_metrics(%s): no live prices for expiry %s", ticker, nearest_exp)
        return None

    option_chain = {"calls": [], "puts": [], "oi_source": "ibkr", "expiry": nearest_exp}
    total_call_vol = 0
    total_put_vol = 0
    total_call_prem = 0.0
    total_put_prem = 0.0

    for strike_str, sides in live_prices.items():
        try:
            strike = float(strike_str)
        except (ValueError, TypeError):
            continue
        for right in ("c", "p"):
            data = sides.get(right, {})
            if not data:
                continue
            iv = data.get("iv", 0) or 0
            delta = data.get("delta", 0) or 0
            gamma = data.get("gamma", 0) or 0
            theta = data.get("theta", 0) or 0
            vega = data.get("vega", 0) or 0
            bid = data.get("bid", 0) or 0
            ask = data.get("ask", 0) or 0
            last = data.get("last", 0) or 0
            volume = data.get("volume", 0) or 0
            oi = data.get("openInterest", 0) or 0
            prem = (bid + ask) / 2 if bid > 0 and ask > 0 else last

            entry = {
                "strike": strike,
                "bid": bid,
                "ask": ask,
                "last": last,
                "volume": volume,
                "openInterest": oi,
                "impliedVolatility": iv,
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "vega": vega,
            }
            if right == "c":
                option_chain["calls"].append(entry)
                total_call_vol += volume
                total_call_prem += prem * oi if oi > 0 else 0
            else:
                option_chain["puts"].append(entry)
                total_put_vol += volume
                total_put_prem += prem * oi if oi > 0 else 0

    option_chain["calls"].sort(key=lambda x: x["strike"])
    option_chain["puts"].sort(key=lambda x: x["strike"])

    if not option_chain["calls"] and not option_chain["puts"]:
        return None

    pc_ratio = total_put_vol / max(total_call_vol, 1)
    call_prem = total_call_prem
    put_prem = total_put_prem

    underlying_data = ticker_data_map.get(ticker, {})
    ohlcv = underlying_data.get("ohlcv", [])
    indicators = underlying_data.get("indicators", {})

    daily_range = 0.0
    if ohlcv:
        high = max(c.get("high", 0) for c in ohlcv[-5:]) if len(ohlcv) >= 5 else 0
        low = min(c.get("low", 0) for c in ohlcv[-5:]) if len(ohlcv) >= 5 else 0
        if high > 0 and low > 0:
            daily_range = (high - low) / low

    price_change_1d = 0.0
    price_change_5d = 0.0
    if ohlcv and len(ohlcv) >= 2:
        close_now = ohlcv[-1].get("close", 0) or 0
        close_1d = ohlcv[-2].get("close", 0) or 0
        price_change_1d = (close_now - close_1d) / max(close_1d, 0.01)
        if len(ohlcv) >= 6:
            close_5d = ohlcv[-6].get("close", 0) or 0
            price_change_5d = (close_now - close_5d) / max(close_5d, 0.01)

    atm_iv = 0.0
    if option_chain["calls"] and option_chain["puts"]:
        near_calls = [c for c in option_chain["calls"] if abs(c["strike"] - underlying_price) / max(underlying_price, 1) < 0.05]
        near_puts = [p for p in option_chain["puts"] if abs(p["strike"] - underlying_price) / max(underlying_price, 1) < 0.05]
        all_iv = [c["impliedVolatility"] for c in near_calls if c["impliedVolatility"] > 0]
        all_iv += [p["impliedVolatility"] for p in near_puts if p["impliedVolatility"] > 0]
        if all_iv:
            atm_iv = sum(all_iv) / len(all_iv)

    hv_10 = 0.0
    if ohlcv and len(ohlcv) >= 11:
        closes = [c.get("close", 0) or 0 for c in ohlcv[-11:]]
        if all(closes):
            log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
            hv_10 = sum(r * r for r in log_returns) / len(log_returns)
            hv_10 = math.sqrt(hv_10 * 252)

    # ── Gamma flip level ──
    total_gamma = 0.0
    cumulative_gamma = 0.0
    gamma_flip = 0.0
    gamma_walls = []
    all_contracts = option_chain["calls"] + option_chain["puts"]
    all_contracts.sort(key=lambda x: x["strike"])
    for c in all_contracts:
        g = c.get("gamma", 0) or 0
        oi = c.get("openInterest", 0) or 0
        contrib = g * oi * underlying_price * 100
        total_gamma += contrib

    cum = 0.0
    prev_strike = 0
    for c in all_contracts:
        g = c.get("gamma", 0) or 0
        oi = c.get("openInterest", 0) or 0
        contrib = g * oi * underlying_price * 100
        cum += contrib
        if prev_strike > 0 and ((cum >= 0 and total_gamma - cum <= 0) or (cum <= 0 and total_gamma - cum >= 0)):
            gamma_flip = (prev_strike + c["strike"]) / 2
        prev_strike = c["strike"]

    # Gamma walls: strikes where gamma concentration > 20% of max
    if all_contracts:
        max_gamma = max((c.get("gamma", 0) or 0) * (c.get("openInterest", 0) or 0) for c in all_contracts)
        if max_gamma > 0:
            for c in all_contracts:
                wall_val = (c.get("gamma", 0) or 0) * (c.get("openInterest", 0) or 0)
                if wall_val >= max_gamma * 0.2:
                    gamma_walls.append({"strike": c["strike"], "strength": wall_val / max_gamma})

    # Delta positioning (aggregate dealer delta)
    delta_positioning = 0.0
    for c in all_contracts:
        d = c.get("delta", 0) or 0
        oi = c.get("openInterest", 0) or 0
        delta_positioning += d * oi

    # ── Vanna/Charm exposure (dealer hedging flow prediction) ──
    # Vanna = dDelta/dVol: dealer delta change as IV moves
    # Charm = dDelta/dTime: dealer delta change from time decay
    # On 0DTE Charm dominates final hours — dealers MUST hedge this flow
    total_vanna = 0.0; total_charm = 0.0
    call_vanna = 0.0; put_vanna = 0.0
    call_charm = 0.0; put_charm = 0.0
    t_years = max(dte / 365.0, 1.0 / (365.0 * 24.0))
    for c in all_contracts:
        stk = c.get("strike", 0) or 0
        iv_c = (c.get("impliedVolatility", atm_iv) or atm_iv)
        if iv_c <= 0 or stk <= 0 or underlying_price <= 0:
            continue
        sigma = iv_c
        try:
            d1 = (math.log(underlying_price / stk) + (0.5 * sigma**2) * t_years) / (sigma * math.sqrt(t_years))
        except (ValueError, ZeroDivisionError):
            continue
        gamma_c = c.get("gamma", 0) or 0
        oi_c = c.get("openInterest", 0) or 0
        vanna_contrib = -d1 * gamma_c * oi_c / max(sigma, 0.01)
        theta_c = c.get("theta", 0) or 0
        charm_contrib = -theta_c * oi_c / max(underlying_price, 0.01)
        total_vanna += vanna_contrib; total_charm += charm_contrib
        if c in option_chain.get("calls", []):
            call_vanna += vanna_contrib; call_charm += charm_contrib
        else:
            put_vanna += vanna_contrib; put_charm += charm_contrib
    charm_direction = "long" if total_charm > 0 else "short" if total_charm < 0 else "neutral"
    charm_magnitude = abs(total_charm) / max(underlying_price, 0.01)

    # ── Put/Call ratio history ──
    if ticker not in _pcr_history:
        _pcr_history[ticker] = []
    _pcr_history[ticker].append(pc_ratio)
    if len(_pcr_history[ticker]) > 5:
        _pcr_history[ticker] = _pcr_history[ticker][-5:]
    pc_ratio_5day_avg = sum(_pcr_history[ticker]) / max(len(_pcr_history[ticker]), 1)

    # ── Chain snapshots for flow comparison ──
    chain_current = option_chain
    chain_prev = _chain_snapshots.get(ticker, chain_current)
    _chain_snapshots[ticker] = chain_current

    # ── Option volume flow (derived from current chain vol deltas) ──
    option_volume_flow = []
    if chain_prev and chain_current:
        prev_calls = {c["strike"]: c for c in chain_prev.get("calls", [])}
        prev_puts = {p["strike"]: p for p in chain_prev.get("puts", [])}
        for c in chain_current.get("calls", []):
            prev = prev_calls.get(c["strike"], {})
            vol_delta = (c.get("volume", 0) or 0) - (prev.get("volume", 0) or 0)
            if vol_delta > 50:
                prem = (c.get("bid", 0) + c.get("ask", 0)) / 2 * vol_delta
                option_volume_flow.append({"type": "call", "strike": c["strike"], "volume": vol_delta, "premium": prem})
        for p in chain_current.get("puts", []):
            prev = prev_puts.get(p["strike"], {})
            vol_delta = (p.get("volume", 0) or 0) - (prev.get("volume", 0) or 0)
            if vol_delta > 50:
                prem = (p.get("bid", 0) + p.get("ask", 0)) / 2 * vol_delta
                option_volume_flow.append({"type": "put", "strike": p["strike"], "volume": vol_delta, "premium": prem})

    # ── VIX data ──
    vix_spot = 0.0
    vix_1m = 0.0
    vix_2m = 0.0
    for t, td in ticker_data_map.items():
        if t == "VX=F":
            vix_spot = td.get("current_price", 0) or 0
            vix_1m = vix_spot
            vix_2m = vix_spot * 1.05

    # ── ATM straddle price ──
    atm_straddle_price = 0.0
    if option_chain["calls"] and option_chain["puts"]:
        nearest_call = min(option_chain["calls"], key=lambda c: abs(c["strike"] - underlying_price))
        nearest_put = min(option_chain["puts"], key=lambda p: abs(p["strike"] - underlying_price))
        c_mid = (nearest_call.get("bid", 0) + nearest_call.get("ask", 0)) / 2
        p_mid = (nearest_put.get("bid", 0) + nearest_put.get("ask", 0)) / 2
        atm_straddle_price = c_mid + p_mid

    # ── Skew term structure ──
    skew_term_1m = 0.0
    atm_idx = len(option_chain["calls"]) // 2 if option_chain["calls"] else 0
    if option_chain["puts"] and option_chain["calls"] and atm_idx > 0 and atm_idx < len(option_chain["puts"]):
        otm_put_iv = option_chain["puts"][0].get("impliedVolatility", 0) or 0
        otm_call_iv = option_chain["calls"][-1].get("impliedVolatility", 0) or 0
        skew_term_1m = otm_put_iv - otm_call_iv

    logger.info("compute_option_metrics(%s): done — pc_ratio=%.3f, atm_iv=%.1f%%, gamma_flip=%.2f, charm=%s", ticker, pc_ratio, atm_iv * 100, gamma_flip, charm_direction)
    return {
        "ticker": f"{ticker}_OPT",
        "instrument_type": "option",
        "underlying": ticker,
        "underlying_price": underlying_price,
        "option_chain": option_chain,
        "underlying_data": underlying_data,
        "pc_ratio": pc_ratio,
        "pc_ratio_5day_avg": pc_ratio_5day_avg,
        "pc_ratio_source": "ibkr",
        "pc_oi_source": "ibkr_live",
        "price_change_1d": price_change_1d,
        "price_change_5d": price_change_5d,
        "daily_range": daily_range,
        "iv": atm_iv,
        "hv_10": hv_10,
        "dte": dte,
        "expiry": nearest_exp,
        "gamma_flip_level": gamma_flip,
        "gamma_walls": gamma_walls,
        "delta_positioning": delta_positioning,
        "chain_current": chain_current,
        "chain_prev": chain_prev,
        "option_volume_flow": option_volume_flow,
        "premium_threshold": 500000,
        "vix_spot": vix_spot,
        "vix_1m": vix_1m,
        "vix_2m": vix_2m,
        "atm_straddle_price": atm_straddle_price,
        "skew_term_1m": skew_term_1m,
        "total_vanna": round(total_vanna, 2),
        "total_charm": round(total_charm, 2),
        "call_vanna": round(call_vanna, 2),
        "put_vanna": round(put_vanna, 2),
        "call_charm": round(call_charm, 2),
        "put_charm": round(put_charm, 2),
        "charm_direction": charm_direction,
        "charm_magnitude": round(charm_magnitude, 6),
        "ohlcv": ohlcv,
        "current_price": underlying_price,
        "data_source": "ibkr",
    }
