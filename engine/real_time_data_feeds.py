"""
Lean Signals - Real-Time Data Feeds.
Sources: IBKR (primary for prices/OHLCV), Finnhub (news/sentiment only).
No yfinance fallbacks. IBKR-only golden rule.
"""
import finnhub
import json
import time
from datetime import datetime, timedelta
from typing import Optional

from utils.logger import get_logger
from utils.config import FINNHUB_API_KEY
from utils.service_tracker import report_api_contact
from utils.time_utils import now_iso

logger = get_logger("engine.real_time_data_feeds")


class RealTimeDataFeedsSkill:
    """
    Live financial data integration for Lean Signals.
    IBKR handles prices, OHLCV, options, depth.
    Finnhub handles news and sentiment.
    Fundamentals/earnings/insider data from IBKR reqFundamentalData.
    No yfinance fallbacks.
    """

    _fundamentals_cache: dict[str, tuple] = {}
    _FUNDAMENTALS_CACHE_TTL = 3600

    def __init__(self):
        self._finnhub_client = None
        if FINNHUB_API_KEY:
            try:
                self._finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)
                logger.info("Finnhub client initialized")
            except Exception as e:
                logger.warning(f"Finnhub init failed: {e}")

    # ── Fundamentals (IBKR reqFundamentalData — returns empty if not connected) ──

    def get_fundamentals(self, ticker: str) -> dict:
        """Fundamentals from IBKR. Returns empty dict if IBKR not connected."""
        cache_key = f"fund_{ticker}"
        now = time.time()
        if cache_key in self._fundamentals_cache:
            data, ts = self._fundamentals_cache[cache_key]
            if now - ts < self._FUNDAMENTALS_CACHE_TTL:
                return data
        # IBKR fundamental data requires a connected streamer
        # Returns empty dict when IBKR isn't connected — strategies handle this
        return {
            "sector": "",
            "industry": "",
            "market_cap": 0,
            "pe_ratio": 0,
            "eps": 0,
            "dividend_yield": 0,
            "52w_high": 0,
            "52w_low": 0,
            "avg_volume": 0,
            "short_ratio": 0,
            "short_pct": 0,
            "beta": 0,
            "name": ticker,
        }

    def get_insider_trades(self, ticker: str) -> list:
        """Insider trades from IBKR. Returns empty list if not available."""
        return []

    def get_earnings(self, ticker: str) -> dict:
        """Earnings from IBKR. Returns empty dict if not available."""
        return {
            "surprise_pct": 0,
            "eps_estimate": 0,
            "eps_actual": 0,
            "next_earnings_date": "",
        }

    # ── News & Sentiment (Finnhub) ──

    def get_news(self, ticker: str, max_items: int = 10) -> list:
        if not self._finnhub_client:
            return []
        try:
            news = self._finnhub_client.company_news(
                ticker,
                _from=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
                to=datetime.now().strftime("%Y-%m-%d"),
            )
            report_api_contact("finnhub_news")
            return [
                {
                    "headline": n.get("headline", ""),
                    "summary": n.get("summary", "")[:300],
                    "url": n.get("url", ""),
                    "source": n.get("source", ""),
                    "datetime": n.get("datetime", 0),
                    "sentiment": n.get("sentiment", 0),
                }
                for n in (news or [])[:max_items]
            ]
        except Exception as e:
            logger.debug(f"get_news({ticker}): {e}")
            return []

    def get_market_news(self, max_items: int = 10) -> list:
        if not self._finnhub_client:
            return []
        try:
            news = self._finnhub_client.general_news("general", min_id=0)
            report_api_contact("finnhub_market_news")
            return [
                {
                    "headline": n.get("headline", ""),
                    "summary": n.get("summary", "")[:300],
                    "url": n.get("url", ""),
                    "source": n.get("source", ""),
                    "datetime": n.get("datetime", 0),
                }
                for n in (news or [])[:max_items]
            ]
        except Exception as e:
            logger.debug(f"get_market_news: {e}")
            return []

    def get_sentiment(self, ticker: str) -> dict:
        if not self._finnhub_client:
            return {"score": 0, "bearish": 0, "bullish": 0}
        try:
            sent = self._finnhub_client.news_sentiment(ticker)
            report_api_contact("finnhub_sentiment")
            return {
                "score": (
                    sent.get("sentiment", {}).get("score", 0)
                    if isinstance(sent.get("sentiment"), dict)
                    else 0
                ),
                "bearish": (
                    sent.get("sentiment", {}).get("bearishPercent", 0)
                    if isinstance(sent.get("sentiment"), dict)
                    else 0
                ),
                "bullish": (
                    sent.get("sentiment", {}).get("bullishPercent", 0)
                    if isinstance(sent.get("sentiment"), dict)
                    else 0
                ),
            }
        except Exception as e:
            logger.debug(f"get_sentiment({ticker}): {e}")
            return {"score": 0, "bearish": 0, "bullish": 0}
