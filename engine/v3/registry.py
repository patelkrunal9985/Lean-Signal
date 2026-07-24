from engine.v3.base import BaseV3Strategy, STRATEGY_FAMILIES
from utils.logger import get_logger

logger = get_logger("engine.v3.registry")

_INSTRUMENT_STRATEGIES = {
    "stock": [],
    "future": [],
    "option": [],
}

# Every V3 strategy mapped to its information family.
# Families represent independent data axes for confluence diversity checking.
STRATEGY_FAMILY_MAP: dict[str, str] = {
    # ── Stock strategies ──
    "premarket_gapper": "flow",
    "sector_rotation": "macro",
    "short_squeeze": "volatility",
    "insider_flow": "macro",
    "earnings_momentum": "macro",
    "dark_pool_proxy": "flow",
    "pairs_trading": "technical",
    # ── Futures strategies ──
    "order_imbalance": "flow",
    "cot_sentiment": "macro",
    "carry_yield": "macro",
    "calendar_spread": "macro",
    "vix_term_structure": "volatility",
    "momentum_cross": "technical",
    "volume_spike": "volume",
    "mean_reversion": "technical",
    "vwap_reversion": "technical",
    "high_low_breakout": "technical",
    "order_flow_burst": "flow",
    "delta_absorption": "flow",
    "profile_poison": "volume",
    "session_continuation": "technical",
    "gamma_pin": "options_micro",
    "order_book_velocity": "flow",
    "delta_divergence": "flow",
    "opening_range_breakout": "technical",
    "spread_reversion": "technical",
    "session_regime": "mtf",
    "vwap_anchored": "technical",
    "iceberg_detection": "flow",
    "time_of_day_momentum": "mtf",
    "gamma_flip": "options_micro",
    "volume_profile_decay": "volume",
    "cumulative_delta_flow": "flow",
    "vpin": "volume",
    "momentum_jerk": "technical",
    "mtf_core": "mtf",
    "fvg_liquidity_sweep": "flow",
    "order_flow_exhaustion": "flow",
    # ── Options strategies ──
    "gamma_exposure": "options_micro",
    "put_call_divergence": "options_macro",
    "iv_skew": "options_micro",
    "expected_vs_actual": "options_macro",
    "oi_concentration": "options_macro",
    "max_pain": "options_micro",
    "large_option_flow": "options_macro",
    "theta_decay": "options_micro",
    "iv_rv_spread": "volatility",
    "delta_positioning": "options_micro",
    "option_volume_flow": "options_macro",
    "gamma_flip_levels": "options_micro",
    "skew_term_structure": "options_micro",
    "earnings_vol_arbitrage": "volatility",
    "zero_dte_gamma": "options_micro",
    "opening_drive": "options_macro",
    "delta_hedging_imbalance": "options_micro",
    "vwap_option_flow": "options_macro",
    "call_put_wall_breakout": "options_micro",
    "unusual_whale_flow": "options_macro",
    "gamma_flip_acceleration": "options_micro",
    "strike_volume_surge": "options_macro",
    "sector_etf_option_rotation": "options_macro",
    "vix_spx_convexity": "volatility",
    "vanna_charm_flow": "options_micro",
    "prior_hl_magnetism": "options_macro",
    "oi_change_rate": "options_macro",
    "vol_smile_curvature": "options_micro",
    "delta_gamma_imbalance": "options_micro",
    "breadth_confirmation": "macro",
    "iv_rank_percentile": "volatility",
    "credit_spread_detector": "options_micro",
    "iron_condor_detector": "options_micro",
    "expiry_day_gamma": "options_micro",
    # ── SR Rejection strategies (Phase 2) ──
    "sr_rejection": "technical",
}


def _import_all():
    from engine.v3.stock.premarket_gapper import PremarketGapper
    from engine.v3.stock.sector_rotation import SectorRotation
    from engine.v3.stock.short_squeeze import ShortSqueeze
    from engine.v3.stock.insider_flow import InsiderFlow
    from engine.v3.stock.earnings_momentum import EarningsMomentum
    from engine.v3.stock.dark_pool_proxy import DarkPoolProxy
    from engine.v3.stock.pairs_trading import PairsTrading
    from engine.v3.futures.order_imbalance import OrderImbalance
    from engine.v3.futures.cot_sentiment import COTSentiment
    from engine.v3.futures.carry_yield import CarryYield
    from engine.v3.futures.calendar_spread import CalendarSpread
    from engine.v3.futures.vix_term_structure import VIXTermStructure
    from engine.v3.futures.momentum_cross import MomentumCross
    from engine.v3.futures.volume_spike import VolumeSpike
    from engine.v3.futures.mean_reversion import MeanReversion
    from engine.v3.futures.vwap_reversion import VWAPReversion
    from engine.v3.futures.high_low_breakout import HighLowBreakout
    from engine.v3.futures.order_flow_burst import OrderFlowBurst
    from engine.v3.futures.delta_absorption import DeltaAbsorption
    from engine.v3.futures.profile_poison import ProfilePoison
    from engine.v3.futures.session_continuation import SessionContinuation
    from engine.v3.futures.gamma_pin import GammaPin
    from engine.v3.futures.order_book_velocity import OrderBookVelocity
    from engine.v3.futures.delta_divergence import DeltaDivergence
    from engine.v3.futures.opening_range_breakout import OpeningRangeBreakout
    from engine.v3.futures.spread_reversion import SpreadReversion
    from engine.v3.futures.session_regime import SessionRegime
    from engine.v3.futures.vwap_anchored import VWAPAnchored
    from engine.v3.futures.iceberg_detection import IcebergDetection
    from engine.v3.futures.time_of_day_momentum import TimeOfDayMomentum
    from engine.v3.futures.gamma_flip import GammaFlip
    from engine.v3.futures.volume_profile_decay import VolumeProfileDecay
    from engine.v3.futures.cumulative_delta_flow import CumulativeDeltaFlow
    from engine.v3.futures.vpin import VPINStrategy
    from engine.v3.futures.momentum_jerk import MomentumJerk
    from engine.v3.futures.mtf_core import MTFCore
    from engine.v3.futures.fvg_liquidity_sweep import FVGLiquiditySweep
    from engine.v3.futures.order_flow_exhaustion import OrderFlowExhaustion
    from engine.v3.options.gamma_exposure import GammaExposure
    from engine.v3.options.put_call_divergence import PutCallDivergence
    from engine.v3.options.iv_skew import IVSkew
    from engine.v3.options.expected_vs_actual import ExpectedVsActual
    from engine.v3.options.oi_concentration import OIConcentration
    from engine.v3.options.max_pain import MaxPain
    from engine.v3.options.large_option_flow import LargeOptionFlow
    from engine.v3.options.theta_decay import ThetaDecay
    from engine.v3.options.iv_rv_spread import IVRVSpread
    from engine.v3.options.delta_positioning import DeltaPositioning
    from engine.v3.options.option_volume_flow import OptionVolumeFlow
    from engine.v3.options.gamma_flip_levels import GammaFlipLevels
    from engine.v3.options.skew_term_structure import SkewTermStructure
    from engine.v3.options.earnings_vol_arbitrage import EarningsVolArbitrage
    from engine.v3.options.zero_dte_gamma import ZeroDTEGamma
    from engine.v3.options.opening_drive import OpeningDrive
    from engine.v3.options.delta_hedging_imbalance import DeltaHedgingImbalance
    from engine.v3.options.vwap_option_flow import VWAPOptionFlow
    from engine.v3.options.call_put_wall_breakout import CallPutWallBreakout
    from engine.v3.options.unusual_whale_flow import UnusualWhaleFlow
    from engine.v3.options.gamma_flip_acceleration import GammaFlipAcceleration
    from engine.v3.options.strike_volume_surge import StrikeVolumeSurge
    from engine.v3.options.sector_etf_option_rotation import SectorETFOptionRotation
    from engine.v3.options.vix_spx_convexity import VIXSPXConvexityArbitrage
    from engine.v3.options.vanna_charm_flow import VannaCharmFlow
    from engine.v3.options.prior_hl_magnetism import PriorHLMagnetism
    from engine.v3.options.oi_change_rate import OIChangeRate
    from engine.v3.options.vol_smile_curvature import VolSmileCurvature
    from engine.v3.options.delta_gamma_imbalance import DeltaGammaImbalance
    from engine.v3.options.breadth_confirmation import BreadthConfirmation
    from engine.v3.options.iv_rank_percentile import IVRankPercentile
    from engine.v3.options.credit_spread_detector import CreditSpreadDetector
    from engine.v3.options.iron_condor_detector import IronCondorDetector
    from engine.v3.options.expiry_day_gamma import ExpiryDayGamma
    from engine.v3.stock.sr_rejection import SRRejectionStock
    from engine.v3.futures.sr_rejection import SRRejectionFutures
    all_strategies = [
        PremarketGapper(), SectorRotation(), ShortSqueeze(), InsiderFlow(),
        EarningsMomentum(), DarkPoolProxy(), PairsTrading(), OrderImbalance(), COTSentiment(),
        CarryYield(), CalendarSpread(), VIXTermStructure(), MomentumCross(), VolumeSpike(),
        MeanReversion(), VWAPReversion(), HighLowBreakout(), OrderFlowBurst(),
        DeltaAbsorption(), ProfilePoison(), SessionContinuation(), GammaPin(),
        OrderBookVelocity(), DeltaDivergence(), OpeningRangeBreakout(), SpreadReversion(),
        SessionRegime(), VWAPAnchored(), IcebergDetection(), TimeOfDayMomentum(),
        GammaFlip(), VolumeProfileDecay(),
        CumulativeDeltaFlow(), VPINStrategy(), MomentumJerk(),
        MTFCore(), FVGLiquiditySweep(), OrderFlowExhaustion(),
        GammaExposure(), PutCallDivergence(), IVSkew(), ExpectedVsActual(),
        OIConcentration(), MaxPain(), LargeOptionFlow(), ThetaDecay(), IVRVSpread(),
        DeltaPositioning(), OptionVolumeFlow(), GammaFlipLevels(), SkewTermStructure(),
        EarningsVolArbitrage(), ZeroDTEGamma(), OpeningDrive(), DeltaHedgingImbalance(),
        VWAPOptionFlow(), CallPutWallBreakout(), UnusualWhaleFlow(), GammaFlipAcceleration(),
        StrikeVolumeSurge(), SectorETFOptionRotation(), VIXSPXConvexityArbitrage(),
        VannaCharmFlow(), PriorHLMagnetism(), OIChangeRate(),
        VolSmileCurvature(), DeltaGammaImbalance(), BreadthConfirmation(),
        IVRankPercentile(), CreditSpreadDetector(), IronCondorDetector(), ExpiryDayGamma(),
        SRRejectionStock(), SRRejectionFutures(),
    ]
    for s in all_strategies:
        s.family = STRATEGY_FAMILY_MAP.get(s.name, "technical")
        for instr in s.applies_to:
            if instr in _INSTRUMENT_STRATEGIES:
                _INSTRUMENT_STRATEGIES[instr].append(s)


def get_strategies(instr_type: str) -> list[BaseV3Strategy]:
    if not _INSTRUMENT_STRATEGIES["stock"]:
        _import_all()
    return list(_INSTRUMENT_STRATEGIES.get(instr_type, []))


def get_selected_strategies(instr_type: str, names: list[str]) -> list[BaseV3Strategy]:
    all_s = get_strategies(instr_type)
    return [s for s in all_s if s.name in names]
