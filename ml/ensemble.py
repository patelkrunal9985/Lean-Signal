"""Model ensemble — combines predictions from multiple models via weighted voting.
Gracefully handles missing models (degradation).
"""
import numpy as np
import json
from pathlib import Path
from typing import Any

from kronos.utils.config import MODEL_DIR


class ModelEnsemble:
    """
    Weighted ensemble of up to 4 models:
    - LightGBM (primary, 40%)
    - Random Forest (25%)
    - Extra Trees (15%)
    - XGBoost (20%) — or LSTM if available

    Each model predicts probability of "up" direction.
    Final = weighted average of individual probabilities.
    """

    def __init__(self):
        self.models: dict[str, Any] = {}
        self.weights: dict[str, float] = {
            "lightgbm": 0.40,
            "random_forest": 0.25,
            "extra_trees": 0.15,
            "xgboost": 0.20,
        }
        self.metadata_path = MODEL_DIR / "ensemble_weights.json"

    def register_model(self, name: str, model: Any, weight: float = None):
        self.models[name] = model
        if weight is not None:
            self.weights[name] = weight
            self._normalize_weights()

    def _normalize_weights(self):
        total = sum(self.weights.values())
        if total > 0:
            for k in self.weights:
                self.weights[k] /= total

    def predict_proba(self, features: np.ndarray) -> dict:
        probabilities = []
        used_weights = []
        model_votes = {}

        for name, model in self.models.items():
            if model is None:
                continue

            try:
                if hasattr(model, "predict_proba"):
                    proba = model.predict_proba(features.reshape(1, -1))[0]
                    if len(proba) >= 2:
                        prob = float(proba[1])
                    else:
                        prob = float(proba[0])
                elif hasattr(model, "predict"):
                    prob = float(model.predict(features.reshape(1, -1))[0])
                else:
                    continue

                probabilities.append(prob)
                used_weights.append(self.weights.get(name, 0.20))
                model_votes[name] = prob
            except Exception:
                continue

        if not probabilities:
            return {"probability": 0.5, "confidence": 0.0, "agreement": 0.0, "model_votes": {}}

        total_weight = sum(used_weights)
        if total_weight > 0:
            weighted_prob = sum(p * w for p, w in zip(probabilities, used_weights)) / total_weight
        else:
            weighted_prob = np.mean(probabilities)

        std = np.std(probabilities) if len(probabilities) > 1 else 0
        agreement = max(0, 1.0 - std * 4)

        distance = abs(weighted_prob - 0.5) * 2
        confidence = distance * (0.5 + agreement * 0.5)

        return {
            "probability": round(float(weighted_prob), 4),
            "confidence": round(float(confidence), 4),
            "agreement": round(float(agreement), 4),
            "model_votes": model_votes,
        }

    def save_weights(self):
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.metadata_path, "w") as f:
            json.dump({"weights": self.weights}, f)

    def load_weights(self):
        if self.metadata_path.exists():
            with open(self.metadata_path) as f:
                data = json.load(f)
                self.weights.update(data.get("weights", {}))
                self._normalize_weights()
