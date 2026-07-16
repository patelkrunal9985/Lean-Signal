"""
Lean Signals - Real-Time Data Feeds
Sources: IBKR (primary), YFinance (fallback), Finnhub (news/sentiment only)
"""
import yfinance as yf
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
    IBKR primary → yfinance fallback for fundamentals only.
    Finnhub for news and sentiment.
    """

    _option_chain_cache: dict[str, dict] = {}
    _OPTION_CHAIN_CACHE_TTL = 60
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

    # ── Fundamentals (yfinance-only, no IBKR equivalent) ──

    def get_fundamentals(self, ticker: str) -> dict:
        cache_key = f"fund_{ticker}"
        now = time.time()
        if cache_key in self._fundamentals_cache:
            data, ts = self._fundamentals_cache[cache_key]
            if now - ts < self._FUNDAMENTALS_CACHE_TTL:
                return data
        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
            result = {
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "market_cap": info.get("marketCap", 0),
                "pe_ratio": info.get("trailingPE", 0) or info.get("forwardPE", 0),
                "eps": info.get("trailingEps", 0),
                "dividend_yield": info.get("dividendYield", 0),
                "52w_high": info.get("fiftyTwoWeekHigh", 0),
                "52w_low": info.get("fiftyTwoWeekLow", 0),
                "avg_volume": info.get("averageVolume", 0),
                "short_ratio": info.get("shortRatio", 0),
                "short_pct": info.get("shortPercentOfFloat", 0),
                "beta": info.get("beta", 0),
                "name": info.get("shortName", ticker),
            }
            self._fundamentals_cache[cache_key] = (result, now)
            return result
        except Exception as e:
            logger.debug(f"get_fundamentals({ticker}): {e}")
            return {}

    def get_insider_trades(self, ticker: str) -> list:
        try:
            tk = yf.Ticker(ticker)
            insider = tk.insider_transactions or []
            return [
                {
                    "name": t.get("insider", {}).get("name", "") if isinstance(t.get("insider"), dict) else t.get("insider", ""),
                    "shares": t.get("shares", 0),
                    "value": t.get("value", 0),
                    "date": str(t.get("startDate", "")),
                    "transaction_type": t.get("transactionDescription", ""),
                }
                for t in (insider if isinstance(insider, list) else [])
            ][:20]
        except Exception as e:
            logger.debug(f"get_insider_trades({ticker}): {e}")
            return []

    def get_earnings(self, ticker: str) -> dict:
        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
            cal = tk.calendar or {}
            return {
                "surprise_pct": info.get("earningsQuarterlyGrowth", 0),
                "eps_estimate": cal.get("earningsEstimate", {}).get("avg", 0) if isinstance(cal.get("earningsEstimate"), dict) else 0,
                "eps_actual": info.get("trailingEps", 0),
                "next_earnings_date": str(info.get("earningsTimestamp", "")),
            }
        except Exception as e:
            logger.debug(f"get_earnings({ticker}): {e}")
            return {}

    # ── News & Sentiment (Finnhub) ──

    def get_news(self, ticker: str, max_items: int = 10) -> list:
        if not self._finnhub_client:
            return []
        try:
            news = self._finnhub_client.company_news(ticker, _from=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"), to=datetime.now().strftime("%Y-%m-%d"))
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
                "score": sent.get("sentiment", {}).get("score", 0) if isinstance(sent.get("sentiment"), dict) else 0,
                "bearish": sent.get("sentiment", {}).get("bearishPercent", 0) if isinstance(sent.get("sentiment"), dict) else 0,
                "bullish": sent.get("sentiment", {}).get("bullishPercent", 0) if isinstance(sent.get("sentiment"), dict) else 0,
            }
        except Exception as e:
            logger.debug(f"get_sentiment({ticker}): {e}")
            return {"score": 0, "bearish": 0, "bullish": 0}
