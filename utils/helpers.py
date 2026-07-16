import uuid
import re


def uuid_str() -> str:
    return str(uuid.uuid4())


_TICKER_SAFE_RE = re.compile(r'^[A-Z0-9.\-=^_]{1,50}$')


def sanitize_ticker(ticker: str) -> str:
    t = str(ticker).strip().upper()
    if not _TICKER_SAFE_RE.match(t) or '..' in t or '/' in t or '\\' in t:
        raise ValueError(f"Unsafe ticker: {ticker!r}")
    return t
