"""
CFTC Commitment of Traders (COT) Data Fetcher.

Downloads and parses weekly COT reports from CFTC.gov.
Free, no API key required. Used by the V3 cot_sentiment strategy.
"""
import csv
import re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("engine.skills.cot_fetcher")

_TICKER_TO_CFTC = {
    # CME (deacmesf.htm): E-mini S&P / NASDAQ / Russell + MICROS + crypto
    "ES=F":  "E-MINI S&P 500",
    "MES=F": "MICRO E-MINI S&P 500",
    "NQ=F":  "NASDAQ MINI",
    "MNQ=F": "MICRO E-MINI NASDAQ-100",
    "RTY=F": "RUSSELL E-MINI",
    "M2K=F": "MICRO E-MINI RUSSELL 2000",
    # COMEX (deacmxsf.htm): precious metals
    "GC=F":  "GOLD - COMMODITY EXCHANGE",
    "MGC=F": "MICRO GOLD - COMMODITY EXCHANGE",
    # CBOT (deacbtsf.htm): DJIA (full + micro)
    "YM=F":  "DJIA",
    "MYM=F": "MICRO DJIA",
    # NYMEX (deanymesf.htm): WTI crude (full + micro), natgas, etc.
    "CL=F":  "WTI-PHYSICAL",
    "MCL=F": "WTI-PHYSICAL",   # CFTC rolls micro WTI under parent WTI-PHYSICAL until micro OI grows
    "NG=F":  "NAT GAS NYME",   # NYMEX physically-settled natgas (Code-023651)
    # CBOE Futures Exchange (deacboesf.htm): VIX futures
    "VX=F":  "VIX FUTURES",
    # Legacy/disabled tickers (kept for compliance mapping)
    "SI=F": "SILVER - COMMODITY EXCHANGE",
    "HE=F": "LEAN HOGS - CHICAGO", "LE=F": "LIVE CATTLE - CHICAGO",
    "ZB=F": "US TREASURY BONDS",
    "ZN=F": "10 YEAR TREASURY NOTES", "ZF=F": "5 YEAR TREASURY NOTES",
    "ZT=F": "2 YEAR TREASURY NOTES",
    "ZC=F": "CORN", "ZS=F": "SOYBEANS", "ZW=F": "WHEAT",
}

# Mapping each ticker to its primary CFTC report exchange. _get_urls() fetches
# the primary first, then falls back to every other exchange's URL so that
# CFTC URL reshuffles don't silently miss data.
_TICKER_PRIMARY_EXCHANGE: dict[str, str] = {
    "ES=F": "CME",   "MES=F": "CME",
    "NQ=F": "CME",   "MNQ=F": "CME",
    "RTY=F": "CME",  "M2K=F": "CME",
    "GC=F": "COMEX", "MGC=F": "COMEX",
    "YM=F": "CBOT",  "MYM=F": "CBOT",
    "CL=F": "NYMEX", "MCL=F": "NYMEX", "NG=F": "NYMEX",
    "VX=F": "CBOE",
}

_EXCHANGE_URLS = {
    "CME":   "https://www.cftc.gov/dea/futures/deacmesf.htm",   # E-minis, MICROS, BTC/ETH
    "COMEX": "https://www.cftc.gov/dea/futures/deacmxsf.htm",   # Gold/Silver
    "CBOT":  "https://www.cftc.gov/dea/futures/deacbtsf.htm",   # DJIA + grains
    "NYMEX": "https://www.cftc.gov/dea/futures/deanymesf.htm",   # WTI crude, natgas
    "CBOE":  "https://www.cftc.gov/dea/futures/deacboesf.htm",   # VIX futures
}

_CACHE_DIR = Path("data/cot")
_CACHE_TTL_HOURS = 48


def get_cot_for_ticker(ticker: str) -> dict:
    """Get COT data for a futures ticker."""
    empty = {
        "total_open_interest": 0, "commercial_long": 0, "commercial_short": 0,
        "noncommercial_long": 0, "noncommercial_short": 0, "small_trader_net_pct": 0,
    }
    ticker_upper = ticker.upper()
    commodity = _TICKER_TO_CFTC.get(ticker_upper, "")
    if not commodity:
        base = ticker_upper.replace("=F", "")
        for key, val in _TICKER_TO_CFTC.items():
            if base in key.upper().replace("=F", ""):
                commodity = val
                break
    if not commodity:
        return empty
    cache_file = _CACHE_DIR / f"cot_{_safe_fn(commodity)}.csv"
    try:
        _cache_valid = _cache_ok(cache_file)
    except Exception:
        _cache_valid = False
    if _cache_valid:
        try:
            return _read_cache(cache_file)
        except Exception:
            pass
    try:
        return _fetch_and_parse(commodity, ticker_upper, cache_file)
    except Exception as e:
        logger.debug(f"COT fetch failed for {ticker}: {e}")
        if cache_file.exists():
            try:
                return _read_cache(cache_file)
            except Exception:
                pass
        return empty


def _safe_fn(name: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name)


def _cache_ok(path: Path) -> bool:
    try:
        if not path.exists():
            return False
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        return age < timedelta(hours=_CACHE_TTL_HOURS)
    except (FileNotFoundError, PermissionError, OSError):
        return False


def _read_cache(path: Path) -> dict:
    with open(path, "r") as f:
        r = csv.DictReader(f)
        for row in r:
            return {k: (int(v) if k != "small_trader_net_pct" else float(v)) for k, v in row.items()}
    return _empty_cot()


def _get_urls(ticker: str) -> list:
    """Return CFTC URLs to try for this ticker, primary first then fallbacks.

    Falling back to every other exchange URL keeps us robust against CFTC
    reshuffling files between exchanges.
    """
    primary = _TICKER_PRIMARY_EXCHANGE.get(ticker.upper())
    urls: list = []
    if primary and primary in _EXCHANGE_URLS:
        urls.append(_EXCHANGE_URLS[primary])
    for ex_url in _EXCHANGE_URLS.values():
        if ex_url not in urls:
            urls.append(ex_url)
    return urls


def _fetch_and_parse(commodity: str, ticker: str, cache_file: Path) -> dict:
    """Download latest COT report from CFTC.gov."""
    urls = _get_urls(ticker)
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Kronos/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            result = _parse_cftc_text(html, commodity)
            if result["total_open_interest"] > 0:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _write_cache(cache_file, result)
                return result
        except Exception as e:
            logger.debug(f"COT URL {url} failed for {commodity}: {e}")
    return _empty_cot()


def _parse_cftc_text(text: str, commodity: str) -> dict:
    """Parse CFTC report text (inside <pre> block) for a commodity's COT data."""
    cu = commodity.upper()
    lines = text.split("\n")

    section_start = None
    for i, line in enumerate(lines):
        if cu in line.upper() and "CODE-" in line.upper():
            section_start = i
            break

    if section_start is None:
        for i, line in enumerate(lines):
            if cu in line.upper():
                section_start = i
                break

    if section_start is None:
        return _empty_cot()

    section = lines[section_start:section_start + 40]

    total_oi = 0
    for line in section:
        oi_match = re.search(r'OPEN INTEREST:\s*([\d,]+)', line, re.IGNORECASE)
        if oi_match:
            total_oi = int(oi_match.group(1).replace(",", ""))
            break

    found_commitments = False
    for line in section:
        stripped = line.strip()
        if stripped == "COMMITMENTS":
            found_commitments = True
            continue
        if found_commitments and re.search(r'\d', stripped):
            numbers = re.findall(r'[\d,]+', stripped)
            nums = [n.replace(",", "") for n in numbers]
            clean = []
            for n in nums:
                try:
                    clean.append(int(n))
                except ValueError:
                    continue
            if len(clean) >= 8:
                noncomm_long = clean[0]
                noncomm_short = clean[1]
                comm_long = clean[3]
                comm_short = clean[4]
                nonrep_long = clean[7]
                nonrep_short = clean[8] if len(clean) > 8 else 0
                total = comm_long + comm_short + noncomm_long + noncomm_short + nonrep_long + nonrep_short
                small_net = round((nonrep_long - nonrep_short) / total, 4) if total > 0 else 0.0
                return {
                    "total_open_interest": total_oi,
                    "commercial_long": comm_long,
                    "commercial_short": comm_short,
                    "noncommercial_long": noncomm_long,
                    "noncommercial_short": noncomm_short,
                    "small_trader_net_pct": small_net,
                }
            break

    return _empty_cot()


def _write_cache(path: Path, data: dict):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "total_open_interest", "commercial_long", "commercial_short",
            "noncommercial_long", "noncommercial_short", "small_trader_net_pct",
        ])
        w.writeheader()
        w.writerow(data)


def _empty_cot() -> dict:
    return {
        "total_open_interest": 0, "commercial_long": 0, "commercial_short": 0,
        "noncommercial_long": 0, "noncommercial_short": 0, "small_trader_net_pct": 0,
    }
