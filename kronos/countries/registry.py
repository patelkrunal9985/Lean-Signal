"""
Country Registry — USA-only market hours and ticker config.
Signal-only system — no portfolio, capital, or broker settings.
"""
from __future__ import annotations
from typing import Optional

COUNTRY_REGISTRY: dict[str, dict] = {
    "usa": {
        "name": "United States",
        "timezone": "America/New_York",
        "currency_code": "USD",
        "default_tickers": ["SPY", "QQQ", "SPX"],
        "description": "NYSE/NASDAQ — US equity markets",
    },
}

DEFAULT_COUNTRY = "usa"


def get_country_config(country_code: Optional[str] = None) -> dict:
    if country_code and country_code in COUNTRY_REGISTRY:
        return COUNTRY_REGISTRY[country_code]
    return COUNTRY_REGISTRY[DEFAULT_COUNTRY]
