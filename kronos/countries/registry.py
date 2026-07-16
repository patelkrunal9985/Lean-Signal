"""
Country Registry — Maps country codes to their config, market hours, and broker modules.
Used by the config dispatcher and dashboard country selector.
"""
from __future__ import annotations
from typing import Optional

# ── Country Registry ─────────────────────────────────────────────

COUNTRY_REGISTRY: dict[str, dict] = {
    "usa": {
        "name": "United States",
        "flag": "\U0001f1fa\U0001f1f8",  # US flag
        "timezone": "America/New_York",
        "currency_symbol": "$",
        "currency_code": "USD",
        "initial_capital": 50000.0,       # Start with $50,000 (margin account)
        "target_capital": 250000.0,      # Goal: $250,000
        "default_tickers": [
            "SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "META",
            "NVDA", "TSLA", "JPM", "V", "JNJ",
        ],
        "broker_env_prefix": "ALPACA",
        "description": "NYSE/NASDAQ — US equity markets",
    },
    "india": {
        "name": "India",
        "flag": "\U0001f1ee\U0001f1f3",  # India flag
        "timezone": "Asia/Kolkata",
        "currency_symbol": "\u20b9",     # ₹
        "currency_code": "INR",
        "initial_capital": 100000.0,     # Start with ₹1,00,000
        "target_capital": 1000000.0,     # Goal: ₹10,00,000
        "default_tickers": [
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
            "HINDUNILVR.NS", "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "BAJFINANCE.NS",
        ],
        "broker_env_prefix": "DHAN",
        "description": "NSE/BSE — Indian equity markets",
    },
}

# Default country
DEFAULT_COUNTRY = "usa"


def get_country_config(country_code: Optional[str] = None) -> dict:
    """Get the config dict for a country code. Falls back to USA if unknown."""
    if country_code and country_code in COUNTRY_REGISTRY:
        return COUNTRY_REGISTRY[country_code]
    return COUNTRY_REGISTRY[DEFAULT_COUNTRY]


def get_available_countries() -> list[dict]:
    """Return list of available country options (for UI dropdown)."""
    return [
        {
            "code": code,
            "name": cfg["name"],
            "flag": cfg["flag"],
            "currency_symbol": cfg.get("currency_symbol", "$"),
            "currency_code": cfg.get("currency_code", "USD"),
            "initial_capital": cfg.get("initial_capital", 1000),
            "target_capital": cfg.get("target_capital", 100000),
        }
        for code, cfg in COUNTRY_REGISTRY.items()
    ]
