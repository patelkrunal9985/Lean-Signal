"""
Mock futures options chain generator.
Produces realistic-looking options data for CME index futures options
using yfinance for the underlying price and a simple BS-style model.
"""
from __future__ import annotations

import math
import random
from calendar import monthcalendar
from datetime import date, datetime, timedelta
from typing import Any

import yfinance as yf

from kronos.countries.usa.options_registry import get_futures_options_spec

MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _third_friday(year: int, month: int) -> date:
    """Return the 3rd Friday of a given month/year."""
    cal = monthcalendar(year, month)
    fridays = [week[4] for week in cal if week[4] != 0]
    return date(year, month, fridays[2])


def _generate_monthly_expirations(n_months: int = 8) -> list[tuple[str, str]]:
    """Return list of (iso_date, label) for the next n monthly expirations."""
    today = date.today()
    result = []
    for i in range(1, n_months + 1):
        raw = today.month + i
        y = today.year + (raw - 1) // 12
        m = ((raw - 1) % 12) + 1
        exp = _third_friday(y, m)
        if exp > today:
            result.append((exp.isoformat(), f"{MONTH_NAMES[m]} {y}"))
    return result


def _bs_premium(current: float, strike: float, days_to_exp: int,
                iv: float, is_call: bool) -> float:
    """Simplified Black-Scholes premium approximation."""
    if days_to_exp <= 0:
        return max(0, current - strike) if is_call else max(0, strike - current)

    t = days_to_exp / 365.0
    intrinsic = (current - strike) if is_call else (strike - current)

    if intrinsic <= 0:
        time_val = current * iv * math.sqrt(t) * 0.4 * math.exp(
            -abs(current - strike) / (current * iv * math.sqrt(t) + 0.001) * 2
        )
        return round(max(0.01, time_val), 2)
    else:
        otm_premium = current * iv * math.sqrt(t) * 0.2 * math.exp(
            -intrinsic / (current * iv * math.sqrt(t) + 0.001) * 1.5
        )
        return round(intrinsic + otm_premium, 2)


def _generate_mock_row(strike: float, current: float, days_to_exp: int,
                       iv: float, is_call: bool, rng: random.Random
                       ) -> dict[str, Any]:
    """Generate a single mock option row."""
    premium = _bs_premium(current, strike, days_to_exp, iv, is_call)
    spread = max(0.05, premium * 0.05)
    bid = round(premium - spread, 2)
    ask = round(premium + spread, 2)
    if bid < 0.01:
        bid = 0.0
    in_the_money = (is_call and strike < current) or (not is_call and strike > current)

    dummy_vol = rng.randint(100, 50000) if premium > 0.05 else rng.randint(0, 500)
    dummy_oi = rng.randint(500, 150000) if premium > 0.05 else rng.randint(0, 2000)

    return {
        "strike": round(strike, 2),
        "lastPrice": round(premium, 2),
        "bid": bid,
        "ask": ask,
        "volume": dummy_vol,
        "openInterest": dummy_oi,
        "impliedVolatility": round(iv * 100, 2),
        "inTheMoney": in_the_money,
        "contractSymbol": "",
    }


def get_futures_options_chain(
    underlying_code: str,
    month: str | None = None,
) -> dict:
    """
    Generate mock options chain for a futures contract.

    Parameters
    ----------
    underlying_code : str
        Futures contract code (e.g., 'ES', 'NQ', 'MES').
    month : str, optional
        Month in 'YYYY-MM' or 'YYYY-MM-DD' format. If None, uses nearest month.

    Returns
    -------
    dict with keys: ticker, underlying_price, expirations, selected_expiration,
    calls, puts — matching the stock options API shape.
    """
    spec = get_futures_options_spec(underlying_code)
    if not spec:
        return {"error": f"Unknown futures contract: {underlying_code}"}

    try:
        ticker_obj = yf.Ticker(spec["underlying_ticker"])
        hist = ticker_obj.history(period="2d")
        if hist.empty:
            return {"error": f"Could not fetch price for {spec['underlying_ticker']}"}
        current_price = round(float(hist["Close"].iloc[-1]), 2)
    except Exception as e:
        return {"error": f"Price fetch failed: {e}"}

    expirations_list = _generate_monthly_expirations(8)

    if month:
        if len(month) == 7:
            target_exp = [e for e in expirations_list if e[0].startswith(month)]
        else:
            target_exp = [e for e in expirations_list if e[0] == month]
        selected = target_exp[0] if target_exp else expirations_list[0]
    else:
        selected = expirations_list[0]

    exp_date = datetime.strptime(selected[0], "%Y-%m-%d").date()
    days_to_exp = (exp_date - date.today()).days
    if days_to_exp < 0:
        days_to_exp = 7

    interval = spec["strike_interval"]
    base_strike = round(current_price / interval) * interval
    num_strikes = 20
    strikes = [base_strike + i * interval for i in range(-num_strikes, num_strikes + 1)]

    iv = random.uniform(0.14, 0.22)
    rng = random.Random(selected[0] + underlying_code)

    calls = [
        _generate_mock_row(s, current_price, days_to_exp, iv, True, rng)
        for s in strikes
    ]
    puts = [
        _generate_mock_row(s, current_price, days_to_exp, iv, False, rng)
        for s in strikes
    ]

    return {
        "ticker": underlying_code + " Options",
        "underlying_price": current_price,
        "expirations": [e[0] for e in expirations_list],
        "expiration_labels": {e[0]: e[1] for e in expirations_list},
        "selected_expiration": selected[0],
        "selected_label": selected[1],
        "calls": calls,
        "puts": puts,
    }
