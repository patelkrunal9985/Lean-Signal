"""
USA Country Config — Market hours, tickers, and broker settings for US equity markets.
"""
from datetime import time

from kronos.countries.registry import get_country_config

COUNTRY_CODE = "usa"
COUNTRY_CONFIG = get_country_config(COUNTRY_CODE)

# ── Market hours (US Eastern Time) ──────────────────────────────
MARKET_OPEN = time(9, 30)       # Regular session open (ET)
MARKET_CLOSE = time(16, 0)      # Regular session close (ET)
EARLY_CLOSE_TIME = time(13, 0)  # Early close (e.g., day after Thanksgiving)

# NYSE holidays for 2026 (observed dates when market is closed)
# Format: (month, day) — checked against US Eastern Time
NYSE_HOLIDAYS_2026 = frozenset({
    (1, 1),   # New Year's Day
    (1, 19),  # Martin Luther King Jr. Day
    (2, 16),  # Presidents' Day
    (4, 3),   # Good Friday
    (5, 25),  # Memorial Day
    (6, 19),  # Juneteenth
    (7, 3),   # Independence Day (observed)
    (9, 7),   # Labor Day
    (11, 26), # Thanksgiving Day
    (12, 25), # Christmas Day
})

# Early close days (market closes at 1:00 PM ET)
EARLY_CLOSE_DATES = frozenset({
    (11, 27), # Day after Thanksgiving
    (12, 24), # Christmas Eve
})

TIMEZONE = "America/New_York"

# CME Globex futures trading hours (Central Time)
# CME uses America/Chicago for its reference clock.
# Schedule: Sun 5:00 PM CT → Fri 4:00 PM CT (continuous, no daily close)
CME_FUTURES_OPEN_SUNDAY = time(17, 0)    # 5:00 PM CT Sunday open
CME_FUTURES_CLOSE_FRIDAY = time(16, 0)   # 4:00 PM CT Friday close
FUTURES_TIMEZONE = "America/Chicago"

# Default tickers for US markets
DEFAULT_TICKERS = COUNTRY_CONFIG["default_tickers"]
