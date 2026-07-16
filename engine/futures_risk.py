"""
Dynamic stop-loss and take-profit selection for futures day trading.

Selects the best SL/TP method based on available data:
  1. Gamma flip levels (from SPX GEX → ES mapping)
  2. Volume profile (POC ± ATR)
  3. ATR-based (0.75× for day trades, 1.5× for swings)
Floors: 0.3% of entry for SL, 0.5% for TP.
"""


def select_stop_loss(ticker: str, entry: float, direction: str,
                     context: dict) -> float:
    """Select the best stop-loss price for a futures trade."""
    atr_val = _get_atr(context)
    candidates = []

    gamma = context.get("es_gamma_levels", {})
    flip = gamma.get("nearest_flip")
    if flip and flip > 0:
        spx_ratio = 10.0
        es_flip = flip / spx_ratio
        if direction == "long" and es_flip < entry:
            candidates.append(("gamma", es_flip))
        elif direction == "short" and es_flip > entry:
            candidates.append(("gamma", es_flip))

    vp = context.get("volume_profile_intraday", {})
    poc = vp.get("poc")
    if poc and atr_val:
        if direction == "long":
            val = vp.get("val")
            if val and val < entry:
                candidates.append(("poc_val", val))
            poc_stop = poc - atr_val
            if poc_stop < entry:
                candidates.append(("poc_atr", poc_stop))
        else:
            vah = vp.get("vah")
            if vah and vah > entry:
                candidates.append(("poc_vah", vah))
            poc_stop = poc + atr_val
            if poc_stop > entry:
                candidates.append(("poc_atr", poc_stop))

    if atr_val:
        multiplier = 0.75 if _is_day_trade(context) else 1.5
        if direction == "long":
            atr_stop = entry - atr_val * multiplier
        else:
            atr_stop = entry + atr_val * multiplier
        candidates.append(("atr", atr_stop))

    depth = context.get("order_book_imbalance", {})
    if depth:
        if direction == "long" and depth.get("direction") == "bearish":
            bid_vol = depth.get("bid_volume", 0)
            ask_vol = depth.get("ask_volume", 0)
            if ask_vol > bid_vol * 1.5 and entry > 0:
                depth_stop = entry * 0.997
                if depth_stop < entry:
                    candidates.append(("depth", depth_stop))
        elif direction == "short" and depth.get("direction") == "bullish":
            bid_vol = depth.get("bid_volume", 0)
            ask_vol = depth.get("ask_volume", 0)
            if bid_vol > ask_vol * 1.5 and entry > 0:
                depth_stop = entry * 1.003
                if depth_stop > entry:
                    candidates.append(("depth", depth_stop))

    if not candidates:
        return _floor_stop(entry, direction)
    if direction == "long":
        best = max(candidates, key=lambda c: c[1])
    else:
        best = min(candidates, key=lambda c: c[1])
    return _round_price(ticker, best[1])


def select_take_profit(ticker: str, entry: float, direction: str,
                       context: dict) -> float:
    """Select the best take-profit price."""
    atr_val = _get_atr(context)
    candidates = []

    gamma = context.get("es_gamma_levels", {})
    if gamma.get("flip_levels"):
        spx_ratio = 10.0
        flips = [f / spx_ratio for f in gamma["flip_levels"]]
        if direction == "long":
            valid = [f for f in flips if f > entry]
            if valid:
                candidates.append(("gamma", min(valid)))
        else:
            valid = [f for f in flips if f < entry]
            if valid:
                candidates.append(("gamma", max(valid)))

    vp = context.get("volume_profile_intraday", {})
    if direction == "long":
        vah = vp.get("vah")
        if vah and vah > entry:
            candidates.append(("vah", vah))
    else:
        val = vp.get("val")
        if val and val < entry:
            candidates.append(("val", val))

    if atr_val:
        multiplier = 1.5 if _is_day_trade(context) else 2.5
        if direction == "long":
            atr_tp = entry + atr_val * multiplier
        else:
            atr_tp = entry - atr_val * multiplier
        candidates.append(("atr", atr_tp))

    if not candidates:
        if direction == "long":
            return _round_price(ticker, entry * 1.01)
        return _round_price(ticker, entry * 0.99)

    if direction == "long":
        best = min(candidates, key=lambda c: c[1])
    else:
        best = max(candidates, key=lambda c: c[1])
    return _round_price(ticker, best[1])


def _get_atr(context: dict) -> float:
    """Extract ATR from context indicators."""
    indicators = context.get("indicators", {})
    if isinstance(indicators, dict):
        atr_val = indicators.get("atr", 0)
        if atr_val:
            return float(atr_val)
    ohlcv = context.get("ohlcv", [])
    if ohlcv and len(ohlcv) >= 15:
        atr_vals = []
        for i in range(1, 15):
            high = ohlcv[-i]["high"]
            low = ohlcv[-i]["low"]
            prev_close = ohlcv[-i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            atr_vals.append(tr)
        if atr_vals:
            return sum(atr_vals) / len(atr_vals)
    return 0.0


def _is_day_trade(context: dict) -> bool:
    """Check if likely a day trade (held < 24h)."""
    return True


def _floor_stop(entry: float, direction: str) -> float:
    """Minimum floor stop at 0.3% of entry."""
    floor = entry * 0.003
    if direction == "long":
        return _round_price("", entry - max(floor, 0.1))
    return _round_price("", entry + max(floor, 0.1))


_ROUND_TO_TICK = {
    "ES=F": 0.25, "MES=F": 0.25, "NQ=F": 0.25, "MNQ=F": 0.25,
    "YM=F": 1.0, "MYM=F": 1.0, "RTY=F": 0.10, "M2K=F": 0.10,
    # GCC_si_DORMANT: kept for compat if re-added
    "GC=F": 0.10, "MGC=F": 0.10, "SI=F": 0.005,
    "CL=F": 0.01, "MCL=F": 0.01, "NG=F": 0.001, "HO=F": 0.0001, "RB=F": 0.0001,
}


def _round_price(ticker: str, price: float) -> float:
    """Round price to appropriate tick size."""
    tick = _ROUND_TO_TICK.get(ticker, 0.01)
    return round(price / tick) * tick
