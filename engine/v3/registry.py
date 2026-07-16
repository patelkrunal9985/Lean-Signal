from engine.v3.base import BaseV3Strategy
from utils.logger import get_logger

logger = get_logger("engine.v3.registry")

_INSTRUMENT_STRATEGIES = {
    "stock": [],
    "future": [],
    "option": [],
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
    all_strategies = [
        PremarketGapper(), SectorRotation(), ShortSqueeze(), InsiderFlow(),
        EarningsMomentum(), DarkPoolProxy(), PairsTrading(), OrderImbalance(), COTSentiment(),
        CarryYield(), CalendarSpread(), VIXTermStructure(), MomentumCross(), VolumeSpike(),
        MeanReversion(), VWAPReversion(), HighLowBreakout(), OrderFlowBurst(),
        DeltaAbsorption(), ProfilePoison(), SessionContinuation(), GammaPin(),
        OrderBookVelocity(), DeltaDivergence(), OpeningRangeBreakout(), SpreadReversion(),
        SessionRegime(), VWAPAnchored(), IcebergDetection(), TimeOfDayMomentum(),
        GammaFlip(), VolumeProfileDecay(),
        GammaExposure(), PutCallDivergence(), IVSkew(), ExpectedVsActual(),
        OIConcentration(), MaxPain(), LargeOptionFlow(), ThetaDecay(), IVRVSpread(),
        DeltaPositioning(), OptionVolumeFlow(), GammaFlipLevels(), SkewTermStructure(),
        EarningsVolArbitrage(), ZeroDTEGamma(), OpeningDrive(), DeltaHedgingImbalance(),
        VWAPOptionFlow(), CallPutWallBreakout(), UnusualWhaleFlow(), GammaFlipAcceleration(),
        StrikeVolumeSurge(), SectorETFOptionRotation(), VIXSPXConvexityArbitrage(),
        VannaCharmFlow(),
    ]
    for s in all_strategies:
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
