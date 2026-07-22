import os
import json
from datetime import time, datetime, date
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
_env_loaded = load_dotenv(PROJECT_ROOT / ".env")

COUNTRY = "usa"

IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

FIXED_STOCKS = ["SPY", "QQQ"]  # SPX/NDX removed: indices are not tradeable, IBKR doesn't stream index data
FIXED_FUTURES = [
    "ES=F", "NQ=F", "YM=F", "RTY=F",
    "MES=F", "MNQ=F", "MYM=F", "M2K=F", "MGC=F", "MCL=F",
    "GC=F", "CL=F", "VX=F",
]

FEATURE_CACHE_DIR = PROJECT_ROOT / "data" / "feature_cache"
MODEL_DIR = PROJECT_ROOT / "data" / "models"
ML_PIPELINE_ENABLED = True



def get_currency_code():
    return "USD"


def get_country_setting(key, default=None):
    return default


DEFAULT_TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]


def is_market_hours(dt: datetime | None = None) -> bool:
    """Check if US equity markets are currently open.

    Handles:
      - Weekends (Sat/Sun → closed)
      - Weekdays before 9:30 AM ET → closed
      - Weekdays after 4:00 PM ET → closed
      - NYSE holidays → closed
      - Early close days (1:00 PM ET) → closed after 1:00 PM

    Args:
        dt: datetime to check (defaults to now NY time)

    Returns:
        True if markets are open at the given time
    """
    try:
        from datetime import timezone as tz
        if dt is None:
            import pytz
            ny_tz = pytz.timezone("America/New_York")
            from datetime import datetime as dt_now
            dt = dt_now.now(ny_tz)
        elif dt.tzinfo is None:
            import pytz
            ny_tz = pytz.timezone("America/New_York")
            dt = dt.replace(tzinfo=ny_tz)

        # Weekend check
        if dt.weekday() >= 5:
            return False

        # Holiday check
        if (dt.month, dt.day) in NYSE_HOLIDAYS_2026:
            return False

        # Time-of-day check
        market_open = MARKET_OPEN  # 9:30
        market_close = MARKET_CLOSE  # 16:00
        early_close = EARLY_CLOSE_TIME  # 13:00

        if (dt.month, dt.day) in EARLY_CLOSE_DATES:
            return market_open <= dt.time() < early_close

        return market_open <= dt.time() < market_close
    except Exception:
        return True  # Conservative: assume open on error


MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
EARLY_CLOSE_TIME = time(13, 0)

NYSE_HOLIDAYS_2026 = frozenset({
    (1, 1), (1, 19), (2, 16), (4, 3), (5, 25),
    (6, 19), (7, 3), (9, 7), (11, 26), (12, 25),
})

EARLY_CLOSE_DATES = frozenset({(11, 27), (12, 24)})

TIMEZONE = "America/New_York"
FUTURES_TIMEZONE = "America/Chicago"
CME_FUTURES_OPEN_SUNDAY = time(17, 0)
CME_FUTURES_CLOSE_FRIDAY = time(16, 0)
