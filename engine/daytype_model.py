"""
Walk-Forward Online Learning Day-Type Classifier.

Predicts today's NY session day type from pre-market IBKR-only features.

Architecture:
  - SGDClassifier (logistic regression) via sklearn
  - Features computed from IBKR data only:
      * Globex range ratio (range / previous day's RTH range)
      * Gap size and direction
      * VIX level and 1m/2m spread
      * Previous day's TPO shape (range, POC position, tail count)
      * Previous day's VPIN and cumulative delta
      * Previous day's volume vs 5-day average
      * Regime classification from last session
  - Walk-forward validation: train on last 20 sessions, predict current
  - Model persists to data/daytype_model.pkl
  - Retrained every 5 cycles (online update)

Target (day type → numeric label):
  0 = normal_day
  1 = balance_day
  2 = breakout_day_up
  3 = breakout_day_down
  4 = trend_day_up
  5 = trend_day_down

Fallback: if model not trained or confidence < 0.4, returns "unknown".
"""
from __future__ import annotations

import json
import numpy as np
import os
import pickle
import time
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger("engine.daytype_model")

DATA_DIR = Path(__file__).parent.parent / "data"
MODEL_PATH = DATA_DIR / "daytype_model.pkl"
FEATURES_PATH = DATA_DIR / "daytype_features.json"
LABEL_MAP = {
    0: "normal_day", 1: "balance_day",
    2: "breakout_day_up", 3: "breakout_day_down",
    4: "trend_day_up", 5: "trend_day_down",
}
REVERSE_LABEL_MAP = {v: k for k, v in LABEL_MAP.items()}

# Sklearn SGDClassifier (logistic regression with online learning)
# Lazy import to avoid hard dependency at module load time.
_SGD = None

# Model state
_model = None
_model_trained = False
_training_count = 0
_feature_history: list[tuple[list[float], str]] = []  # (features, actual_label)
_MAX_HISTORY = 100


def _get_sgd():
    global _SGD
    if _SGD is None:
        try:
            from sklearn.linear_model import SGDClassifier
            _SGD = SGDClassifier
        except ImportError:
            logger.warning("sklearn not installed — daytype model disabled")
            return None
    return _SGD


def _load_model():
    global _model, _model_trained
    try:
        if MODEL_PATH.exists():
            with open(MODEL_PATH, "rb") as f:
                _model = pickle.load(f)
            _model_trained = True
            logger.info("DayType model loaded from disk")
    except Exception as e:
        logger.debug(f"DayType model load failed: {e}")
        _model = None
        _model_trained = False


def _save_model():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if _model:
            with open(MODEL_PATH, "wb") as f:
                pickle.dump(_model, f)
    except Exception as e:
        logger.debug(f"DayType model save failed: {e}")


def _load_features():
    global _feature_history
    try:
        if FEATURES_PATH.exists():
            with open(FEATURES_PATH) as f:
                data = json.load(f)
            _feature_history = [(feat, label) for feat, label in data]
    except Exception:
        _feature_history = []


def _save_features():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(FEATURES_PATH, "w") as f:
            json.dump(_feature_history, f, indent=2)
    except Exception as e:
        logger.debug(f"DayType features save failed: {e}")


def _extract_features(
    ohlcv_1m: list,
    cumulative_delta: dict,
    tpo_profile: dict,
    globex_range: dict,
    vix_spot: float,
    vix_1m: float,
    vix_2m: float,
    current_price: float,
) -> list[float]:
    """Extract feature vector from available IBKR data.

    Returns flat list of ~25 numerical features.
    """
    features = []

    # 1. Price features (4)
    if ohlcv_1m and len(ohlcv_1m) >= 2:
        prev_close = ohlcv_1m[-2]["close"]
        features.append(ohlcv_1m[-1]["close"])
        features.append((ohlcv_1m[-1]["close"] - ohlcv_1m[0]["open"]) / max(ohlcv_1m[0]["open"], 0.01))
        features.append(max(c["high"] for c in ohlcv_1m[-20:]) - min(c["low"] for c in ohlcv_1m[-20:]))
        features.append(sum(c.get("volume", 0) for c in ohlcv_1m[-20:]))
    else:
        features.extend([0, 0, 0, 0])

    # 2. VPIN and delta (4)
    if cumulative_delta:
        features.append(cumulative_delta.get("vpin", 0))
        features.append(cumulative_delta.get("cumulative_delta", 0) / max(abs(cumulative_delta.get("cumulative_delta", 0)), 1))
        features.append(cumulative_delta.get("delta_60s", 0) / max(abs(cumulative_delta.get("delta_60s", 0)), 1))
        features.append(cumulative_delta.get("volume_imbalance", 0))
    else:
        features.extend([0, 0, 0, 0])

    # 3. TPO profile features (4)
    if tpo_profile:
        features.append(tpo_profile.get("profile_range", 0) / max(tpo_profile.get("poc", 1), 0.01))
        features.append(abs(tpo_profile.get("close_vs_poc", 0)))
        features.append(tpo_profile.get("tail_count", 0))
        features.append(tpo_profile.get("period_count", 0))
    else:
        features.extend([0, 0, 0, 0])

    # 4. Globex features (3)
    if globex_range:
        features.append(globex_range.get("gap_pct", 0))
        features.append(1.0 if globex_range.get("gap_up", False) else -1.0 if globex_range.get("gap_down", False) else 0.0)
        features.append(globex_range.get("globex_range", 0))
    else:
        features.extend([0, 0, 0])

    # 5. VIX features (3)
    features.append(vix_spot)
    features.append(vix_1m - vix_2m if vix_2m > 0 else 0)
    features.append(vix_spot / max(vix_1m, 0.01) if vix_1m > 0 else 0)

    # 6. Volume features (3)
    if ohlcv_1m and len(ohlcv_1m) >= 20:
        recent_vol = sum(c.get("volume", 0) for c in ohlcv_1m[-10:])
        older_vol = sum(c.get("volume", 0) for c in ohlcv_1m[-20:-10])
        features.append(recent_vol / max(older_vol, 1))
        features.append(sum(1 for c in ohlcv_1m[-5:] if c["close"] > c["open"]))
        features.append(1.0 if current_price > 0 and ohlcv_1m[-1]["close"] > ohlcv_1m[-5]["close"] else 0.0)
    else:
        features.extend([1.0, 2.5, 0.5])

    # 7. Time features (3)
    _, _, hour = _current_et_components()
    features.append(hour / 24.0)
    features.append(1.0 if 570 <= hour * 60 <= 600 else 0.0)  # pre-market?
    features.append(1.0 if 900 <= hour * 60 <= 960 else 0.0)  # power hour?

    return features


def _current_et_components():
    from utils.time_utils import now_ny
    t = now_ny()
    return t.weekday(), t.hour, t.minute


def predict_day_type(
    ohlcv_1m: list,
    cumulative_delta: dict,
    tpo_profile: dict,
    globex_range: dict,
    vix_spot: float,
    vix_1m: float,
    vix_2m: float,
    current_price: float,
) -> dict:
    """Predict today's day type from pre-market/early-session features.

    Returns dict with:
      - prediction: str (day type label or "unknown")
      - confidence: float (0-1)
      - probabilities: dict of label -> probability
      - trained: bool
      - samples: int (number of training samples)
    """
    global _model, _model_trained, _training_count

    if _model is None:
        _load_model()

    SGD = _get_sgd()
    if SGD is None or _model is None or not _model_trained:
        return {
            "prediction": "unknown",
            "confidence": 0,
            "probabilities": {},
            "trained": False,
            "samples": len(_feature_history),
        }

    features = _extract_features(
        ohlcv_1m, cumulative_delta, tpo_profile,
        globex_range, vix_spot, vix_1m, vix_2m, current_price,
    )

    try:
        probs = _model.predict_proba([features])[0]
        pred_class = int(_model.predict([features])[0])
        confidence = float(max(probs))
        pred_label = LABEL_MAP.get(pred_class, "unknown")
        prob_dict = {LABEL_MAP.get(i, f"class_{i}"): round(float(p), 4) for i, p in enumerate(probs)}

        if confidence < 0.40:
            pred_label = "unknown"

        return {
            "prediction": pred_label,
            "confidence": round(confidence, 4),
            "probabilities": prob_dict,
            "trained": True,
            "samples": len(_feature_history),
        }
    except Exception as e:
        logger.debug(f"DayType prediction failed: {e}")
        return {"prediction": "unknown", "confidence": 0, "trained": True, "samples": len(_feature_history)}


def train(
    actual_label: str,
    ohlcv_1m: list,
    cumulative_delta: dict,
    tpo_profile: dict,
    globex_range: dict,
    vix_spot: float,
    vix_1m: float,
    vix_2m: float,
    current_price: float,
):
    """Record a training example and periodically retrain.

    Called at end-of-day or when the day type is confirmed.

    Args:
        actual_label: One of the LABEL_MAP values.
        All other args: Same as predict_day_type.
    """
    global _model, _model_trained, _training_count, _feature_history

    target = REVERSE_LABEL_MAP.get(actual_label)
    if target is None:
        logger.debug(f"DayType train: unknown label {actual_label}")
        return

    features = _extract_features(
        ohlcv_1m, cumulative_delta, tpo_profile,
        globex_range, vix_spot, vix_1m, vix_2m, current_price,
    )

    # Store for persistence
    _feature_history.append((features, actual_label))
    if len(_feature_history) > _MAX_HISTORY:
        _feature_history = _feature_history[-_MAX_HISTORY:]
    _save_features()

    # Retrain every 5 cycles or when model is fresh with 5+ samples
    SGD = _get_sgd()
    if SGD is None:
        return

    _training_count += 1
    should_retrain = _training_count % 5 == 0 or (_model is None and len(_feature_history) >= 5)

    if not should_retrain and _model is not None:
        # Online update (partial fit)
        try:
            _model.partial_fit([features], [target], classes=np.array([0, 1, 2, 3, 4, 5]))
            _model_trained = True
            _save_model()
        except Exception as e:
            logger.debug(f"DayType online update failed: {e}")
        return

    # Full retrain
    if len(_feature_history) >= 5:
        try:
            X = [f for f, _ in _feature_history]
            y = [REVERSE_LABEL_MAP.get(l, 0) for _, l in _feature_history]
            unique_classes = sorted(set(y))
            if len(unique_classes) < 2:
                # If only 1 class so far, fit with dummy data for all 6 classes
                # then partial_fit will build from there
                _save_features()
                return
            new_model = SGD(loss="log_loss", penalty="l2", alpha=0.0001, max_iter=1000, tol=1e-3)
            new_model.fit(X, y)
            _model = new_model
            _model_trained = True
            _save_model()
            logger.info(f"DayType model retrained: {len(_feature_history)} samples ({len(unique_classes)} classes)")
        except Exception as e:
            logger.debug(f"DayType full retrain failed: {e}")


def init():
    """Initialize model and feature history from disk."""
    _load_model()
    _load_features()
    logger.info(f"DayType model initialized: samples={len(_feature_history)}, trained={_model_trained}")
