import math


def compute_correlation(prices_a: list[float], prices_b: list[float]) -> float:
    n = min(len(prices_a), len(prices_b))
    if n < 5:
        return 0.0
    a = prices_a[-n:]
    b = prices_b[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    den_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if den_a == 0 or den_b == 0:
        return 0.0
    return num / (den_a * den_b)


SECTOR_MAP = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "INTC": "XLK", "AMD": "XLK",
    "CRM": "XLK", "ADBE": "XLK", "ORCL": "XLK", "CSCO": "XLK",
    "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC",
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "NKE": "XLY",
    "JPM": "XLF", "GS": "XLF", "BAC": "XLF", "V": "XLF", "MA": "XLF",
    "JNJ": "XLV", "PFE": "XLV", "UNH": "XLV", "MRK": "XLV",
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE",
    "CAT": "XLI", "GE": "XLI", "MMM": "XLI", "BA": "XLI",
    "PG": "XLP", "KO": "XLP", "PEP": "XLP", "WMT": "XLP",
    "NEE": "XLU", "DUK": "XLU", "SO": "XLU",
    "PLD": "XLRE", "AMT": "XLRE", "EQIX": "XLRE",
    "LIN": "XLB", "APD": "XLB", "ECL": "XLB",
}


def get_sector(ticker: str) -> str:
    return SECTOR_MAP.get(ticker.upper(), "")
