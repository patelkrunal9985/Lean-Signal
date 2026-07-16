from kronos.indicators.advanced_indicators import (
    _detect_fvg as detect_fair_value_gaps,
    _detect_msb as detect_market_structure_break,
    _detect_liquidity as detect_liquidity_zones,
)

def detect_order_blocks(highs, lows, closes, volumes, lookback=10):
    """Detect Order Blocks (ICT) — large candle zones where institutions entered."""
    import numpy as np
    result = {"ob_count": 0, "bullish_obs": [], "bearish_obs": [], "signal": "neutral"}
    if len(highs) < 5:
        return result
    for i in range(max(1, len(highs) - lookback - 1), len(highs) - 1):
        body = abs(closes[i] - opens[i]) if 'opens' in dir() and len(opens) > i else abs(closes[i] - closes[i-1])
        prev_body = abs(closes[i-1] - (closes[i-2] if i >= 2 else closes[i-1]))
        avg_vol = np.mean(volumes[max(0, i-5):i+1]) if len(volumes) > 5 else volumes[i]
        if avg_vol <= 0:
            continue
        vol_ratio = volumes[i] / avg_vol
        if body > prev_body * 1.5 and vol_ratio > 1.5:
            if closes[i] > opens[i] if 'opens' in dir() and len(opens) > i else closes[i] > closes[i-1]:
                result["bullish_obs"].append({"index": i, "low": lows[i], "high": highs[i], "strength": vol_ratio})
            else:
                result["bearish_obs"].append({"index": i, "low": lows[i], "high": highs[i], "strength": vol_ratio})
    result["ob_count"] = len(result["bullish_obs"]) + len(result["bearish_obs"])
    return result

def compute_market_structure(highs, lows, closes, volumes):
    """Aggregate all market structure indicators."""
    fvg = detect_fair_value_gaps(highs, lows, closes)
    msb = detect_market_structure_break(highs, lows, closes)
    liq = detect_liquidity_zones(highs, lows, volumes)
    ob = detect_order_blocks(highs, lows, closes, volumes)
    return {"fvg": fvg, "msb": msb, "liquidity": liq, "order_blocks": ob}
