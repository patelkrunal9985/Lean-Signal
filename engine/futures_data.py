"""
Futures-specific data computations: intraday VWAP, cumulative delta,
volume profile (POC/VAH/VAL), and Globex session range.
All IBKR-only — returns empty data on disconnect.
"""


def compute_intraday_vwap(candles_1m: list) -> dict:
    """Compute VWAP + 1st/2nd standard deviation bands from 1-min candles."""
    if not candles_1m or len(candles_1m) < 5:
        return {}
    cum_pv = 0.0
    cum_vol = 0
    for c in candles_1m:
        typ_price = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = c.get("volume", 0)
        cum_pv += typ_price * vol
        cum_vol += vol
    if cum_vol == 0:
        return {}
    vwap = cum_pv / cum_vol
    sq_diff_sum = 0.0
    for c in candles_1m:
        typ_price = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = c.get("volume", 0)
        sq_diff_sum += vol * (typ_price - vwap) ** 2
    std = (sq_diff_sum / cum_vol) ** 0.5 if cum_vol > 0 else 0.0
    last_close = candles_1m[-1]["close"]
    return {
        "vwap": round(vwap, 2),
        "std": round(std, 2),
        "band1_upper": round(vwap + std, 2),
        "band1_lower": round(vwap - std, 2),
        "band2_upper": round(vwap + 2 * std, 2),
        "band2_lower": round(vwap - 2 * std, 2),
        "price_vs_vwap": round((last_close - vwap) / vwap, 6) if vwap else 0,
        "close_above_vwap": last_close > vwap,
    }


def compute_cumulative_delta(candles_1m: list,
                             depth_snapshot: dict = None) -> dict:
    """Compute cumulative buy/sell delta from 1-min candle bodies + optional
    order book imbalance for direction confirmation."""
    if not candles_1m or len(candles_1m) < 5:
        return {}
    deltas = []
    for c in candles_1m:
        body_high = max(c["open"], c["close"])
        body_low = min(c["open"], c["close"])
        body_range = body_high - body_low
        if body_range == 0:
            delta = 0
        else:
            buy_frac = (c["close"] - c["low"]) / (c["high"] - c["low"] + 1e-10)
            delta = int((buy_frac * 2 - 1) * c.get("volume", 0))
        deltas.append(delta)
    cum_delta = sum(deltas)
    avg_delta = cum_delta / max(len(deltas), 1)
    last_10 = sum(deltas[-10:]) if len(deltas) >= 10 else cum_delta
    price_change = candles_1m[-1]["close"] - candles_1m[0]["open"]
    bullish_div = (price_change < 0 and last_10 > avg_delta * 1.5)
    bearish_div = (price_change > 0 and last_10 < avg_delta * 0.5)
    result = {
        "cumulative_delta": cum_delta,
        "avg_delta_per_bar": round(avg_delta, 1),
        "last_10_delta": last_10,
        "price_change": round(price_change, 2),
        "bullish_divergence": bullish_div,
        "bearish_divergence": bearish_div,
        "raw_bars": deltas,  # WARNING FIX: per-bar cumulative delta list. DeltaDivergence
                             # strategy reads this for accurate 5-bar delta_change calc
                             # instead of falling back to scalar last_10_delta.
    }
    if depth_snapshot:
        bids = depth_snapshot.get("bids", [])
        asks = depth_snapshot.get("asks", [])
        bid_vol = sum(s for _, s in bids[:5])
        ask_vol = sum(s for _, s in asks[:5])
        total = bid_vol + ask_vol
        if total > 0:
            imbalance = (bid_vol - ask_vol) / total
            result["depth_imbalance"] = round(imbalance, 4)
            result["depth_direction"] = ("buying" if imbalance > 0.05
                                         else "selling" if imbalance < -0.05
                                         else "neutral")
    return result


def compute_volume_profile(candles_1m: list, num_bins: int = 25) -> dict:
    """Compute intraday volume profile: POC, VAH, VAL from 1-min candles."""
    if not candles_1m or len(candles_1m) < 5:
        return {}
    low = min(c["low"] for c in candles_1m)
    high = max(c["high"] for c in candles_1m)
    if high <= low:
        return {}
    bin_size = (high - low) / num_bins
    bins = {i: 0 for i in range(num_bins)}
    for c in candles_1m:
        avg = (c["high"] + c["low"]) / 2.0
        idx = min(int((avg - low) / bin_size), num_bins - 1)
        bins[idx] += c.get("volume", 0)
    if not bins or max(bins.values()) == 0:
        return {}
    poc_idx = max(bins, key=lambda k: bins[k])
    poc = low + (poc_idx + 0.5) * bin_size
    total_vol = sum(bins.values())
    vol_target = total_vol * 0.70
    sorted_idxs = sorted(bins.keys())
    poc_pos = sorted_idxs.index(poc_idx)
    cum_vol = bins[poc_idx]
    left = poc_pos - 1
    right = poc_pos + 1
    included = {poc_idx}
    while cum_vol < vol_target and (left >= 0 or right < len(sorted_idxs)):
        next_left = bins.get(sorted_idxs[left], 0) if left >= 0 else 0
        next_right = bins.get(sorted_idxs[right], 0) if right < len(sorted_idxs) else 0
        if next_left >= next_right and left >= 0:
            included.add(sorted_idxs[left])
            cum_vol += next_left
            left -= 1
        elif right < len(sorted_idxs):
            included.add(sorted_idxs[right])
            cum_vol += next_right
            right += 1
        else:
            break
    val_idx = min(included)
    vah_idx = max(included)
    val = low + val_idx * bin_size
    vah = low + (vah_idx + 1) * bin_size
    last_close = candles_1m[-1]["close"]
    return {
        "poc": round(poc, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "value_area_width": round(vah - val, 2),
        "close_vs_poc": round((last_close - poc) / poc, 6) if poc else 0,
        "close_above_poc": last_close > poc,
        "close_above_vah": last_close > vah,
        "close_below_val": last_close < val,
    }


def get_globex_range(ticker: str) -> dict:
    """Get overnight Globex session high/low for a futures ticker.

    Fetches pre-market daily candles from IBKR only.
    Returns empty dict on disconnect.
    """
    try:
        from engine.skills.ibkr_data_feed import fetch_historical_bars
        from datetime import datetime as _dt
        bars = fetch_historical_bars(ticker, "1 D", "1 min")
        if not bars or len(bars) < 5:
            return {}
        rth_time_str = _RTH_OPEN_TIMES.get(ticker, "09:30")
        rth_hour, rth_minute = int(rth_time_str.split(":")[0]), int(rth_time_str.split(":")[1])
        rth_open = None
        # Try ISO datetime parsing first, then substring matching
        for i, c in enumerate(bars):
            ts = c.get("timestamp", "")
            matched = False
            try:
                ts_dt = _dt.fromisoformat(ts)
                if ts_dt.hour == rth_hour and ts_dt.minute == rth_minute and i + 30 < len(bars):
                    matched = True
            except (ValueError, TypeError):
                if rth_time_str in ts and i + 30 < len(bars):
                    matched = True
            if matched:
                rth_open = c
                break
        if rth_open is None:
            # Fallback: find first bar near RTH open time (with index guard)
            for i, c in enumerate(bars):
                ts = c.get("timestamp", "")
                try:
                    ts_dt = _dt.fromisoformat(ts)
                    if ts_dt.hour >= rth_hour and ts_dt.minute >= rth_minute and i + 30 < len(bars):
                        rth_open = c
                        break
                except (ValueError, TypeError):
                    continue
        if rth_open is None:
            mid = len(bars) // 2
            rth_open = bars[mid]
        rth_idx = bars.index(rth_open) if rth_open in bars else 0
        pre_bars = bars[:rth_idx] if rth_idx > 0 else []
        post_bars = bars[rth_idx:] if rth_idx < len(bars) else []
        globex_high = max((c["high"] for c in pre_bars), default=0)
        globex_low = min((c["low"] for c in pre_bars), default=0)
        rth_high = max((c["high"] for c in post_bars), default=0)
        rth_low = min((c["low"] for c in post_bars), default=0)
        rth_open_price = rth_open["open"] if rth_open else 0
        last_price = bars[-1]["close"] if bars else 0
        gap_pct = ((last_price - globex_high) / globex_high
                   if globex_high and last_price > globex_high
                   else ((last_price - globex_low) / globex_low
                         if globex_low and last_price < globex_low
                         else 0))
        return {
            "globex_high": round(globex_high, 2),
            "globex_low": round(globex_low, 2),
            "rth_open": round(rth_open_price, 2),
            "rth_high": round(rth_high, 2),
            "rth_low": round(rth_low, 2),
            "gap_up": last_price > globex_high if globex_high else None,
            "gap_down": last_price < globex_low if globex_low else None,
            "gap_pct": round(gap_pct, 4) if gap_pct else 0.0,
            "globex_range": round(globex_high - globex_low, 2) if globex_high and globex_low else 0,
        }
    except Exception:
        return {}


_RTH_OPEN_TIMES = {
    "ES=F": "09:30", "NQ=F": "09:30", "YM=F": "09:30", "RTY=F": "09:30",
    "MES=F": "09:30", "MNQ=F": "09:30", "MYM=F": "09:30", "M2K=F": "09:30",
    "GC=F": "08:20", "MGC=F": "08:20", "SI=F": "08:25",
    "CL=F": "09:00", "MCL=F": "09:00", "HO=F": "09:00", "RB=F": "09:00",
    "NG=F": "09:00",
    "ZC=F": "10:30", "ZS=F": "10:30", "ZW=F": "10:30",
    "HE=F": "10:30", "LE=F": "10:30",
    "GF=F": "10:30",
}
