"""
USA Market Hours — NYSE trading calendar with holiday, early-close, and CME futures support.
"""
from datetime import time, datetime, date

from .config import (
    MARKET_OPEN,
    MARKET_CLOSE,
    EARLY_CLOSE_TIME,
    NYSE_HOLIDAYS_2026,
    EARLY_CLOSE_DATES,
    TIMEZONE,
    CME_FUTURES_OPEN_SUNDAY,
    CME_FUTURES_CLOSE_FRIDAY,
    FUTURES_TIMEZONE,
)


def _now_in_tz(tz_name: str = TIMEZONE) -> datetime:
    """Get current time in the given timezone (timezone-aware)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now()


def is_weekend(d: date) -> bool:
    """Monday=0, Sunday=6."""
    return d.weekday() >= 5


def is_holiday(d: date) -> bool:
    """Check if date is a known NYSE holiday in 2026."""
    return (d.month, d.day) in NYSE_HOLIDAYS_2026


def is_early_close(d: date) -> bool:
    """Check if market closes early today."""
    return (d.month, d.day) in EARLY_CLOSE_DATES


def closing_time(d: date) -> time:
    """Get today's market close time (regular or early)."""
    return EARLY_CLOSE_TIME if is_early_close(d) else MARKET_CLOSE


def is_market_hours() -> bool:
    """Check if U.S. stock market is currently open for trading.

    Timezone-aware: converts to America/New_York regardless of where
    the server is physically located. Checks:
      - Weekends (always closed)
      - NYSE holidays (closed)
      - Regular hours: 9:30 AM - 4:00 PM ET
      - Early close days: 9:30 AM - 1:00 PM ET
    """
    now = _now_in_tz()
    today = now.date()
    current_time = time(now.hour, now.minute)

    if is_weekend(today):
        return False

    if is_holiday(today):
        return False

    close = closing_time(today)
    return MARKET_OPEN <= current_time <= close


def is_futures_market_hours() -> bool:
    """Check if CME Globex futures market is currently open.

    CME Equity Index futures (ES, NQ, etc.) trade 24/5:
      Sunday:    5:00 PM CT open → no close
      Mon–Thu:   continuous (24h)
      Friday:    closes at 4:00 PM CT
      Saturday:  closed all day

    The one weekly gap is Friday 4:00 PM CT → Sunday 5:00 PM CT.
    """
    now = _now_in_tz(FUTURES_TIMEZONE)
    today = now.date()
    current_time = time(now.hour, now.minute)
    weekday = today.weekday()  # Mon=0 … Sun=6

    # Saturday → always closed
    if weekday == 5:
        return False

    # Sunday → check if after 5 PM CT open
    if weekday == 6:
        return current_time >= CME_FUTURES_OPEN_SUNDAY

    # Monday–Thursday → always open (24h)
    if weekday <= 3:
        return True

    # Friday → open until 4 PM CT
    return weekday == 4 and current_time <= CME_FUTURES_CLOSE_FRIDAY


def market_summary() -> dict:
    """Return a human-readable market status dict with NYSE + CME awareness."""
    now_et = _now_in_tz(TIMEZONE)
    now_ct = _now_in_tz(FUTURES_TIMEZONE)
    today_et = now_et.date()
    current_time_et = time(now_et.hour, now_et.minute)

    equity_open = is_market_hours()
    futures_open = is_futures_market_hours()

    if equity_open:
        close = closing_time(today_et)
        early_label = " (Early Close 1:00 PM)" if is_early_close(today_et) else ""
        return {
            "open": True,
            "futures_open": True,
            "reason": "trading_hours",
            "label": f"Open{early_label}",
            "close_time": close.strftime("%I:%M %p ET"),
            "est_time": now_et.isoformat(),
        }

    if futures_open:
        return {
            "open": True,
            "futures_open": True,
            "reason": "futures_only",
            "label": f"Open (Futures) — CME Globex {now_ct.strftime('%I:%M %p CT')}",
            "est_time": now_et.isoformat(),
        }

    tomorrow = "Monday"
    if today_et.weekday() == 6:
        tomorrow = "Monday"
    elif today_et.weekday() == 4:
        tomorrow = "Sunday"

    if is_weekend(today_et):
        return {
            "open": False,
            "futures_open": False,
            "reason": "weekend",
            "label": "Closed (Weekend)",
            "next_open": f"{tomorrow} 9:30 AM ET",
            "est_time": now_et.isoformat(),
        }

    if is_holiday(today_et):
        return {
            "open": False,
            "futures_open": False,
            "reason": "holiday",
            "label": f"Closed ({today_et.strftime('%B %d')} — Market Holiday)",
            "next_open": "Next trading day 9:30 AM ET",
            "est_time": now_et.isoformat(),
        }

    if current_time_et < MARKET_OPEN:
        return {
            "open": False,
            "futures_open": True,
            "reason": "premarket",
            "label": "Pre-Market (Futures Trading)",
            "opens_at": "9:30 AM ET",
            "next_open": f"Today {MARKET_OPEN.strftime('%I:%M %p ET')}",
            "est_time": now_et.isoformat(),
        }

    return {
        "open": False,
        "futures_open": futures_open,
        "reason": "after_hours",
        "label": "After-Hours (Futures Trading)" if futures_open else "Closed",
        "next_open": "Next trading day 9:30 AM ET",
        "est_time": now_et.isoformat(),
    }
