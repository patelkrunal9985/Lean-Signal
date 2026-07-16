"""
Composite regime detector — combines rule-based and HMM approaches.
Primary interface for the rest of the system.
"""
from .rule_detector import RuleBasedRegimeDetector
from .hmm_detector import HMMRegimeDetector

class RegimeDetector:
    """
    Composite market regime detector.
    
    Uses rule-based detection immediately (no training needed).
    HMM kicks in after sufficient data is collected.
    
    Output feeds into:
    - Strategy selection (Planner)
    - Strategy confidence modifiers (Modeling)
    - Composite weight allocation (Modeling)
    - Position sizing multipliers (Risk)
    """
    
    REGIME_MULTIPLIERS = {
        "strong_uptrend": 0.80,
        "strong_downtrend": 0.70,
        "ranging": 1.00,
        "high_volatility": 0.50,
    }
    
    REGIME_RECOMMENDED_STRATEGIES = {
        "strong_uptrend": ["vw_momentum", "trend_momentum", "mtf_confluence"],
        "strong_downtrend": ["vw_momentum", "order_flow_delta", "mtf_confluence"],
        "ranging": ["mean_reversion", "sector_relative_zscore", "rvol_absorption"],
        "high_volatility": ["volatility_breakout", "order_flow_delta", "vw_momentum"],
    }
    
    def __init__(self):
        self.rule_detector = RuleBasedRegimeDetector()
        self.hmm_detector = HMMRegimeDetector()
        self.hmm_trained = False
    
    def train_hmm(self, ohlcv_data: dict[str, list[dict]]) -> None:
        """Train HMM on pooled historical data from multiple tickers."""
        all_ohlcv = []
        for ticker, ohlcv in ohlcv_data.items():
            if len(ohlcv) >= 100:
                all_ohlcv.extend(ohlcv)
        if len(all_ohlcv) >= 100:
            self.hmm_detector.train(all_ohlcv)
            self.hmm_trained = self.hmm_detector.is_trained
    
    def detect(self, ohlcv: list[dict], indicators: dict) -> dict:
        """
        Detect current market regime.
        
        Parameters
        ----------
        ohlcv : list of OHLCV dicts
        indicators : dict of technical indicators
        
        Returns
        -------
        dict:
            primary_regime: str
            confidence: float
            scores: dict of per-regime scores
            details: feature values
            risk_multiplier: float (for position sizing)
            recommended_strategies: list[str]
        """
        rule_result = self.rule_detector.detect(ohlcv, indicators)
        
        if self.hmm_trained:
            result = self.hmm_detector.detect(ohlcv, rule_result)
        else:
            result = rule_result
        
        regime = result["primary_regime"]
        
        result["risk_multiplier"] = self.REGIME_MULTIPLIERS.get(regime, 1.0)
        result["recommended_strategies"] = self.REGIME_RECOMMENDED_STRATEGIES.get(regime, [])
        
        return result
    
    def get_regime_weight(self, regime_type: str, strategy_name: str) -> float:
        """Return a strategy weight boost based on regime compatibility (0.5-1.5)."""
        recs = self.REGIME_RECOMMENDED_STRATEGIES.get(regime_type, [])
        if strategy_name in recs:
            return 1.2  # boost
        if strategy_name in ("mean_reversion",) and regime_type in ("strong_uptrend", "strong_downtrend"):
            return 0.5  # penalize mean reversion in trends
        return 1.0  # neutral
