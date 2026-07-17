from __future__ import annotations
import math
import statistics
from typing import Any

from utils.helpers import (
    calculate_rsi, calculate_macd, calculate_bollinger_bands,
    calculate_atr, calculate_sma, calculate_ema,
)
from utils.logger import get_logger

logger = get_logger("engine.skills.data_processing")


class DataProcessingSkill:
    def clean_data(self, ohlcv_list: list) -> list:
        cleaned = []
        for bar in ohlcv_list:
            if any(math.isnan(getattr(bar, f, bar.close)) for f in ("open", "high", "low", "close", "volume")):
                continue
            if bar.high < bar.low or bar.close < bar.low or bar.close > bar.high:
                continue
            if bar.volume <= 0:
                continue
            cleaned.append(bar)
        for i in range(1, len(cleaned)):
            prev = cleaned[i - 1]
            curr = cleaned[i]
            pct = abs(curr.close - prev.close) / prev.close if prev.close > 0 else 0
            if pct > 0.5:
                curr.close = prev.close * (1.0 + (0.5 if curr.close > prev.close else -0.5))
                curr.open = curr.close
                curr.high = max(curr.close, prev.close)
                curr.low = min(curr.close, prev.close)
        logger.info(f"Cleaned {len(ohlcv_list)} bars -> {len(cleaned)}")
        return cleaned

    def add_features(self, data: dict) -> dict:
        closes = data.get("closes", [])
        result = dict(data)
        result["returns"] = []
        result["log_returns"] = []
        result["volatility"] = 0.0
        for i in range(1, len(closes)):
            r = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] != 0 else 0.0
            result["returns"].append(r)
            result["log_returns"].append(math.log(closes[i] / closes[i - 1]) if closes[i - 1] > 0 and closes[i] > 0 else 0.0)
        if len(result["returns"]) >= 20:
            result["volatility"] = statistics.stdev(result["returns"][-20:]) * math.sqrt(252)
        highs = data.get("highs", [])
        lows = data.get("lows", [])
        volumes = data.get("volumes", [])
        if closes and highs and lows:
            result["typical_price"] = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
            result["vwap"] = (
                sum(c * v for c, v in zip(closes, volumes)) / sum(volumes)
                if sum(volumes) > 0 else closes[-1]
            )
            result["hlc_ratio"] = [
                (h - l) / c if c != 0 else 0.0 for h, l, c in zip(highs, lows, closes)
            ]
            result["price_range"] = max(closes) - min(closes)
        return result

    def normalize_data(self, data: dict) -> dict:
        result = dict(data)
        for key in ("closes", "highs", "lows", "opens", "volumes", "returns"):
            values = data.get(key, [])
            if not values or not any(isinstance(v, (int, float)) for v in values):
                continue
            numeric = [v for v in values if isinstance(v, (int, float))]
            if not numeric:
                continue
            mn = min(numeric)
            mx = max(numeric)
            if mx == mn:
                result[f"{key}_norm"] = [0.5] * len(values)
                continue
            result[f"{key}_norm"] = [(v - mn) / (mx - mn) if isinstance(v, (int, float)) else 0.5 for v in values]
        closes = data.get("closes", [])
        if closes:
            numeric_c = [v for v in closes if isinstance(v, (int, float))]
            if numeric_c:
                mn_c = statistics.mean(numeric_c)
                sd_c = statistics.stdev(numeric_c) if len(numeric_c) > 1 else 1.0
                if sd_c > 0:
                    result["closes_zscore"] = [(v - mn_c) / sd_c for v in closes]
                else:
                    result["closes_zscore"] = [0.0] * len(closes)
        return result

    def compute_all_indicators(self, ohlcv: list) -> dict:
        closes = [bar.close for bar in ohlcv]
        highs = [bar.high for bar in ohlcv]
        lows = [bar.low for bar in ohlcv]
        volumes = [bar.volume for bar in ohlcv]
        rsi = calculate_rsi(closes, 14)
        macd = calculate_macd(closes)
        bb = calculate_bollinger_bands(closes)
        sma_20 = calculate_sma(closes, 20)
        sma_50 = calculate_sma(closes, 50)
        ema_12 = calculate_ema(closes, 12)
        ema_26 = calculate_ema(closes, 26)
        atr = calculate_atr(highs, lows, closes)
        result = {
            "rsi": rsi,
            "macd": macd,
            "bollinger_bands": bb,
            "sma_20": sma_20,
            "sma_50": sma_50,
            "ema_12": ema_12,
            "ema_26": ema_26,
            "atr": atr,
            "close": closes[-1] if closes else 0.0,
            "volume": volumes[-1] if volumes else 0,
        }
        if len(closes) >= 2:
            result["change_1d_pct"] = (closes[-1] - closes[-2]) / closes[-2] * 100 if closes[-2] != 0 else 0.0
        if sma_20 > 0 and sma_50 > 0:
            result["trend"] = "bullish" if sma_20 > sma_50 else "bearish"
        else:
            result["trend"] = "neutral"
        if rsi is not None:
            result["rsi_signal"] = "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral"
        return result

    def generate_feature_matrix(self, tickers_data: dict) -> dict:
        feature_matrix = {}
        feature_names = []
        for ticker, data in tickers_data.items():
            features = self.compute_all_indicators(data.get("ohlcv", []))
            flat = {
                "rsi": features.get("rsi", 50),
                "macd": features.get("macd", {}).get("macd", 0),
                "macd_signal": features.get("macd", {}).get("signal", 0),
                "macd_hist": features.get("macd", {}).get("histogram", 0),
                "bb_upper": features.get("bollinger_bands", {}).get("upper", 0),
                "bb_middle": features.get("bollinger_bands", {}).get("middle", 0),
                "bb_lower": features.get("bollinger_bands", {}).get("lower", 0),
                "bb_bandwidth": features.get("bollinger_bands", {}).get("bandwidth", 0),
                "sma_20": features.get("sma_20", 0),
                "sma_50": features.get("sma_50", 0),
                "ema_12": features.get("ema_12", 0),
                "ema_26": features.get("ema_26", 0),
                "atr": features.get("atr", 0),
                "volume": features.get("volume", 0),
                "close": features.get("close", 0),
            }
            if not feature_names:
                feature_names = list(flat.keys())
            feature_matrix[ticker] = flat
        return {"matrix": feature_matrix, "feature_names": feature_names, "num_tickers": len(tickers_data)}

    def detect_outliers(self, prices: list, threshold: float = 3.0) -> list:
        if len(prices) < 3:
            return [{"index": i, "price": p, "z_score": 0.0, "is_outlier": False} for i, p in enumerate(prices)]
        mean_p = statistics.mean(prices)
        stdev_p = statistics.stdev(prices)
        if stdev_p == 0:
            return [{"index": i, "price": p, "z_score": 0.0, "is_outlier": False} for i, p in enumerate(prices)]
        results = []
        for i, price in enumerate(prices):
            z = (price - mean_p) / stdev_p
            results.append({
                "index": i, "price": price,
                "z_score": round(z, 4), "is_outlier": abs(z) > threshold,
            })
        outlier_count = sum(1 for r in results if r["is_outlier"])
        logger.info(f"Outlier detection: {outlier_count}/{len(prices)} outliers (z>{threshold})")
        return results
