import math
import uuid
import re
from typing import Optional


def uuid_str() -> str:
    return str(uuid.uuid4())


_TICKER_SAFE_RE = re.compile(r'^[A-Z0-9.\-=^_]{1,50}$')


def sanitize_ticker(ticker: str) -> str:
    t = str(ticker).strip().upper()
    if not _TICKER_SAFE_RE.match(t) or '..' in t or '/' in t or '\\' in t:
        raise ValueError(f"Unsafe ticker: {ticker!r}")
    return t


def calculate_sma(data: list[float], period: int) -> list[Optional[float]]:
    result = [None] * len(data)
    for i in range(period - 1, len(data)):
        result[i] = sum(data[i - period + 1:i + 1]) / period
    return result


def calculate_ema(data: list[float], period: int) -> list[Optional[float]]:
    result = [None] * len(data)
    if not data:
        return result
    multiplier = 2 / (period + 1)
    result[period - 1] = sum(data[:period]) / period
    for i in range(period, len(data)):
        result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def calculate_rsi(data: list[float], period: int = 14) -> list[Optional[float]]:
    result = [None] * len(data)
    if len(data) < period + 1:
        return result
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = data[i] - data[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100 - (100 / (1 + rs))
    for i in range(period + 1, len(data)):
        diff = data[i] - data[i - 1]
        gain = max(diff, 0)
        loss = max(-diff, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100 - (100 / (1 + rs))
    return result


def calculate_macd(data: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    ema_fast = calculate_ema(data, fast)
    ema_slow = calculate_ema(data, slow)
    macd_line = [None] * len(data)
    for i in range(len(data)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]
    sig = calculate_ema([v for v in macd_line if v is not None], signal) if any(v is not None for v in macd_line) else []
    signal_line = [None] * len(data)
    sig_idx = 0
    for i in range(len(data)):
        if macd_line[i] is not None:
            if sig_idx < len(sig) and sig[sig_idx] is not None:
                signal_line[i] = sig[sig_idx]
            sig_idx += 1
    histogram = [None] * len(data)
    for i in range(len(data)):
        if macd_line[i] is not None and signal_line[i] is not None:
            histogram[i] = macd_line[i] - signal_line[i]
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def calculate_bollinger_bands(data: list[float], period: int = 20, std_dev: float = 2.0) -> dict:
    sma = calculate_sma(data, period)
    upper = [None] * len(data)
    lower = [None] * len(data)
    for i in range(period - 1, len(data)):
        window = data[i - period + 1:i + 1]
        variance = sum((x - sma[i]) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper[i] = sma[i] + std_dev * std
        lower[i] = sma[i] - std_dev * std
    return {"middle": sma, "upper": upper, "lower": lower}


def calculate_atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[Optional[float]]:
    result = [None] * len(high)
    if len(high) < period + 1:
        return result
    tr = []
    for i in range(1, len(high)):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr.append(max(hl, hc, lc))
    result[period] = sum(tr[:period]) / period
    for i in range(period + 1, len(high)):
        result[i] = (result[i - 1] * (period - 1) + tr[i - 1]) / period
    return result


def calculate_adx(high: list[float], low: list[float], close: list[float], period: int = 14) -> Optional[float]:
    if len(high) < period * 2:
        return None
    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, len(high)):
        tr_list.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
    atr_vals = []
    for i in range(len(tr_list)):
        if i < period:
            continue
        if i == period:
            atr_vals.append(sum(tr_list[:period]) / period)
        else:
            atr_vals.append((atr_vals[-1] * (period - 1) + tr_list[i]) / period)
    plus_di_vals, minus_di_vals, dx_vals = [], [], []
    for i in range(len(atr_vals)):
        idx = i + period
        pdi = (sum(plus_dm[idx - period:idx]) / period) / atr_vals[i] * 100
        mdi = (sum(minus_dm[idx - period:idx]) / period) / atr_vals[i] * 100
        plus_di_vals.append(pdi)
        minus_di_vals.append(mdi)
        dx_vals.append(abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0)
    if len(dx_vals) < period:
        return None
    return sum(dx_vals[-period:]) / period


def calculate_hv_percentile(closes: list[float], period: int = 20) -> float:
    if len(closes) < period + 1:
        return 0.5
    log_rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            log_rets.append(math.log(closes[i] / closes[i - 1]))
    if len(log_rets) < period:
        return 0.5
    hv = [0.0] * (len(log_rets) - period + 1)
    for i in range(len(hv)):
        window = log_rets[i:i + period]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        hv[i] = math.sqrt(var) * math.sqrt(252)
    hv.sort()
    if len(hv) < 2:
        return 0.5
    rank = sum(1 for v in hv if v <= hv[-1]) / len(hv)
    return rank


def calculate_rolling_zscore(data: list[float], period: int = 20) -> list[Optional[float]]:
    result = [None] * len(data)
    for i in range(period, len(data)):
        window = data[i - period:i]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(var)
        result[i] = (data[i] - mean) / std if std > 0 else 0
    return result


def calculate_value_area(high: list[float], low: list[float], close: list[float], volume: list[float], period: int = 20) -> dict:
    if not high or not low or not close or not volume:
        return {"poc": 0, "vah": 0, "val": 0}
    recent_high = max(high[-period:]) if len(high) >= period else max(high)
    recent_low = min(low[-period:]) if len(low) >= period else min(low)
    poc = (recent_high + recent_low) / 2
    return {"poc": poc, "vah": recent_high, "val": recent_low}


def calculate_order_flow_imbalance(ohlcv: list[dict], lookback: int = 5) -> dict:
    if len(ohlcv) < lookback:
        return {"ratio": 0.5}
    up_vol = sum(b.get("volume", 0) for b in ohlcv[-lookback:] if b.get("close", 0) >= b.get("open", 0))
    down_vol = sum(b.get("volume", 0) for b in ohlcv[-lookback:] if b.get("close", 0) < b.get("open", 0))
    total = up_vol + down_vol
    ratio = up_vol / total if total > 0 else 0.5
    return {"ratio": ratio, "up_volume": up_vol, "down_volume": down_vol}


def detect_liquidity_sweep(ohlcv: list[dict]) -> dict:
    if len(ohlcv) < 10:
        return {"detected": False, "confidence": 0.0}
    highs = [b.get("high", 0) for b in ohlcv[-10:]]
    lows = [b.get("low", 0) for b in ohlcv[-10:]]
    closes = [b.get("close", 0) for b in ohlcv[-10:]]
    recent_high = max(highs[:-1]) if len(highs) > 1 else highs[0]
    recent_low = min(lows[:-1]) if len(lows) > 1 else lows[0]
    last_close = closes[-1] if closes else 0
    last_high = highs[-1] if len(highs) > 1 else 0
    last_low = lows[-1] if len(lows) > 1 else 0
    sweep_up = last_high > recent_high and last_close < last_high
    sweep_down = last_low < recent_low and last_close > last_low
    detected = sweep_up or sweep_down
    confidence = 0.6 if detected else 0.0
    return {"detected": detected, "confidence": confidence, "direction": "up" if sweep_up else ("down" if sweep_down else "none")}


def calculate_anchored_vwap(ohlcv: list[dict], anchor_idx: int = 0) -> dict:
    if not ohlcv or anchor_idx >= len(ohlcv):
        return {"vwap": 0, "price_vs_vwap": 0}
    cum_pv = 0.0
    cum_vol = 0.0
    for b in ohlcv[anchor_idx:]:
        typical = (b.get("high", 0) + b.get("low", 0) + b.get("close", 0)) / 3
        vol = b.get("volume", 0)
        cum_pv += typical * vol
        cum_vol += vol
    vwap = cum_pv / cum_vol if cum_vol > 0 else 0
    last_price = ohlcv[-1].get("close", 0)
    price_vs_vwap = (last_price - vwap) / vwap if vwap > 0 else 0
    return {"vwap": vwap, "price_vs_vwap": price_vs_vwap}
