"""Beta-binomial probability calibration for model outputs.
Better than Platt scaling for financial data (small samples, non-stationary).
"""
import json
import numpy as np
from pathlib import Path
from typing import Any
from kronos.utils.config import MODEL_DIR


class ConfidenceCalibrator:
    """
    Beta-binomial calibration with 10 bins.

    For each bin, tracks (alpha, beta) prior and (wins, trials) observed.
    Calibrated probability = (alpha + wins) / (alpha + beta + trials)

    Prior: Beta(1, 1) = uniform (no bias)
    """

    def __init__(self, n_bins: int = 10):
        self.n_bins = n_bins
        self.bins = {}
        self.calibration_path = MODEL_DIR / "calibration_data.json"
        self._load()

    def _bin_index(self, probability: float) -> int:
        idx = min(int(probability * self.n_bins), self.n_bins - 1)
        return max(0, idx)

    def update(self, predicted_proba: float, actual_outcome: int):
        bin_idx = self._bin_index(predicted_proba)
        if bin_idx not in self.bins:
            self.bins[bin_idx] = {"alpha": 1.0, "beta": 1.0, "wins": 0, "trials": 0}

        self.bins[bin_idx]["trials"] += 1
        if actual_outcome == 1:
            self.bins[bin_idx]["wins"] += 1

    def calibrate(self, predicted_proba: float) -> float:
        bin_idx = self._bin_index(predicted_proba)
        bin_data = self.bins.get(bin_idx)

        if bin_data is None or bin_data["trials"] == 0:
            return predicted_proba

        alpha = bin_data["alpha"] + bin_data["wins"]
        beta = bin_data["beta"] + (bin_data["trials"] - bin_data["wins"])

        calibrated = alpha / (alpha + beta)
        return round(float(calibrated), 4)

    def calibrate_with_ci(self, predicted_proba: float) -> dict:
        bin_idx = self._bin_index(predicted_proba)
        bin_data = self.bins.get(bin_idx)

        if bin_data is None or bin_data["trials"] == 0:
            return {"calibrated": predicted_proba, "lower_bound": max(0, predicted_proba - 0.1), "upper_bound": min(1, predicted_proba + 0.1)}

        alpha = bin_data["alpha"] + bin_data["wins"]
        beta = bin_data["beta"] + (bin_data["trials"] - bin_data["wins"])

        mean = alpha / (alpha + beta)
        var = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))
        std = np.sqrt(var)

        return {
            "calibrated": round(float(mean), 4),
            "lower_bound": round(float(max(0, mean - 1.645 * std)), 4),
            "upper_bound": round(float(min(1, mean + 1.645 * std)), 4),
            "samples": bin_data["trials"],
        }

    def save(self):
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.calibration_path, "w") as f:
            json.dump({"bins": self.bins}, f)

    def _load(self):
        if self.calibration_path.exists():
            try:
                with open(self.calibration_path) as f:
                    data = json.load(f)
                    self.bins = {int(k): v for k, v in data.get("bins", {}).items()}
            except Exception:
                self.bins = {}

    def get_reliability_diagnostics(self) -> dict:
        if not self.bins:
            return {"status": "no_data"}

        total_trials = sum(b["trials"] for b in self.bins.values())
        return {
            "total_samples": total_trials,
            "bins_populated": len(self.bins),
            "status": "active",
        }
