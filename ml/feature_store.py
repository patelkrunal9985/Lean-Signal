"""Feature cache with SQLite backend and TTL expiry.
Features are cached per (ticker, feature_hash) with a configurable TTL.
"""
import sqlite3
import json
import time
import hashlib
import numpy as np
from pathlib import Path
from kronos.utils.config import FEATURE_CACHE_DIR


class FeatureStore:
    """Persistent feature cache with TTL."""

    def __init__(self, db_path: str | Path = None):
        if db_path is None:
            FEATURE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            db_path = FEATURE_CACHE_DIR / "feature_cache.db"
        self.db_path = str(db_path)
        self.ttl_seconds = 300
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS features (
                    cache_key TEXT PRIMARY KEY,
                    ticker TEXT,
                    feature_json TEXT,
                    created_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_features_ticker ON features(ticker)")
            conn.commit()

    def _make_key(self, ticker: str, ohlcv_hash: str) -> str:
        return f"{ticker}:{ohlcv_hash}"

    def _hash_ohlcv(self, ohlcv: list[dict]) -> str:
        recent = ohlcv[-5:] if len(ohlcv) >= 5 else ohlcv
        data = json.dumps(recent, sort_keys=True, default=str)
        return hashlib.md5(data.encode()).hexdigest()[:16]

    def get(self, ticker: str, ohlcv: list[dict]) -> np.ndarray | None:
        ohlcv_hash = self._hash_ohlcv(ohlcv)
        cache_key = self._make_key(ticker, ohlcv_hash)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT feature_json, created_at FROM features WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()

        if row is None:
            return None

        feature_json, created_at = row
        if time.time() - created_at > self.ttl_seconds:
            self._delete(cache_key)
            return None

        return np.array(json.loads(feature_json))

    def set(self, ticker: str, ohlcv: list[dict], features: np.ndarray):
        ohlcv_hash = self._hash_ohlcv(ohlcv)
        cache_key = self._make_key(ticker, ohlcv_hash)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO features (cache_key, ticker, feature_json, created_at) VALUES (?, ?, ?, ?)",
                (cache_key, ticker, json.dumps(features.tolist()), time.time()),
            )
            conn.commit()

    def _delete(self, cache_key: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM features WHERE cache_key = ?", (cache_key,))
            conn.commit()

    def clear_expired(self):
        cutoff = time.time() - self.ttl_seconds
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM features WHERE created_at < ?", (cutoff,))
            conn.commit()

    def clear_ticker(self, ticker: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM features WHERE ticker = ?", (ticker,))
            conn.commit()
