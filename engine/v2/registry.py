"""
Central strategy registry — manages all strategies (legacy + advanced).
Provides dynamic loading, regime gating, metadata, and a canonical
V2 strategy catalog that is the SINGLE source of truth for strategy
names, entry_indicators keys, display labels, and import paths.

Every consumer that needs a list of V2 strategies MUST derive it from
this module. Hardcoded lists in modeling_agent.py, kronos_server.py,
and option_analyzer.py are forbidden.
"""

import json
import importlib

from utils.logger import get_logger
from utils.config import PROJECT_ROOT

logger = get_logger("engine.v2.registry")


# ═══════════════════════════════════════════════════════════════
# V2 Strategy Catalog — Single Source of Truth
# ═══════════════════════════════════════════════════════════════
# Each entry:
#   name           — subagent key (matching what modeling_agent registers)
#   label          — human-readable display name for UI
#   ei_key         — key in entry_indicators where confidence is stored
#   module         — import path relative to kronos root
#   class_name     — class to instantiate
#   dir_source     — "confidence" = direction from confidence threshold
#                    "order_flow" = direction from order_flow_bias
#   ei_reasons_key — optional: entry_indicators key for reasons list
#   is_legacy      — true for the 3 basic strategies (mean_reversion, etc.)

V2_STRATEGY_CATALOG = [
    # ── 13 Advanced V2 strategies ──
    {
        "name": "vw_momentum",
        "label": "VW Momentum",
        "ei_key": "vw_momentum_signal",
        "module": "engine.v2.vw_momentum",
        "class_name": "VWMomentumStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "order_flow_delta",
        "label": "Order Flow",
        "ei_key": "order_flow_delta_conf",
        "module": "engine.v2.order_flow_delta",
        "class_name": "OrderFlowDeltaStrategy",
        "dir_source": "order_flow",
        "ei_reasons_key": "order_flow_delta_reasons",
    },
    {
        "name": "sector_relative_zscore",
        "label": "Sector Z-Score",
        "ei_key": "sector_relative_zscore_conf",
        "module": "engine.v2.sector_relative_zscore",
        "class_name": "SectorRelativeZscoreStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "rvol_absorption",
        "label": "RVOL Absorption",
        "ei_key": "rvol_absorption_conf",
        "module": "engine.v2.rvol_absorption",
        "class_name": "RVOLAbsorptionStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "mtf_confluence",
        "label": "MTF Confluence",
        "ei_key": "mtf_confluence_conf",
        "module": "engine.v2.mtf_confluence",
        "class_name": "MTFConfluenceStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "vwap_deviation",
        "label": "VWAP Deviation",
        "ei_key": "vwap_deviation_conf",
        "module": "engine.v2.vwap_deviation",
        "class_name": "VWAPDeviationStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "volume_momentum_surge",
        "label": "Vol Momentum Surge",
        "ei_key": "volume_momentum_surge_conf",
        "module": "engine.v2.volume_momentum_surge",
        "class_name": "VolumeMomentumSurgeStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "sr_levels",
        "label": "S/R Levels",
        "ei_key": "sr_levels_conf",
        "module": "engine.v2.sr_levels",
        "class_name": "SRLevelsStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "opening_range",
        "label": "Opening Range",
        "ei_key": "opening_range_conf",
        "module": "engine.v2.opening_range",
        "class_name": "OpeningRangeStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "stop_hunt",
        "label": "Stop Hunt",
        "ei_key": "stop_hunt_conf",
        "module": "engine.v2.stop_hunt",
        "class_name": "StopHuntStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "pullback",
        "label": "Pullback",
        "ei_key": "pullback_conf",
        "module": "engine.v2.pullback",
        "class_name": "PullbackStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "volume_profile",
        "label": "Volume Profile",
        "ei_key": "volume_profile_conf",
        "module": "engine.v2.volume_profile",
        "class_name": "VolumeProfileStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "spread_compression",
        "label": "Spread Compression",
        "ei_key": "spread_compression_conf",
        "module": "engine.v2.spread_compression",
        "class_name": "SpreadCompressionStrategy",
        "dir_source": "confidence",
    },
    # ── 3 Advanced Indicator strategies ──
    {
        "name": "advanced_momentum",
        "label": "Adv Momentum",
        "ei_key": "adv_momentum_conf",
        "module": "engine.v2.advanced_momentum",
        "class_name": "AdvancedMomentumStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "market_structure",
        "label": "Market Structure",
        "ei_key": "market_structure_conf",
        "module": "engine.v2.market_structure_strategy",
        "class_name": "MarketStructureStrategy",
        "dir_source": "confidence",
    },
    {
        "name": "volatility_regime",
        "label": "Volatility Regime",
        "ei_key": "volatility_regime_conf",
        "module": "engine.v2.volatility_regime_strategy",
        "class_name": "VolatilityRegimeStrategy",
        "dir_source": "confidence",
    },
    # ── 3 Legacy / basic strategies ──
    {
        "name": "mean_reversion",
        "label": "Mean Reversion",
        "ei_key": "mean_reversion_conf",
        "module": None,       # registered directly in ModelingAgent.__init__
        "class_name": "MeanReversionStrategy",
        "dir_source": "confidence",
        "is_legacy": True,
    },
    {
        "name": "trend_momentum",
        "label": "Trend Momentum",
        "ei_key": "trend_momentum_conf",
        "module": None,
        "class_name": "TrendMomentumStrategy",
        "dir_source": "confidence",
        "is_legacy": True,
    },
    {
        "name": "volatility_breakout",
        "label": "Volatility Breakout",
        "ei_key": "volatility_breakout_conf",
        "module": None,
        "class_name": "VolatilityBreakoutStrategy",
        "dir_source": "confidence",
        "is_legacy": True,
    },
    # ── ML Ensemble (virtual strategy) ──
    {
        "name": "ml_ensemble",
        "label": "ML Ensemble",
        "ei_key": "ml_confidence",
        "module": None,       # not a standalone subagent
        "class_name": None,
        "dir_source": "confidence",
    },
]

# Total: 20 V2 strategies (16 subagent-based + 1 ML + 3 legacy)


# ═══════════════════════════════════════════════════════════════
# Derived lookup helpers
# ═══════════════════════════════════════════════════════════════

# name → catalog entry
_V2_BY_NAME: dict[str, dict] = {e["name"]: e for e in V2_STRATEGY_CATALOG}

# ei_key → catalog entry
_V2_BY_EI_KEY: dict[str, dict] = {e["ei_key"]: e for e in V2_STRATEGY_CATALOG}

# list of just the names (for iteration)
V2_STRATEGY_NAMES: list[str] = [e["name"] for e in V2_STRATEGY_CATALOG]

# list of names that have importable modules (for modeling_agent registration)
V2_IMPORTABLE_NAMES: list[str] = [e["name"] for e in V2_STRATEGY_CATALOG if e.get("module")]

# ═══════════════════════════════════════════════════════════════
# Per-instrument-type V2 strategy lists
# ═══════════════════════════════════════════════════════════════
#   stock:   all 20 strategies (IEX depth + OHLCV available)
#   future:  all 20 strategies (OHLCV fallback available for all)
#   option:  8 conservative subset for UI display only —
#            V2 is NOT dispatched for options in modeling_agent.py
#            (option OHLCV is stale/low-volume, V2 signals on it are noise)
V2_BY_INSTR_TYPE: dict[str, list[str]] = {
    "stock": V2_STRATEGY_NAMES,
    # Futures: restrict to OHLCV-compatible strategies only.
    # Many V2 strategies are stock-specific (sector baskets, pre-market opening
    # range, stop-hunt patterns) and return neutral on futures, inflating
    # false-negatives. This subset keeps only strategies validated on futures data.
    "future": [
        "mean_reversion", "trend_momentum", "volatility_breakout",
        "vw_momentum", "advanced_momentum", "vwap_deviation",
        "volume_momentum_surge", "market_structure", "volatility_regime",
        "mtf_confluence", "rvol_absorption", "order_flow_delta",
        "volume_profile", "ml_ensemble",
    ],
    "option": [
        "vw_momentum", "sector_relative_zscore",
        "mtf_confluence", "advanced_momentum",
        "market_structure", "volatility_regime",
        "mean_reversion", "trend_momentum",
    ],
}


def get_v2_catalog_entry(name: str) -> dict | None:
    """Return the catalog entry for a V2 strategy by name."""
    return _V2_BY_NAME.get(name)


def get_v2_entry_by_ei_key(ei_key: str) -> dict | None:
    """Return the catalog entry for a V2 strategy by entry_indicators key."""
    return _V2_BY_EI_KEY.get(ei_key)


def get_v2_ei_key_map() -> list[tuple[str, str, str]]:
    """Return list of (ei_key, label, dir_source) tuples for the Analyse popup.

    This is what kronos_server.py's _V2_KEY_MAP previously hardcoded.
    """
    return [(e["ei_key"], e["label"], e["dir_source"]) for e in V2_STRATEGY_CATALOG]


def get_v2_ei_key_to_name() -> dict[str, str]:
    """Return mapping of ei_key → strategy name (for _V2_STRAT map)."""
    return {e["ei_key"]: e["name"] for e in V2_STRATEGY_CATALOG}


def get_v2_display_order() -> list[str]:
    """Return strategy names in catalog order (for consistent display)."""
    return [e["name"] for e in V2_STRATEGY_CATALOG]


def get_v2_names_for_type(instr_type: str) -> list[str]:
    """Return V2 strategy names applicable for a given instrument type.

    Args:
        instr_type: "stock", "future", or "option"
    Returns:
        List of V2 strategy names. Defaults to all strategies for unknown types.
    """
    return list(V2_BY_INSTR_TYPE.get(instr_type, V2_BY_INSTR_TYPE["stock"]))


def get_v2_display_keys_for_type(instr_type: str) -> list[str]:
    """Return V2 keys for a given instrument type, matching the frontend V2_KEY_MAP.

    These are the keys used in the JS ``_buildV2Strategies`` V2_KEY_MAP array
    for display/iteration.  Most match the catalog ``ei_key`` directly, but a
    few differ (e.g. ``order_flow_delta`` vs the catalog's storage key
    ``order_flow_delta_conf``).  This function is the single source of truth
    for the client-side ``V2_BY_TYPE`` variable injected at render time.
    """
    names = V2_BY_INSTR_TYPE.get(instr_type, V2_BY_INSTR_TYPE["stock"])
    # ── V2_KEY_MAP display key overrides ──
    # The catalog's ei_key is the storage key (used to read confidence from
    # entry_indicators).  The JS V2_KEY_MAP uses a display key that matches
    # the flat-score key in entry_indicators.  Override mismatches here.
    _display_key: dict[str, str] = {
        "order_flow_delta_conf": "order_flow_delta",
    }
    return [
        _display_key.get(_V2_BY_NAME[n]["ei_key"], _V2_BY_NAME[n]["ei_key"])
        for n in names if n in _V2_BY_NAME
    ]


# ═══════════════════════════════════════════════════════════════
# Strategy metadata (JSON config — preserves backward compat)
# ═══════════════════════════════════════════════════════════════

class StrategyRegistry:
    """Registry for all trading strategies with regime compatibility metadata."""

    _strategies: dict[str, dict] = {}
    _loaded = False

    @classmethod
    def _ensure_loaded(cls):
        if cls._loaded:
            return
        cls._load_from_config()
        cls._loaded = True

    @classmethod
    def _load_from_config(cls):
        """Load strategy metadata from JSON config."""
        config_path = PROJECT_ROOT / "kronos" / "config" / "strategy_config.json"
        if config_path.exists():
            with open(config_path) as f:
                cls._strategies = json.load(f)

    @classmethod
    def get_enabled_strategies(cls, regime_type: str = None) -> list[str]:
        """Return strategy names that are enabled and regime-compatible."""
        cls._ensure_loaded()
        result = []
        for name, cfg in cls._strategies.items():
            if not cfg.get("enabled", True):
                continue
            if regime_type:
                compat = cfg.get("regime_compatibility", ["*"])
                if "*" not in compat and regime_type not in compat:
                    continue
            result.append(name)
        return result

    @classmethod
    def get_strategy_config(cls, name: str) -> dict:
        """Get configuration for a specific strategy."""
        cls._ensure_loaded()
        return cls._strategies.get(name, {})

    @classmethod
    def iter_strategies(cls, regime_type: str = None):
        """Iterate over (name, config) for enabled, compatible strategies."""
        for name in cls.get_enabled_strategies(regime_type):
            yield name, cls._strategies[name]

    @classmethod
    def get_weights(cls, regime_type: str = None) -> dict[str, float]:
        """Return dynamic strategy weights based on regime compatibility."""
        cls._ensure_loaded()
        weights = {}
        for name, cfg in cls._strategies.items():
            if not cfg.get("enabled", True):
                continue
            w = cfg.get("weight_default", 0.10)
            if regime_type:
                compat = cfg.get("regime_compatibility", ["*"])
                if "*" not in compat and regime_type not in compat:
                    w *= 0.3
            weights[name] = w

        total = sum(weights.values())
        if total > 0:
            for k in weights:
                weights[k] /= total
        return weights


class V2StrategyRegistry:
    """Simplified V2 strategy runner for Lean Signals.

    Loads V2 strategies from the catalog and runs them synchronously
    against a context dict (no subagent system needed).
    """

    def __init__(self):
        self._strategies = []
        self._loaded = False
        self._load()

    def _load(self):
        if self._loaded:
            return
        for entry in V2_STRATEGY_CATALOG:
            name = entry["name"]
            module_path = entry.get("module", "")
            class_name = entry.get("class_name", "")
            if not module_path or not class_name:
                continue
            try:
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name, None)
                if cls:
                    self._strategies.append((name, cls, entry))
            except Exception as e:
                logger.warning(
                    "V2 strategy load failed: %s from %s: %s",
                    entry["name"], entry.get("module", "?"), e,
                )
        self._loaded = True

    def run_all(self, context: dict) -> list[dict]:
        results = []
        import asyncio
        try:
            _loop = asyncio.get_running_loop()
            _created_loop = False
        except RuntimeError:
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
            _created_loop = True
        try:
            for name, cls, entry in self._strategies:
                try:
                    strategy = cls()
                    result = _loop.run_until_complete(strategy.execute(context))
                    if isinstance(result, dict):
                        direction = result.get("direction", "neutral")
                        confidence = result.get("confidence", 0)
                        reasons = result.get("reasons", [])
                        if isinstance(reasons, list):
                            reasoning = "; ".join(str(r) for r in reasons[:3])
                        else:
                            reasoning = str(reasons) if reasons else ""
                        results.append({
                            "name": name,
                            "direction": direction if direction in ("long", "short") else "neutral",
                            "confidence": float(confidence) if isinstance(confidence, (int, float)) else 0,
                            "source": "v2",
                            "reasoning": reasoning,
                        })
                except Exception as e:
                    logger.debug(
                        "V2 strategy %s failed on %s: %s",
                        name, context.get("ticker", "?"), e,
                    )
        finally:
            if _created_loop:
                _loop.close()
        return results
