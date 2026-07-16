"""Feature engineering pipeline — transforms OHLCV + indicators into
66 feature vectors for ML model training and inference.
"""
import numpy as np
from typing import Any

from kronos.utils.helpers import (
    calculate_rsi, calculate_macd, calculate_bollinger_bands,
    calculate_atr, calculate_sma, calculate_ema,
    calculate_adx, calculate_hv_percentile, calculate_rolling_zscore,
    calculate_value_area, calculate_order_flow_imbalance,
    detect_liquidity_sweep, calculate_anchored_vwap,
)
from .feature_store import FeatureStore


class FeatureTransformer:
    """
    Computes 66 features from OHLCV + indicators across 9 categories.

    Caches results in FeatureStore with 5-minute TTL.
    """

    def __init__(self):
        self.store = FeatureStore()

    def compute_features(self, ticker: str, ohlcv: list[dict],
                          indicators: dict = None) -> np.ndarray:
        cached = self.store.get(ticker, ohlcv)
        if cached is not None:
            return cached

        features = {}
        features.update(self._price_features(ohlcv))
        features.update(self._technical_features(ohlcv, indicators or {}))
        features.update(self._microstructure_features(ohlcv))
        features.update(self._statistical_features(ohlcv))
        features.update(self._regime_features(indicators or {}))
        features.update(self._interaction_features(ohlcv, indicators or {}))
        features.update(self._volume_profile_features(indicators or {}))

        feature_names = sorted(features.keys())
        vector = np.array([features[k] for k in feature_names], dtype=np.float32)

        if len(vector) < 66:
            vector = np.pad(vector, (0, 66 - len(vector)), 'constant')
        elif len(vector) > 66:
            vector = vector[:66]

        self.store.set(ticker, ohlcv, vector)
        return vector

    def compute_features_sequence(self, ticker: str, ohlcv: list[dict],
                                   indicators: dict = None) -> np.ndarray:
        """Compute feature vectors for every bar with enough history.
        
        Returns (N, 66) array where N = len(ohlcv) - min_lookback + 1.
        Each row is the feature vector at that point using only past data.
        """
        min_lookback = 55  # max lookback among all feature functions
        if len(ohlcv) < min_lookback + 5:
            return np.zeros((0, 66), dtype=np.float32)

        vectors = []
        for i in range(min_lookback, len(ohlcv)):
            window = ohlcv[:i + 1]
            vec = self.compute_features(ticker, window, indicators)
            vectors.append(vec)

        return np.array(vectors, dtype=np.float32)

    def _price_features(self, ohlcv: list[dict]) -> dict:
        if not ohlcv or len(ohlcv) < 5:
            return {}

        closes = np.array([c["close"] for c in ohlcv])
        features = {}

        for period, name in [(1, "ret_1"), (5, "ret_5"), (21, "ret_21")]:
            if len(closes) > period:
                features[f"ret_{name}"] = float((closes[-1] - closes[-(period + 1)]) / max(closes[-(period + 1)], 0.01))
            else:
                features[f"ret_{name}"] = 0.0

        log_ret = np.diff(np.log(closes + 1e-10))
        for period, name in [(1, "log_ret_1"), (5, "log_ret_5")]:
            if len(log_ret) >= period:
                features[name] = float(np.sum(log_ret[-period:]))
            else:
                features[name] = 0.0

        for period in [20, 50]:
            if len(closes) >= period:
                recent = closes[-period:]
                features[f"zscore_{period}"] = float((closes[-1] - np.mean(recent)) / max(np.std(recent), 1e-10))
            else:
                features[f"zscore_{period}"] = 0.0

        for period in [20, 50]:
            if len(closes) >= period:
                sma = np.mean(closes[-period:])
                features[f"price_sma{period}_dist"] = float((closes[-1] - sma) / max(sma, 0.01))
            else:
                features[f"price_sma{period}_dist"] = 0.0

        return features

    def _technical_features(self, ohlcv: list[dict], indicators: dict) -> dict:
        closes = [c["close"] for c in ohlcv]
        highs = [c["high"] for c in ohlcv]
        lows = [c["low"] for c in ohlcv]
        features = {}

        rsi = indicators.get("rsi_14", 0) or calculate_rsi(closes, 14)

        features["rsi_norm"] = float(rsi / 100.0) if rsi else 0.5

        if len(closes) >= 20:
            rsi_now = rsi if isinstance(rsi, (int, float)) else rsi[-1] if hasattr(rsi, '__iter__') else 50
            rsi_prev = indicators.get("rsi_14_prev", rsi_now)
            features["rsi_divergence"] = float(rsi_now - rsi_prev)
        else:
            features["rsi_divergence"] = 0.0

        macd_data = indicators.get("macd", {})
        if not macd_data and len(closes) >= 26:
            macd_data = calculate_macd(closes)

        macd_line = macd_data.get("macd", 0) if isinstance(macd_data, dict) else 0
        macd_hist = macd_data.get("histogram", 0)

        features["macd_hist_norm"] = float(macd_hist / max(abs(macd_line), 0.01)) if abs(macd_line) > 0.01 else 0.0
        features["macd_acceleration"] = 0.0

        bb = indicators.get("bollinger_bands", {})
        if not bb and len(closes) >= 20:
            bb = calculate_bollinger_bands(closes)

        current_price = closes[-1] if closes else 0
        bb_upper = bb.get("upper", current_price * 1.02)
        bb_lower = bb.get("lower", current_price * 0.98)
        bb_middle = bb.get("middle", current_price)

        bb_position = (current_price - bb_lower) / max(bb_upper - bb_lower, 0.01)
        bb_width = (bb_upper - bb_lower) / max(bb_middle, 0.01)
        bb_squeeze = 1.0 if bb_width < 0.04 else 0.0

        features["bb_position"] = float(min(max(bb_position, 0), 1))
        features["bb_width"] = float(bb_width)
        features["bb_squeeze"] = float(bb_squeeze)

        atr = indicators.get("atr_14", 0)
        if not atr and len(closes) >= 15:
            atr = calculate_atr(highs, lows, closes, 14)
        atr_pct = atr / max(current_price, 0.01) if atr else 0.01
        features["atr_ratio"] = float(atr_pct)

        return features

    def _microstructure_features(self, ohlcv: list[dict]) -> dict:
        if not ohlcv or len(ohlcv) < 5:
            return {}

        features = {}

        volumes = np.array([c["volume"] for c in ohlcv], dtype=float)
        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        current_vol = volumes[-1]
        features["vol_ratio"] = float(current_vol / max(avg_vol, 0.01))

        if len(volumes) >= 20:
            recent_vol = volumes[-20:]
            features["vol_zscore"] = float((current_vol - np.mean(recent_vol)) / max(np.std(recent_vol), 1e-10))
        else:
            features["vol_zscore"] = 0.0

        last = ohlcv[-1]
        body = abs(last["close"] - last["open"])
        candle_range = max(last["high"] - last["low"], 0.001)
        upper_wick = last["high"] - max(last["close"], last["open"])
        lower_wick = min(last["close"], last["open"]) - last["low"]

        features["candle_body_ratio"] = float(body / candle_range)
        features["candle_upper_wick"] = float(upper_wick / candle_range)
        features["candle_lower_wick"] = float(lower_wick / candle_range)
        features["candle_direction"] = float(1.0 if last["close"] > last["open"] else (-1.0 if last["close"] < last["open"] else 0.0))

        if len(ohlcv) >= 5:
            closes = [c["close"] for c in ohlcv[-10:]]
            up_count = 0
            for i in range(1, len(closes)):
                if closes[i] > closes[i - 1]:
                    up_count += 1
            features["consecutive_up_ratio"] = float(up_count / max(len(closes) - 1, 1))
        else:
            features["consecutive_up_ratio"] = 0.5

        of_data = calculate_order_flow_imbalance(ohlcv, 5) if len(ohlcv) >= 5 else {}
        features["order_flow_ratio"] = float(of_data.get("ratio", 0.5))

        sweep = detect_liquidity_sweep(ohlcv) if len(ohlcv) >= 10 else {}
        features["liq_sweep_detected"] = float(1.0 if sweep.get("detected", False) else 0.0)
        features["liq_sweep_confidence"] = float(sweep.get("confidence", 0.0))

        return features

    def _statistical_features(self, ohlcv: list[dict]) -> dict:
        if not ohlcv or len(ohlcv) < 20:
            return {}

        closes = np.array([c["close"] for c in ohlcv])
        log_returns = np.diff(np.log(closes + 1e-10))

        features = {}

        if len(log_returns) >= 10:
            returns_20 = log_returns[-20:]
            std_20 = np.std(returns_20)
            skew_val = float(np.mean(returns_20 ** 3) / max(std_20 ** 3, 1e-10))
            features["skew"] = 0.0 if np.isnan(skew_val) or np.isinf(skew_val) else skew_val
            kurt_val = float(np.mean(returns_20 ** 4) / max(std_20 ** 4, 1e-10) - 3)
            features["kurtosis"] = 0.0 if np.isnan(kurt_val) or np.isinf(kurt_val) else kurt_val
            features["max_drawdown_20"] = float(np.min(np.minimum.accumulate(closes[-20:]) - closes[-20:]) / max(closes[-20:][0], 0.01))

            if len(log_returns) >= 21:
                x1 = log_returns[-21:-1]  # 20 elements
                y1 = log_returns[-20:]    # 20 elements
                if len(x1) == len(y1) and len(x1) > 1:
                    features["autocorr_1"] = float(np.corrcoef(x1, y1)[0, 1])
                else:
                    features["autocorr_1"] = 0.0
            else:
                features["autocorr_1"] = 0.0

            if len(log_returns) >= 26:
                x5 = log_returns[-26:-5]  # 21 elements
                y5 = log_returns[-21:]    # 21 elements (fixed: was -21:-1 giving 20)
                if len(x5) == len(y5) and len(x5) > 1:
                    features["autocorr_5"] = float(np.corrcoef(x5, y5)[0, 1])
                else:
                    features["autocorr_5"] = 0.0
            else:
                features["autocorr_5"] = 0.0
        else:
            features["skew"] = 0.0
            features["kurtosis"] = 0.0
            features["max_drawdown_20"] = 0.0
            features["autocorr_1"] = 0.0
            features["autocorr_5"] = 0.0

        if len(log_returns) >= 20:
            threshold = np.percentile(log_returns[-20:], 5)
            tail = log_returns[-20:][log_returns[-20:] <= threshold]
            features["tail_risk"] = float(abs(np.mean(tail))) if len(tail) > 0 else 0.0
        else:
            features["tail_risk"] = 0.0

        return features

    def _regime_features(self, indicators: dict) -> dict:
        features = {}

        adx = indicators.get("adx", 0)
        features["trend_strength"] = float(min(adx / 50.0, 1.0)) if adx else 0.3

        hv_percentile = indicators.get("hv_percentile", 0.5)
        features["vol_regime_high"] = float(1.0 if hv_percentile > 0.85 else 0.0)
        features["vol_regime_low"] = float(1.0 if hv_percentile < 0.3 else 0.0)

        if "bollinger_bands" in indicators:
            bb = indicators["bollinger_bands"]
            prev_bb = indicators.get("bollinger_bands_prev", {})
            bb_width_change = (bb.get("bandwidth", 0) - prev_bb.get("bandwidth", 0)) if prev_bb else 0
            features["range_expansion"] = float(min(max(bb_width_change * 100, -1), 1))
        else:
            features["range_expansion"] = 0.0

        features["hv_percentile"] = float(hv_percentile)

        return features

    def _interaction_features(self, ohlcv: list[dict], indicators: dict) -> dict:
        features = {}

        rsi = indicators.get("rsi_14", 50)
        bb = indicators.get("bollinger_bands", {})

        features["rsi_bb_interaction"] = float((rsi / 100.0) * 2 - 1)

        atr = indicators.get("atr_14", 0)
        closes = [c["close"] for c in ohlcv[-2:]] if len(ohlcv) >= 2 else [0, 1]
        ret = (closes[-1] - closes[-2]) / max(closes[-2], 0.01) if len(closes) >= 2 else 0
        features["vol_return_interaction"] = float(atr * ret * 100) if atr else 0.0

        macd = indicators.get("macd", {})
        macd_hist = macd.get("histogram", 0) if isinstance(macd, dict) else 0
        features["macd_trend"] = float(macd_hist)

        features["autocorr_return"] = 0.0

        features["vol_range"] = float(atr * bb.get("bandwidth", 0) * 100) if atr else 0.0

        return features

    def _volume_profile_features(self, indicators: dict) -> dict:
        features = {}

        value_area = indicators.get("value_area", {})
        poc = value_area.get("poc", 0)
        vah = value_area.get("vah", 0)
        val = value_area.get("val", 0)

        features["price_vs_poc"] = float(poc) if poc else 0.5
        features["price_vs_vah"] = float(vah) if vah else 0.0
        features["price_vs_val"] = float(val) if val else 0.0

        avwap = indicators.get("anchored_vwap", {})
        features["avwap_distance"] = float(avwap.get("distance_pct", 0))

        return features
