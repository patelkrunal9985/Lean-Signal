import numpy as np
from typing import Optional


class AdvancedIndicatorSet:
    """Container for all computed advanced indicators."""

    def __init__(self):
        self.smart_money = {}
        self.momentum = {}
        self.volume_profile = {}
        self.volatility = {}
        self.flow = {}
        self.signals = {}


def _to_np(ohlcv: list[dict], field: str) -> np.ndarray:
    return np.array([b.get(field, 0) for b in ohlcv], dtype=float)


def compute_all_advanced(ohlcv: list[dict]) -> AdvancedIndicatorSet:
    """Compute all advanced indicators from OHLCV data."""
    result = AdvancedIndicatorSet()

    if len(ohlcv) < 20:
        return result

    opens = _to_np(ohlcv, "open")
    highs = _to_np(ohlcv, "high")
    lows = _to_np(ohlcv, "low")
    closes = _to_np(ohlcv, "close")
    volumes = _to_np(ohlcv, "volume")

    # ── Smart Money / ICT ──
    fvg = _detect_fvg(highs, lows, closes)
    if fvg:
        result.smart_money["fvg_count"] = fvg["count"]
        result.smart_money["fvg_direction"] = fvg.get("direction", "neutral")
        result.smart_money["fvg_strength"] = fvg.get("strength", 0)
        result.signals["fvg_signal"] = fvg.get("signal", "neutral")

    msb = _detect_msb(highs, lows, closes)
    if msb:
        result.smart_money["msb_detected"] = msb["detected"]
        result.smart_money["msb_direction"] = msb.get("direction", "neutral")
        result.signals["msb_signal"] = msb.get("signal", "neutral")

    liq = _detect_liquidity(highs, lows, closes, volumes)
    result.smart_money["liquidity_sweep"] = liq.get("sweep", False)
    result.smart_money["liquidity_zone_high"] = liq.get("zone_high", 0)
    result.smart_money["liquidity_zone_low"] = liq.get("zone_low", 0)
    result.signals["liq_signal"] = liq.get("signal", "neutral")

    # ── Momentum ──
    fisher = _fisher_transform(closes, period=10)
    result.momentum["fisher"] = fisher.get("value", 0)
    result.momentum["fisher_signal"] = fisher.get("signal", "neutral")

    aroon_result = _aroon(highs, lows, period=14)
    result.momentum["aroon_up"] = aroon_result.get("up", 50)
    result.momentum["aroon_down"] = aroon_result.get("down", 50)
    result.momentum["aroon_oscillator"] = aroon_result.get("oscillator", 0)
    result.signals["aroon_signal"] = aroon_result.get("signal", "neutral")

    cmo = _chande_momentum(closes, period=14)
    result.momentum["cmo"] = cmo.get("value", 0)
    result.signals["cmo_signal"] = cmo.get("signal", "neutral")

    kst_result = _kst(closes)
    result.momentum["kst"] = kst_result.get("value", 0)
    result.momentum["kst_signal"] = kst_result.get("signal", "neutral")

    coppock = _coppock_curve(closes)
    result.momentum["coppock"] = coppock.get("value", 0)
    result.signals["coppock_signal"] = coppock.get("signal", "neutral")

    # ── Volume Profile ──
    vp = _value_area(highs, lows, closes, volumes)
    result.volume_profile["vah"] = vp.get("vah", 0)
    result.volume_profile["val"] = vp.get("val", 0)
    result.volume_profile["poc"] = vp.get("poc", 0)
    result.volume_profile["value_area_width"] = vp.get("width", 0)
    last_close = closes[-1] if len(closes) > 0 else 0
    if vp.get("vah", 0) > 0:
        result.volume_profile["close_vs_value_area"] = (
            "above" if last_close > vp["vah"]
            else "below" if last_close < vp["val"]
            else "inside"
        )
    result.signals["value_area_signal"] = vp.get("signal", "neutral")

    # ── Volatility ──
    hurst = _hurst_exponent(closes)
    result.volatility["hurst"] = hurst.get("value", 0.5)
    result.volatility["hurst_regime"] = hurst.get("regime", "random")
    result.signals["hurst_signal"] = hurst.get("signal", "neutral")

    chandelier = _chandelier_exit(highs, lows, closes, period=22, multiplier=3)
    result.volatility["chandelier_long"] = chandelier.get("long_stop", 0)
    result.volatility["chandelier_short"] = chandelier.get("short_stop", 0)
    result.volatility["chandelier_direction"] = chandelier.get("direction", "neutral")
    result.signals["chandelier_signal"] = chandelier.get("signal", "neutral")

    st = _supertrend(highs, lows, closes, period=10, multiplier=3)
    result.volatility["supertrend"] = st.get("trend", "neutral")
    result.volatility["supertrend_stop"] = st.get("stop", 0)
    result.signals["supertrend_signal"] = st.get("signal", "neutral")

    ulcer = _ulcer_index(closes, period=14)
    result.volatility["ulcer"] = ulcer.get("value", 0)
    result.volatility["ulcer_risk"] = ulcer.get("risk", "low")

    # ── Flow ──
    flow = _cumulative_delta(ohlcv)
    result.flow["cumulative_delta"] = flow.get("value", 0)
    result.flow["delta_divergence"] = flow.get("divergence", "none")
    result.signals["delta_signal"] = flow.get("signal", "neutral")

    kalman = _kalman_filter(closes)
    result.volatility["kalman"] = kalman.get("value", 0)
    result.volatility["kalman_trend"] = kalman.get("trend", "neutral")

    return result


def _sma(data: np.ndarray, period: int) -> np.ndarray:
    if len(data) < period:
        return np.full_like(data, np.nan)
    ret = np.cumsum(data, dtype=float)
    ret[period:] = ret[period:] - ret[:-period]
    ret[:period - 1] = np.nan
    ret[period - 1:] /= period
    return ret


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    if len(data) < period:
        return np.full_like(data, np.nan)
    alpha = 2 / (period + 1)
    out = np.full_like(data, np.nan)
    out[0] = data[0]
    for i in range(1, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    if len(high) < 2:
        return np.full_like(high, np.nan)
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr = np.concatenate([[tr[0]], tr])
    atr = np.full_like(high, np.nan)
    atr[0] = tr[0]
    for i in range(1, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _detect_fvg(highs: np.ndarray, lows: np.ndarray,
                closes: np.ndarray, lookback: int = 5) -> dict:
    """Detect Fair Value Gaps (ICT concept)."""
    result = {"count": 0, "direction": "neutral", "strength": 0, "signal": "neutral"}
    if len(highs) < 3:
        return result
    count = 0
    total_strength = 0.0
    for i in range(max(1, len(highs) - lookback - 2), len(highs) - 1):
        if lows[i + 1] > highs[i - 1]:
            gap = lows[i + 1] - highs[i - 1]
            avg_range = np.mean(highs[max(0, i - 5):i + 1] - lows[max(0, i - 5):i + 1])
            if avg_range > 0 and gap > avg_range * 0.3:
                count += 1
                total_strength += gap / avg_range
                result["direction"] = "bullish"
        elif highs[i + 1] < lows[i - 1]:
            gap = lows[i - 1] - highs[i + 1]
            avg_range = np.mean(highs[max(0, i - 5):i + 1] - lows[max(0, i - 5):i + 1])
            if avg_range > 0 and gap > avg_range * 0.3:
                count += 1
                total_strength += gap / avg_range
                result["direction"] = "bearish"
    result["count"] = count
    result["strength"] = round(total_strength, 4) if count > 0 else 0
    if count >= 2 and total_strength > 2:
        result["signal"] = result["direction"]
    return result


def _detect_msb(highs: np.ndarray, lows: np.ndarray,
                closes: np.ndarray, lookback: int = 8) -> dict:
    """Detect Market Structure Break (ICT)."""
    result = {"detected": False, "direction": "neutral", "signal": "neutral"}
    if len(highs) < lookback * 2:
        return result
    recent = closes[-lookback:]
    if len(recent) < 4:
        return result
    pivot_high = np.max(highs[-lookback * 2:-lookback])
    pivot_low = np.min(lows[-lookback * 2:-lookback])
    if closes[-1] > pivot_high and closes[-2] > pivot_high * 0.995:
        result["detected"] = True
        result["direction"] = "bullish"
        result["signal"] = "long"
    elif closes[-1] < pivot_low and closes[-2] < pivot_low * 1.005:
        result["detected"] = True
        result["direction"] = "bearish"
        result["signal"] = "short"
    return result


def _detect_liquidity(highs: np.ndarray, lows: np.ndarray,
                      closes: np.ndarray,
                      volumes: np.ndarray, lookback: int = 10) -> dict:
    """Detect liquidity sweeps / stop hunts."""
    result = {"sweep": False, "signal": "neutral", "zone_high": 0, "zone_low": 0}
    if len(highs) < lookback + 3:
        return result
    recent_highs = highs[-lookback:-1]
    recent_lows = lows[-lookback:-1]
    zone_high = np.max(recent_highs)
    zone_low = np.min(recent_lows)
    result["zone_high"] = round(zone_high, 2)
    result["zone_low"] = round(zone_low, 2)
    last_vol = volumes[-1] if len(volumes) > 0 else 0
    avg_vol = np.mean(volumes[-lookback * 3:-1]) if len(volumes) > lookback * 3 else 1
    vol_spike = avg_vol > 0 and last_vol > avg_vol * 1.5
    if highs[-1] > zone_high and closes[-1] < zone_high:
        result["sweep"] = True
        result["signal"] = "short"
    elif lows[-1] < zone_low and closes[-1] > zone_low:
        result["sweep"] = True
        result["signal"] = "long"
    return result


def _fisher_transform(closes: np.ndarray, period: int = 10) -> dict:
    """Fisher Transform — normalizes prices to detect reversals."""
    result = {"value": 0, "signal": "neutral"}
    if len(closes) < period + 2:
        return result
    recent = closes[-(period + 1):]
    low = np.min(recent)
    high = np.max(recent)
    if high == low:
        return result
    last = closes[-1]
    value = 0.5 * ((last - low) / (high - low) * 2 - 1) + 0.5 * 0
    fisher = 0.5 * np.log((1 + value) / max(1 - value, 0.001))
    result["value"] = round(fisher, 4)
    prev_recent = closes[-(period + 2):-1]
    prev_low = np.min(prev_recent)
    prev_high = np.max(prev_recent)
    if prev_high > prev_low:
        prev_value = 0.5 * ((closes[-2] - prev_low) / (prev_high - prev_low) * 2 - 1)
        prev_fisher = 0.5 * np.log((1 + prev_value) / max(1 - prev_value, 0.001))
        if fisher > 2 and prev_fisher <= 2:
            result["signal"] = "short"
        elif fisher < -2 and prev_fisher >= -2:
            result["signal"] = "long"
    return result


def _aroon(highs: np.ndarray, lows: np.ndarray, period: int = 14) -> dict:
    """Aroon Up/Down/Oscillator."""
    result = {"up": 50, "down": 50, "oscillator": 0, "signal": "neutral"}
    if len(highs) <= period:
        return result
    high_idx = np.argmax(highs[-period:])
    low_idx = np.argmin(lows[-period:])
    aroon_up = ((period - high_idx) / period) * 100
    aroon_down = ((period - low_idx) / period) * 100
    result["up"] = round(aroon_up, 2)
    result["down"] = round(aroon_down, 2)
    result["oscillator"] = round(aroon_up - aroon_down, 2)
    if aroon_up > 70 and aroon_down < 30:
        result["signal"] = "long"
    elif aroon_down > 70 and aroon_up < 30:
        result["signal"] = "short"
    return result


def _chande_momentum(closes: np.ndarray, period: int = 14) -> dict:
    """Chande Momentum Oscillator."""
    result = {"value": 0, "signal": "neutral"}
    if len(closes) <= period:
        return result
    diffs = np.diff(closes[-(period + 1):])
    up = np.sum(diffs[diffs > 0])
    down = np.sum(np.abs(diffs[diffs < 0]))
    total = up + down
    if total > 0:
        cmo = ((up - down) / total) * 100
        result["value"] = round(cmo, 2)
        if cmo > 50:
            result["signal"] = "long"
        elif cmo < -50:
            result["signal"] = "short"
    return result


def _kst(closes: np.ndarray) -> dict:
    """Know Sure Thing (KST) Oscillator."""
    result = {"value": 0, "signal": "neutral"}
    if len(closes) < 50:
        return result
    roc1 = (closes[-1] - closes[-11]) / max(closes[-11], 0.01) * 100 if closes[-11] != 0 else 0
    roc2 = (closes[-1] - closes[-16]) / max(closes[-16], 0.01) * 100 if closes[-16] != 0 else 0
    roc3 = (closes[-1] - closes[-21]) / max(closes[-21], 0.01) * 100 if closes[-21] != 0 else 0
    roc4 = (closes[-1] - closes[-31]) / max(closes[-31], 0.01) * 100 if closes[-31] != 0 else 0
    kst_val = roc1 + 2 * roc2 + 3 * roc3 + 4 * roc4
    result["value"] = round(kst_val, 4)
    prev_roc1 = (closes[-2] - closes[-12]) / max(closes[-12], 0.01) * 100 if len(closes) > 12 and closes[-12] != 0 else 0
    prev_roc2 = (closes[-2] - closes[-17]) / max(closes[-17], 0.01) * 100 if len(closes) > 17 and closes[-17] != 0 else 0
    prev_roc3 = (closes[-2] - closes[-22]) / max(closes[-22], 0.01) * 100 if len(closes) > 22 and closes[-22] != 0 else 0
    prev_roc4 = (closes[-2] - closes[-32]) / max(closes[-32], 0.01) * 100 if len(closes) > 32 and closes[-32] != 0 else 0
    prev_kst = prev_roc1 + 2 * prev_roc2 + 3 * prev_roc3 + 4 * prev_roc4
    signal_line = 0.1 * kst_val + 0.9 * prev_kst if not np.isnan(prev_kst) else kst_val
    if kst_val > signal_line and kst_val > 0:
        result["signal"] = "long"
    elif kst_val < signal_line and kst_val < 0:
        result["signal"] = "short"
    return result


def _coppock_curve(closes: np.ndarray) -> dict:
    """Coppock Curve — long-term momentum."""
    result = {"value": 0, "signal": "neutral"}
    if len(closes) < 15:
        return result
    roc_long = (closes[-1] - closes[-15]) / max(closes[-15], 0.01) * 100 if closes[-15] != 0 else 0
    roc_short = (closes[-1] - closes[-12]) / max(closes[-12], 0.01) * 100 if closes[-12] != 0 else 0
    coppock = roc_long + roc_short
    result["value"] = round(coppock, 4)
    if coppock > 10:
        result["signal"] = "long"
    elif coppock < -10:
        result["signal"] = "short"
    return result


def _value_area(highs: np.ndarray, lows: np.ndarray,
                closes: np.ndarray, volumes: np.ndarray,
                lookback: int = 20, value_pct: float = 0.70) -> dict:
    """Volume Profile — Value Area High/Low and Point of Control."""
    result = {"vah": 0, "val": 0, "poc": 0, "width": 0, "signal": "neutral"}
    if len(closes) < lookback:
        return result
    h = highs[-lookback:]
    l = lows[-lookback:]
    v = volumes[-lookback:]
    price_range = np.max(h) - np.min(l)
    if price_range <= 0:
        return result
    n_bins = 20
    bin_edges = np.linspace(np.min(l), np.max(h), n_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    vol_profile = np.zeros(n_bins)
    for i in range(lookback):
        for j in range(n_bins):
            if bin_edges[j] <= highs[-(lookback - i)] and bin_edges[j + 1] >= lows[-(lookback - i)]:
                overlap = min(bin_edges[j + 1], highs[-(lookback - i)]) - max(bin_edges[j], lows[-(lookback - i)])
                if overlap > 0:
                    vol_profile[j] += volumes[-(lookback - i)] * (overlap / (highs[-(lookback - i)] - lows[-(lookback - i)]))
    total_vol = np.sum(vol_profile)
    if total_vol <= 0:
        return result
    poc_idx = np.argmax(vol_profile)
    result["poc"] = round(bin_centers[poc_idx], 2)
    sorted_idx = np.argsort(vol_profile)[::-1]
    cum_vol = 0
    vah_idx, val_idx = poc_idx, poc_idx
    for idx in sorted_idx:
        cum_vol += vol_profile[idx]
        if cum_vol / total_vol <= value_pct:
            vah_idx = max(vah_idx, idx)
            val_idx = min(val_idx, idx)
    result["vah"] = round(bin_centers[vah_idx], 2)
    result["val"] = round(bin_centers[val_idx], 2)
    result["width"] = round(result["vah"] - result["val"], 2)
    last_close = closes[-1]
    if result["vah"] > 0:
        if last_close > result["vah"]:
            result["signal"] = "long"
        elif last_close < result["val"]:
            result["signal"] = "short"
    return result


def _hurst_exponent(closes: np.ndarray, max_lag: int = 20) -> dict:
    """Hurst Exponent — measures trend persistence."""
    result = {"value": 0.5, "regime": "random", "signal": "neutral"}
    if len(closes) < max_lag * 2:
        return result
    returns = np.diff(np.log(closes[-(max_lag * 3):] + 1e-10))
    if len(returns) < max_lag:
        return result
    lags = range(2, min(max_lag, len(returns) // 2))
    tau = []
    for lag in lags:
        variance = np.std(returns[::lag]) if len(returns[::lag]) > 1 else 0
        if variance > 0:
            tau.append(np.log(variance))
    if len(tau) < 2:
        return result
    poly = np.polyfit(np.log(lags[:len(tau)]), tau, 1)
    hurst = poly[0] / 2.0 + 0.5
    hurst = max(0.01, min(0.99, hurst))
    result["value"] = round(hurst, 4)
    if hurst > 0.65:
        result["regime"] = "trending"
        result["signal"] = "long" if closes[-1] > closes[-len(returns) // 2] else "short"
    elif hurst < 0.35:
        result["regime"] = "mean_reverting"
    return result


def _chandelier_exit(highs: np.ndarray, lows: np.ndarray,
                     closes: np.ndarray, period: int = 22, multiplier: float = 3) -> dict:
    """Chandelier Exit — volatility-based trailing stop."""
    result = {"long_stop": 0, "short_stop": 0, "direction": "neutral", "signal": "neutral"}
    if len(closes) < period:
        return result
    atr_val = _atr(highs, lows, closes, period)
    if np.isnan(atr_val[-1]):
        return result
    max_high = np.max(highs[-period:])
    min_low = np.min(lows[-period:])
    long_stop = max_high - atr_val[-1] * multiplier
    short_stop = min_low + atr_val[-1] * multiplier
    result["long_stop"] = round(long_stop, 2)
    result["short_stop"] = round(short_stop, 2)
    if closes[-1] > long_stop:
        result["direction"] = "long"
        result["signal"] = "long"
    elif closes[-1] < short_stop:
        result["direction"] = "short"
        result["signal"] = "short"
    return result


def _supertrend(highs: np.ndarray, lows: np.ndarray,
                closes: np.ndarray, period: int = 10, multiplier: float = 3) -> dict:
    """SuperTrend — trend-following indicator."""
    result = {"trend": "neutral", "stop": 0, "signal": "neutral"}
    if len(closes) < period + 1:
        return result
    atr_val = _atr(highs, lows, closes, period)
    if np.isnan(atr_val[-1]):
        return result
    hl2 = (highs + lows) / 2
    upper_band = hl2 + multiplier * atr_val
    lower_band = hl2 - multiplier * atr_val
    supertrend = np.full(len(closes), np.nan)
    direction = np.ones(len(closes))
    for i in range(period, len(closes)):
        if closes[i] > upper_band[i - 1]:
            direction[i] = 1
        elif closes[i] < lower_band[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
            if direction[i] == 1:
                lower_band[i] = max(lower_band[i], lower_band[i - 1])
            else:
                upper_band[i] = min(upper_band[i], upper_band[i - 1])
        supertrend[i] = lower_band[i] if direction[i] == 1 else upper_band[i]
    if not np.isnan(supertrend[-1]):
        result["stop"] = round(supertrend[-1], 2)
        result["trend"] = "up" if direction[-1] == 1 else "down"
        result["signal"] = "long" if direction[-1] == 1 else "short"
    return result


def _ulcer_index(closes: np.ndarray, period: int = 14) -> dict:
    """Ulcer Index — downside risk measure."""
    result = {"value": 0, "risk": "low"}
    if len(closes) < period:
        return result
    max_close = np.maximum.accumulate(closes[-period - 1:-1]) if period < len(closes) else closes
    pct_dd = (closes[-period - 1:-1] - max_close[:len(closes[-period - 1:-1])]) / max_close[:len(closes[-period - 1:-1])] * 100
    squared = pct_dd ** 2
    ulcer = np.sqrt(np.mean(squared)) if len(squared) > 0 else 0
    result["value"] = round(ulcer, 4)
    if ulcer < 5:
        result["risk"] = "low"
    elif ulcer < 10:
        result["risk"] = "medium"
    else:
        result["risk"] = "high"
    return result


def _cumulative_delta(ohlcv: list[dict]) -> dict:
    """Cumulative Delta — estimated buy/sell volume imbalance."""
    result = {"value": 0, "divergence": "none", "signal": "neutral"}
    if len(ohlcv) < 10:
        return result
    closes = np.array([b.get("close", 0) for b in ohlcv[-30:]])
    try:
        tick_volumes = np.array([abs(b.get("volume", 0)) for b in ohlcv[-30:]])
    except Exception:
        return result
    if len(tick_volumes) < 3:
        return result
    if len(closes) < 2:
        return result
    price_up = np.diff(closes) > 0
    delta = np.where(price_up, tick_volumes[1:], -tick_volumes[1:])
    cum_delta = np.sum(delta[-5:]) if len(delta) >= 5 else np.sum(delta)
    result["value"] = round(float(cum_delta), 2)
    price_trend = closes[-1] - closes[-min(10, len(closes))]
    delta_trend = cum_delta
    if price_trend > 0 and delta_trend < -abs(cum_delta) * 0.3:
        result["divergence"] = "bearish"
        result["signal"] = "short"
    elif price_trend < 0 and delta_trend > abs(cum_delta) * 0.3:
        result["divergence"] = "bullish"
        result["signal"] = "long"
    return result


def _kalman_filter(closes: np.ndarray, r: float = 0.01, q: float = 0.001) -> dict:
    """Kalman Filter — smooth price estimate for trend detection."""
    result = {"value": 0, "trend": "neutral"}
    if len(closes) < 10:
        return result
    n = len(closes)
    x = np.zeros(n)
    p = np.zeros(n)
    x[0] = closes[0]
    p[0] = 1.0
    for i in range(1, n):
        x_pred = x[i - 1]
        p_pred = p[i - 1] + q
        k = p_pred / (p_pred + r)
        x[i] = x_pred + k * (closes[i] - x_pred)
        p[i] = (1 - k) * p_pred
    result["value"] = round(x[-1], 2)
    result["trend"] = "up" if x[-1] > x[-5] else "down" if x[-1] < x[-5] else "neutral"
    return result
