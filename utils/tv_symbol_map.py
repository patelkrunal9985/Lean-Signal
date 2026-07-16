def to_tv_symbol(ticker: str) -> str:
    if ticker.endswith("=F"):
        return f"CME_MINI:{ticker.replace('=F', '')}1!"
    return ticker


def get_tv_timeframe(interval: str) -> str:
    return interval


def get_tv_range(period: str) -> str:
    return period
