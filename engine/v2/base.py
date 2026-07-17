"""
Base class for all advanced strategies.
Extends BaseSubagent with regime compatibility metadata.
"""
from engine.core.agent import BaseSubagent


class BaseAdvancedStrategy(BaseSubagent):
    """Strategy base with regime metadata and feature requirements."""
    
    def __init__(self, name: str):
        super().__init__(name, parent="modeling")
        self.requires_data: list[str] = []
        self.regime_compatibility: list[str] = ["*"]
    
    def is_compatible_with_regime(self, regime_type: str) -> bool:
        """Check if this strategy can operate in the given regime."""
        if "*" in self.regime_compatibility:
            return True
        return regime_type in self.regime_compatibility
    
    def get_regime_weight(self, regime_type: str) -> float:
        """Return weight multiplier for given regime. Override in subclass."""
        return 1.0
