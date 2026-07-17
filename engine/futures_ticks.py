"""
Tick accumulator and micro-structure detection for futures.

Detects large prints, iceberg orders, and acceleration patterns
from IBKR tick-by-tick data. IBKR-only — returns empty on disconnect.
"""
import statistics


def accumulate_ticks(ticker: str, interval_sec: int = 120) -> list:
    """Fetch recent ticks for a futures ticker.

    Returns list of {price, size, timestamp} or empty list on disconnect.
    """
    try:
        from engine.skills.ibkr_data_feed import fetch_historical_ticks
        ticks = fetch_historical_ticks(ticker, duration_sec=interval_sec)
        return ticks or []
    except Exception:
        return []


def detect_large_prints(ticks: list, z_thresh: float = 2.0) -> list:
    """Detect unusually large trades (>2 sigma from mean size)."""
    if not ticks or len(ticks) < 5:
        return []
    sizes = [t.get("size", 0) for t in ticks if t.get("size", 0) > 0]
    if len(sizes) < 5:
        return []
    mean_sz = statistics.mean(sizes)
    std_sz = statistics.stdev(sizes) if len(sizes) > 1 else 0
    if std_sz == 0:
        return []
    threshold = mean_sz + z_thresh * std_sz
    large = [t for t in ticks if t.get("size", 0) > threshold]
    if not large:
        return []
    recent = large[-5:]
    avg_large_size = statistics.mean(t["size"] for t in recent)
    return [{
        "count": len(large),
        "largest_size": max(t["size"] for t in large),
        "avg_large_size": round(avg_large_size, 0),
        "z_threshold": round(threshold, 0),
        "large_prints": [{"price": t["price"], "size": t["size"],
                          "ts": t.get("timestamp", "")} for t in recent],
        "large_buy_prints": [t for t in recent if t.get("side", "?") == "B"],
        "large_sell_prints": [t for t in recent if t.get("side", "?") == "S"],
    }]


def detect_icebergs(ticks: list, size_tolerance: float = 0.05) -> list:
    """Detect potential iceberg orders: same-size clusters at nearby prices."""
    if not ticks or len(ticks) < 10:
        return []
    clusters = []
    seen = set()
    for i in range(len(ticks)):
        if i in seen:
            continue
        base = ticks[i]
        base_size = base.get("size", 0)
        base_price = base.get("price", 0)
        if base_size <= 0 or base_price <= 0:
            continue
        cluster = [base]
        seen.add(i)
        for j in range(i + 1, len(ticks)):
            if j in seen:
                continue
            other = ticks[j]
            other_size = other.get("size", 0)
            other_price = other.get("price", 0)
            size_diff = abs(other_size - base_size) / max(base_size, 1)
            price_diff = abs(other_price - base_price) / max(base_price, 1)
            if size_diff <= size_tolerance and price_diff <= 0.001:
                cluster.append(other)
                seen.add(j)
        if len(cluster) >= 3:
            total_sz = sum(t["size"] for t in cluster)
            clusters.append({
                "count": len(cluster),
                "total_size": total_sz,
                "avg_price": round(statistics.mean(t["price"] for t in cluster), 2),
                "avg_size": round(statistics.mean(t["size"] for t in cluster), 0),
                "side": cluster[0].get("side", "?"),
                "is_buying": cluster[-1]["price"] > cluster[0]["price"],
            })
    return clusters[:5]


def detect_acceleration(ticks: list, window: int = 10) -> dict:
    """Detect price acceleration/deceleration in tick sequence."""
    if not ticks or len(ticks) < window * 2:
        return {}
    prices = [t["price"] for t in ticks if t.get("price", 0) > 0]
    if len(prices) < window * 2:
        return {}
    first_half = prices[:window]
    second_half = prices[-window:]
    first_change = first_half[-1] - first_half[0] if first_half else 0
    second_change = second_half[-1] - second_half[0] if second_half else 0
    acceleration = second_change - first_change
    return {
        "first_half_change": round(first_change, 2),
        "second_half_change": round(second_change, 2),
        "acceleration": round(acceleration, 2),
        "accelerating_up": acceleration > 0 and second_change > 0,
        "accelerating_down": acceleration < 0 and second_change < 0,
        "decelerating_up": acceleration < 0 and first_change > 0 > second_change,
        "decelerating_down": acceleration > 0 and first_change < 0 < second_change,
    }


def analyze_tick_clusters(ticker: str) -> dict:
    """Full tick analysis for a futures ticker: large prints, icebergs,
    acceleration."""
    ticks = accumulate_ticks(ticker)
    if not ticks:
        return {}
    return {
        "tick_count": len(ticks),
        "large_prints": detect_large_prints(ticks),
        "icebergs": detect_icebergs(ticks),
        "acceleration": detect_acceleration(ticks),
    }
