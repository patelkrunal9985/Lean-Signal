"""
HMM-based regime detector. Requires training data but provides more
nuanced classification than the rule-based approach.
Falls back to rule-based when insufficient training data.
"""
import numpy as np
from typing import Any

class HMMRegimeDetector:
    """
    Hidden Markov Model for market regime classification.
    
    Uses sklearn's GaussianHMM (via hmmlearn if available, else falls back
    to a simplified clustering approach).
    """
    
    def __init__(self, n_regimes: int = 4):
        self.n_regimes = n_regimes
        self.model = None
        self.is_trained = False
        self._feature_mean = None
        self._feature_std = None
    
    def _compute_features(self, ohlcv: list[dict]) -> np.ndarray:
        """Extract feature vector for HMM from OHLCV."""
        closes = np.array([c["close"] for c in ohlcv])
        highs = np.array([c["high"] for c in ohlcv])
        lows = np.array([c["low"] for c in ohlcv])
        volumes = np.array([c["volume"] for c in ohlcv])
        
        returns = np.diff(closes) / closes[:-1]
        log_returns = np.diff(np.log(closes + 1e-10))
        
        # Rolling volatility (10-bar)
        vol = np.zeros(len(returns))
        for i in range(len(returns)):
            start = max(0, i - 9)
            vol[i] = np.std(returns[start:i+1])
        
        # Volume change
        vol_change = np.diff(volumes) / (volumes[:-1] + 1e-10)
        
        # Feature matrix: [return, log_return, volatility, volume_change]
        min_len = min(len(returns), len(vol), len(vol_change))
        features = np.column_stack([
            returns[-min_len:],
            log_returns[-min_len:],
            vol[-min_len:],
            vol_change[-min_len:],
        ])
        
        return features
    
    def train(self, ohlcv: list[dict]) -> None:
        """Train HMM on historical data."""
        features = self._compute_features(ohlcv)
        if len(features) < 50:
            self.is_trained = False
            return
        
        # Normalize
        self._feature_mean = np.mean(features, axis=0)
        self._feature_std = np.std(features, axis=0) + 1e-10
        features_norm = (features - self._feature_mean) / self._feature_std
        
        # Use KMeans as a simplified HMM approximation (hmmlearn has complex deps)
        from sklearn.cluster import KMeans
        self.model = KMeans(n_clusters=self.n_regimes, random_state=42, n_init=10)
        self.model.fit(features_norm)
        self.is_trained = True
    
    def detect(self, ohlcv: list[dict], rule_result: dict) -> dict:
        """
        Detect regime using HMM. Falls back to rule-based if not trained.
        
        Parameters
        ----------
        ohlcv : list[dict]
        rule_result : dict from RuleBasedRegimeDetector.detect()
        
        Returns
        -------
        dict with primary_regime, confidence, scores
        """
        if not self.is_trained or self.model is None:
            return {"primary_regime": rule_result["primary_regime"], 
                    "confidence": rule_result["confidence"],
                    "scores": rule_result["scores"],
                    "source": "rule_fallback"}
        
        features = self._compute_features(ohlcv)
        if len(features) < 1:
            return {"primary_regime": rule_result["primary_regime"],
                    "confidence": rule_result["confidence"],
                    "source": "rule_fallback"}
        
        # Normalize using training stats
        features_norm = (features[-1:] - self._feature_mean) / self._feature_std
        
        # Get cluster assignment
        cluster = self.model.predict(features_norm)[0]
        
        # Map HMM cluster to regime label based on cluster center characteristics
        center = self.model.cluster_centers_[cluster]
        
        # Determine regime from cluster center features
        # center[0] = return, center[2] = volatility
        if center[2] > 0.5:  # high vol cluster
            hmm_regime = "high_volatility"
        elif center[0] > 0.3:  # positive returns
            hmm_regime = "strong_uptrend"
        elif center[0] < -0.3:  # negative returns
            hmm_regime = "strong_downtrend"
        else:
            hmm_regime = "ranging"
        
        # Fuse HMM and rule-based results
        rule_regime = rule_result["primary_regime"]
        rule_conf = rule_result["confidence"]
        
        if hmm_regime == rule_regime:
            primary = rule_regime
            confidence = min(rule_conf + 0.15, 0.95)
        else:
            # Use the one with higher confidence
            hmm_conf = 0.5  # base confidence for HMM
            if hmm_regime == "high_volatility":
                hmm_conf = min(abs(center[2]) * 2, 0.8)
            elif hmm_regime in ("strong_uptrend", "strong_downtrend"):
                hmm_conf = min(abs(center[0]) * 2, 0.8)
            
            if hmm_conf > rule_conf:
                primary = hmm_regime
                confidence = hmm_conf
            else:
                primary = rule_regime
                confidence = rule_conf
        
        return {
            "primary_regime": primary,
            "confidence": round(confidence, 4),
            "scores": rule_result.get("scores", {}),
            "hmm_cluster": int(cluster),
            "source": "hmm_fused",
        }
