"""
Adaptive composite scoring system.
Replaces the hardcoded 60/25/15 formula with regime-aware,
performance-weighted ensemble scoring.
"""
import json
import numpy as np
from typing import Any
from pathlib import Path
from kronos.utils.config import MODEL_DIR


class AdaptiveCompositeScorer:
    """
    Computes composite signal scores using:
    1. Strategy voting with regime-dependent weights
    2. ML signal integration
    3. Pattern recognition with regime adjustment
    4. RL Q-value contribution
    5. Risk penalty

    Weights shift based on:
    - Current market regime (from RegimeDetector)
    - Goal progress (near goal = more conservative)
    - Historical strategy performance (from lesson system)
    """

    def __init__(self):
        self.weights_path = MODEL_DIR / "scorer_weights.json"
        self._load_weights()

    def _load_weights(self):
        """Load persisted weights or use defaults."""
        defaults = {
            "strategy_vote": 0.50,
            "pattern": 0.15,
            "ml_signal": 0.10,
            "rl_signal": 0.05,
            "risk_penalty_max": 0.20,
            "regime_boost_max": 0.15,
        }

        if self.weights_path.exists():
            try:
                with open(self.weights_path) as f:
                    saved = json.load(f)
                    defaults.update(saved)
            except Exception:
                pass

        self.weights = defaults

    def _get_regime_adjustment(self, regime_type: str) -> float:
        """Return confidence adjustment based on regime (-0.10 to +0.10)."""
        adjustments = {
            "strong_uptrend": 0.05,
            "strong_downtrend": 0.00,
            "ranging": 0.10,
            "high_volatility": -0.10,
        }
        return adjustments.get(regime_type, 0.0)

    def compute(
        self,
        strategy_results: list[dict],
        pattern_result: dict,
        ml_result: dict,
        rl_result: dict,
        risk_result: dict,
        regime_type: str = "ranging",
        goal_progress: float = 0.0,
    ) -> tuple[str, float, dict]:
        """
        Compute composite signal score.

        Parameters
        ----------
        strategy_results : list of per-strategy result dicts
        pattern_result : dict from PatternRecognitionSubagent
        ml_result : dict from ML pipeline
        rl_result : dict from RL subagent
        risk_result : dict from RiskForecastSubagent
        regime_type : str — current market regime
        goal_progress : float — 0.0 to 1.0

        Returns
        -------
        tuple of (direction: str, composite_score: float, debug: dict)
        """
        # 1. Count strategy votes (with per-strategy weights)
        long_weight = 0.0
        short_weight = 0.0
        total_weight = 0.0
        strategy_details = []

        for s_result in strategy_results:
            direction = s_result.get("direction", "neutral")
            confidence = s_result.get("confidence", 0.0)
            strategy_name = s_result.get("strategy", "unknown")

            if direction == "neutral" or confidence < 0.15:
                continue

            regime_bonus = self._get_regime_adjustment(regime_type)
            adj_conf = confidence + regime_bonus

            if direction == "long":
                long_weight += adj_conf
            elif direction == "short":
                short_weight += adj_conf

            total_weight += adj_conf
            strategy_details.append({
                "strategy": strategy_name,
                "direction": direction,
                "confidence": confidence,
                "adjusted": round(adj_conf, 4),
                "regime_bonus": regime_bonus,
            })

        # 2. ML signal contribution
        ml_signal = ml_result.get("ml_signal", "neutral")
        ml_confidence = ml_result.get("ml_confidence", 0.0)
        ml_weight = self.weights["ml_signal"]

        if ml_signal == "long" and ml_confidence > 0.15:
            long_weight += ml_confidence * 2 * ml_weight
            total_weight += ml_confidence * ml_weight
            strategy_details.append({
                "strategy": "ml_ensemble",
                "direction": ml_signal,
                "confidence": ml_confidence,
                "adjusted": round(ml_confidence * ml_weight, 4),
            })
        elif ml_signal == "short" and ml_confidence > 0.15:
            short_weight += ml_confidence * 2 * ml_weight
            total_weight += ml_confidence * ml_weight
            strategy_details.append({
                "strategy": "ml_ensemble",
                "direction": ml_signal,
                "confidence": ml_confidence,
                "adjusted": round(ml_confidence * ml_weight, 4),
            })

        # 3. Pattern recognition contribution
        pattern_direction = pattern_result.get("direction", "neutral")
        pattern_confidence = pattern_result.get("confidence", 0.0)
        pattern_weight = self.weights["pattern"]

        if regime_type == "high_volatility":
            pattern_weight *= 0.5

        if pattern_direction == "long" and pattern_confidence > 0.3:
            long_weight += pattern_confidence * pattern_weight
            total_weight += pattern_confidence * pattern_weight
        elif pattern_direction == "short" and pattern_confidence > 0.3:
            short_weight += pattern_confidence * pattern_weight
            total_weight += pattern_confidence * pattern_weight

        # 4. RL Q-value contribution
        rl_q_value = rl_result.get("q_value", 0.0)
        rl_weight = self.weights["rl_signal"]

        if rl_q_value > 0.1:
            long_weight += rl_q_value * rl_weight
            total_weight += rl_q_value * rl_weight
        elif rl_q_value < -0.1:
            short_weight += abs(rl_q_value) * rl_weight
            total_weight += abs(rl_q_value) * rl_weight

        # 5. Goal progress adjustment
        progress_pct = goal_progress if isinstance(goal_progress, (int, float)) else goal_progress.get("progress_pct", 0.0)
        goal_multiplier = 1.0
        if progress_pct > 80:
            goal_multiplier = 1.3
            if long_weight > 0 or short_weight > 0:
                if long_weight > short_weight:
                    long_weight *= (1.0 / goal_multiplier)
                else:
                    short_weight *= (1.0 / goal_multiplier)

        # 6. Determine direction
        if long_weight > short_weight and long_weight > total_weight * 0.3:
            direction = "long"
            raw_score = long_weight / max(total_weight, 0.001)
        elif short_weight > long_weight and short_weight > total_weight * 0.3:
            direction = "short"
            raw_score = short_weight / max(total_weight, 0.001)
        else:
            direction = "neutral"
            raw_score = 0.0

        # 7. Risk penalty
        risk_score = risk_result.get("risk_score", 0.5)
        risk_penalty = 0.0
        if risk_score > 0.6:
            risk_penalty = (risk_score - 0.6) / 0.4 * self.weights["risk_penalty_max"]

        composite = max(0.0, min(raw_score * (1.0 - risk_penalty), 1.0))

        debug = {
            "direction": direction,
            "composite_score": round(composite, 4),
            "raw_score": round(raw_score, 4),
            "risk_penalty": round(risk_penalty, 4),
            "long_weight": round(long_weight, 4),
            "short_weight": round(short_weight, 4),
            "total_weight": round(total_weight, 4),
            "goal_multiplier": goal_multiplier,
            "strategy_details": strategy_details,
            "weights_used": self.weights,
        }

        return direction, round(composite, 4), debug
