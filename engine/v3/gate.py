import math
from datetime import datetime, timezone
from kronos.ml.posterior import StatisticalPosterior, StrategySignature
from kronos.risk.loss_tracker import LossTracker
from kronos.risk.correlation import compute_correlation, get_sector
from kronos.utils.logger import get_logger

logger = get_logger("kronos.v3.gate")

# Default gate thresholds — all overridable via settings dict passed to FiveLayerGate.
# Keys with 'gate_' prefix are read from settings at runtime.
SURVIVAL_GATE = {
    "gate_market_open_delay_min": 5,
    "gate_close_buffer_min": 5,
    "gate_atr_sweet_spot_min": 0.001,
    "gate_atr_sweet_spot_max": 0.050,
    "gate_atr_sweet_spot_max_option": 0.080,
    "gate_atr_bonus_min": 0.012,
    "gate_atr_bonus_max": 0.025,
    "gate_volume_ratio_min": 0.01,
    "gate_spread_pct_max": 0.005,
    "gate_futures_min_dollar_volume": 100_000_000,
    "gate_options_min_contract_volume": 10,
    "gate_options_spread_pct_max": 0.10,
}

DIRECTION_GATE = {
    "gate_min_tf_agreement": 1,
    "gate_tf_confidence_min": 0.20,
    "gate_fast_tf_veto_confidence": 0.5,
    "gate_regime_high_confidence": 0.7,
    "gate_regime_low_confidence": 0.20,
    "gate_directional_bias_threshold": 0.70,
    "gate_directional_bias_penalty": 0.85,
}

SIGNAL_GATE = {
    "gate_min_strategies_agree": 1,
    "gate_min_strategies_agree_option": 1,
    "gate_strategy_confidence_min": 0.12,
    "gate_strategy_confidence_min_option": 0.10,
    "gate_posterior_probability_min": 0.15,
    "gate_posterior_probability_min_option": 0.15,
    "gate_posterior_startup_min": 0.10,
    "gate_posterior_startup_min_option": 0.10,
    "gate_posterior_startup_trades": 10,
    "gate_min_confidence_stock": 0.25,
    "gate_min_confidence_future": 0.12,
    "gate_min_confidence_option": 0.10,
    "gate_bonus_4plus_strategies": 1.10,
    "gate_bonus_6plus_strategies": 1.20,
}

RISK_GATE = {
    "gate_correlation_max": 0.70,
    "gate_sector_concentration_max": 0.25,

    "gate_portfolio_losses_cooldown": 5,
    "gate_daily_loss_pct_halt": 0.03,
    "gate_daily_loss_pct_reduce": 0.02,
    "gate_drawdown_halt_pct": 0.50,
    "gate_drawdown_reduce_pct": 0.05,
    "gate_max_exposure_stock": 0.25,
    "gate_max_exposure_future": 0.30,
    "gate_max_exposure_option": 0.15,
    "gate_max_positions_per_day": 10,
}

EXECUTION_GATE = {
    "gate_slippage_max_atr": 0.3,
    "gate_slippage_multiplier": 1.5,
    "gate_size_aggressive_pct": 0.02,
    "gate_size_normal_pct": 0.01,
    "gate_size_conservative_pct": 0.005,
    "gate_size_exploratory_pct": 0.0025,
    "gate_max_price_movement_pct": 0.005,
    "gate_min_position_value": 100.0,
    "gate_max_position_value": 50000.0,
}

ALIGNMENT_MATRIX = {
    "strong_uptrend": {"long": "allowed", "short": "blocked"},
    "strong_downtrend": {"long": "blocked", "short": "allowed"},
    "ranging": {"long": "allowed", "short": "allowed"},
    "high_volatility": {"long": "allowed", "short": "allowed"},
}


def _current_et_minutes() -> int:
    from kronos.utils.time_utils import now_ny
    t = now_ny()
    return t.hour * 60 + t.minute


class FiveLayerGate:
    def __init__(self, settings: dict = None):
        self.posterior = StatisticalPosterior()
        self.loss_tracker = LossTracker()
        self.gate_metrics = {f"layer_{i}": {"passed": 0, "rejected": 0, "reasons": {}} for i in range(1, 6)}
        self.settings = settings or {}

    def _apply_settings(self, gate_dict: dict) -> dict:
        """Override gate defaults with any matching settings. All gate setting keys
        use 'gate_' prefix (e.g. 'gate_atr_sweet_spot_min') to avoid collision."""
        g = dict(gate_dict)
        for key in g:
            if key in self.settings:
                val = self.settings[key]
                val_type = type(g[key])
                try:
                    g[key] = val_type(val)
                except (ValueError, TypeError):
                    pass
        return g

    def _get_survival_gate(self) -> dict:
        return self._apply_settings(SURVIVAL_GATE)

    def _get_direction_gate(self) -> dict:
        return self._apply_settings(DIRECTION_GATE)

    def _get_signal_gate(self) -> dict:
        return self._apply_settings(SIGNAL_GATE)

    def _get_risk_gate(self) -> dict:
        return self._apply_settings(RISK_GATE)

    def _get_execution_gate(self) -> dict:
        return self._apply_settings(EXECUTION_GATE)

    def process_ticker(self, ticker_data: dict, portfolio_state: dict, active_strategies: list[dict], regime: dict) -> dict:
        ticker = ticker_data.get("ticker", "")
        instr_type = ticker_data.get("instrument_type", "stock")
        ohlcv = ticker_data.get("ohlcv", [])
        indicators = ticker_data.get("indicators", {})

        sg = self._get_survival_gate()
        survival = self._check_survival_gate(ticker, ohlcv, indicators, instr_type, sg)
        if not survival["passed"]:
            self._record_rejection(1, survival["reason"])
            return {"ticker": ticker, "action": "skip", "reason": survival["reason"]}
        self.gate_metrics["layer_1"]["passed"] += 1

        dg = self._get_direction_gate()
        direction_result = self._check_direction_gate(active_strategies, regime, ohlcv, indicators, dg, instr_type)
        if not direction_result["passed"]:
            self._record_rejection(2, direction_result["reason"])
            return {"ticker": ticker, "action": "skip", "reason": direction_result["reason"]}
        self.gate_metrics["layer_2"]["passed"] += 1
        signal_direction = direction_result["direction"]
        confidence_mult = direction_result.get("confidence_multiplier", 1.0)

        signal_g = self._get_signal_gate()
        signal_result = self._check_signal_gate(active_strategies, signal_direction, regime, instr_type, signal_g, confidence_mult)
        if not signal_result["passed"]:
            self._record_rejection(3, signal_result["reason"])
            return {"ticker": ticker, "action": "skip", "reason": signal_result["reason"]}
        self.gate_metrics["layer_3"]["passed"] += 1
        adjusted_confidence = signal_result["confidence"]
        posterior = signal_result.get("posterior", 0.5)

        rg = self._get_risk_gate()
        risk_result = self._check_risk_gate(ticker, signal_direction, portfolio_state, rg)
        if not risk_result["passed"]:
            self._record_rejection(4, risk_result["reason"])
            return {"ticker": ticker, "action": "skip", "reason": risk_result["reason"]}
        self.gate_metrics["layer_4"]["passed"] += 1
        size_multipliers = risk_result.get("size_multipliers", [1.0])

        eg = self._get_execution_gate()
        position_size = self._calculate_position_size(adjusted_confidence, portfolio_state.get("account_value", 50000), size_multipliers, regime, eg)

        execution_result = self._check_execution_gate(ticker_data, signal_direction, position_size, eg)
        if not execution_result["passed"]:
            self._record_rejection(5, execution_result["reason"])
            return {"ticker": ticker, "action": "skip", "reason": execution_result["reason"]}
        self.gate_metrics["layer_5"]["passed"] += 1

        # ── Option action: buy vs sell (applies to options only) ──
        option_action = "buy"
        if instr_type == "option":
            dte = ticker_data.get("dte", ticker_data.get("indicators", {}).get("option_dte", 14))
            iv = ticker_data.get("iv", ticker_data.get("indicators", {}).get("iv", 0))
            hv = ticker_data.get("hv_10", ticker_data.get("indicators", {}).get("hv_10", 0))
            option_action = self._decide_option_action(active_strategies, regime, signal_direction, dte, iv, hv)

        return {
            "ticker": ticker,
            "action": "trade",
            "direction": signal_direction,
            "confidence": adjusted_confidence,
            "size": position_size,
            "size_pct": position_size / max(portfolio_state.get("account_value", 50000), 1),
            "posterior": posterior,
            "regime": regime.get("primary_regime", "ranging"),
            "entry_price": ticker_data.get("current_price", 0),
            "stop_loss": 0.0,  # computed downstream by _finalize_signal()
            "take_profit": 0.0,  # computed downstream by _finalize_signal()
            "strategy_votes": active_strategies,
            "option_action": option_action,
        }

    def _record_rejection(self, layer: int, reason: str):
        self.gate_metrics[f"layer_{layer}"]["rejected"] += 1
        r = self.gate_metrics[f"layer_{layer}"]["reasons"]
        r[reason] = r.get(reason, 0) + 1

    def _check_survival_gate(self, ticker: str, ohlcv: list, indicators: dict, instr_type: str, sg: dict = None) -> dict:
        if sg is None:
            sg = self._get_survival_gate()
        if not ohlcv:
            return {"passed": False, "reason": "no_data"}
        close = ohlcv[-1].get("close", 0) if isinstance(ohlcv[-1], dict) else ohlcv[-1]
        atr = indicators.get("atr_14", 0)
        atr_pct = atr / close if close > 0 else 0
        now_et = _current_et_minutes()
        open_delay = sg["gate_market_open_delay_min"]
        close_buf = sg["gate_close_buffer_min"]
        market_open_et = 9 * 60 + 30
        market_close_et = 16 * 60
        # Futures trade nearly 24/5 on CME Globex — skip equity market hours check
        if instr_type != "future":
            if now_et < market_open_et + open_delay:
                return {"passed": False, "reason": "market_not_yet_open"}
            if now_et >= market_close_et - close_buf:
                return {"passed": False, "reason": "too_close_to_close"}
        _atr_max = sg["gate_atr_sweet_spot_max"]
        if instr_type == "option":
            _atr_max = sg.get("gate_atr_sweet_spot_max_option", 0.080)
        if atr_pct < sg["gate_atr_sweet_spot_min"]:
            return {"passed": False, "reason": f"atr_too_low_{atr_pct:.4f}"}
        if atr_pct > _atr_max:
            return {"passed": False, "reason": f"atr_too_high_{atr_pct:.4f}"}
        if isinstance(ohlcv[-1], dict):
            # Futures: skip volume ratio check — yfinance often reports volume=0
            # for futures tickers, even though real exchange volume exists.
            # Fall through and only check spread below.
            if instr_type != "future":
                volume = ohlcv[-1].get("volume", 0)
                recent_vols = [b.get("volume", 0) for b in ohlcv[-20:]] if len(ohlcv) >= 20 else [volume]
                avg_vol = sum(recent_vols) / max(len(recent_vols), 1)
                vol_ratio = volume / max(avg_vol, 1)
                if vol_ratio < sg["gate_volume_ratio_min"]:
                    return {"passed": False, "reason": f"volume_too_low_{vol_ratio:.2f}x"}
        if instr_type == "option":
            opt_vol = indicators.get("option_contract_volume", -1)
            min_opt_vol = sg.get("gate_options_min_contract_volume", 100)
            if 0 <= opt_vol < min_opt_vol:
                # After-hours/weekend: volume=0, fall back to open interest
                opt_oi = indicators.get("option_chain_oi", 0)
                min_oi = sg.get("gate_options_min_oi_fallback", 1000)
                if opt_oi < min_oi:
                    return {"passed": False, "reason": f"option_volume_too_low_{opt_vol}_lt_{min_opt_vol}_oi_{opt_oi}"}
                # OI is healthy — allow through with volume=0 (after-hours)
                logger.debug(f"Survival gate: {ticker} volume=0 but OI={opt_oi} >= {min_oi} — allowing")
        ds = indicators.get("data_source", "")
        if ds and ds != "iex_depth":
            logger.debug(f"Survival gate: {ticker} data_source={ds} (not IEX depth)")
        bid = indicators.get("bid", 0)
        ask = indicators.get("ask", 0)
        if bid > 0 and ask > 0:
            spread_pct = (ask - bid) / close
            if spread_pct > sg["gate_spread_pct_max"]:
                return {"passed": False, "reason": f"spread_too_wide_{spread_pct:.5f}"}
        if instr_type == "option":
            opt_bid = indicators.get("option_bid", 0)
            opt_ask = indicators.get("option_ask", 0)
            if opt_bid > 0 and opt_ask > 0:
                opt_spread_pct = (opt_ask - opt_bid) / max(opt_bid, 0.01)
                max_opt_spread = sg.get("gate_options_spread_pct_max", 0.10)
                if opt_spread_pct > max_opt_spread:
                    return {"passed": False, "reason": f"option_spread_too_wide_{opt_spread_pct:.2%}"}
        return {"passed": True, "atr_pct": atr_pct}

    def _check_direction_gate(self, active_strategies: list[dict], regime: dict, ohlcv: list, indicators: dict = None, dg: dict = None, instr_type: str = "stock") -> dict:
        if dg is None:
            dg = self._get_direction_gate()
        regime_type = regime.get("primary_regime", "ranging")
        regime_conf = regime.get("confidence", 0.5)
        long_weight = sum(s.get("confidence", 0) * 0.5 for s in active_strategies if s.get("direction") == "long")
        short_weight = sum(s.get("confidence", 0) * 0.5 for s in active_strategies if s.get("direction") == "short")
        total_weight = long_weight + short_weight
        signal_dir = "long" if long_weight >= short_weight else "short"
        if total_weight == 0:
            return {"passed": False, "reason": "no_strategy_direction"}
        # V3 options signal: skip regime alignment check — option strategies
        # already encode the underlying's trend in their direction (puts on
        # downtrend, calls on uptrend). instr_type may be "future" for futures
        # options (survival gate bypasses market hours), so we check "option"
        # as well since V3 opt scan passes instrument_type="option".
        if instr_type not in ("option", "future_with_option") and ALIGNMENT_MATRIX.get(regime_type, {}).get(signal_dir, "allowed") == "blocked":
            return {"passed": False, "reason": f"regime_blocks_{signal_dir}_in_{regime_type}"}
        confidence_mult = 1.0
        if regime_conf < dg["gate_regime_low_confidence"]:
            confidence_mult = 0.85

        # ── Trend awareness checks ──
        # Options skip: V3 option strategies already encode the underlying's
        # trend in their direction (puts on downtrend, calls on uptrend).
        if indicators is not None and ohlcv and instr_type != "option":
            closes = [c["close"] for c in ohlcv]
            current_price = closes[-1] if closes else 0
            sma20 = indicators.get("sma_20", 0)
            rsi = indicators.get("rsi", indicators.get("rsi_14", 50))
            bb_pct = indicators.get("bb_pct", indicators.get("bb_percent", 0.5))
            macd_raw = indicators.get("macd", 0)
            if isinstance(macd_raw, dict):
                macd = macd_raw.get("macd", macd_raw.get("value", 0))
            else:
                macd = macd_raw if isinstance(macd_raw, (int, float)) else 0
            macd_hist = indicators.get("macd_hist", indicators.get("macd_histogram", 0))
            if isinstance(macd_hist, dict):
                macd_hist = macd_hist.get("hist", macd_hist.get("histogram", 0))
            of_bias = indicators.get("order_flow_bias", "neutral")
            vw_mom = indicators.get("vw_momentum_signal", 0)

            trend_reasons = []

            # Check 1: Strong SMA20 trend (price >3% above SMA → block shorts; <3% below → block longs)
            if sma20 > 0 and current_price > 0:
                price_vs_sma20 = (current_price - sma20) / sma20
                if price_vs_sma20 > 0.03 and signal_dir == "short":
                    return {"passed": False, "reason": f"price_+{price_vs_sma20:.1%}_above_sma20_blocks_short"}
                if price_vs_sma20 < -0.03 and signal_dir == "long":
                    return {"passed": False, "reason": f"price_{price_vs_sma20:.1%}_below_sma20_blocks_long"}

                # Check 2: Moderate SMA20 trend (1.5%+) → penalize counter-trade
                if price_vs_sma20 > 0.015 and signal_dir == "short":
                    confidence_mult *= 0.50
                    trend_reasons.append(f"price_+{price_vs_sma20:.1%}_above_sma20")
                if price_vs_sma20 < -0.015 and signal_dir == "long":
                    confidence_mult *= 0.50
                    trend_reasons.append(f"price_{price_vs_sma20:.1%}_below_sma20")

            # Check 3: Oversold/overbought RSI + BB protection
            if rsi < 35 and bb_pct < 0.25 and signal_dir == "short":
                confidence_mult *= 0.40
                trend_reasons.append(f"oversold_rsi={rsi}_bb={bb_pct:.2f}")
            if rsi > 65 and bb_pct > 0.75 and signal_dir == "long":
                confidence_mult *= 0.40
                trend_reasons.append(f"overbought_rsi={rsi}_bb={bb_pct:.2f}")

            # Check 4: Contradictory signal — order flow + vw momentum oppose direction
            if of_bias == "bullish" and vw_mom > 0.6 and signal_dir == "short":
                confidence_mult *= 0.60
                trend_reasons.append(f"bullish_flow+vw={vw_mom:.2f}_vs_short")
            if of_bias == "bearish" and vw_mom < -0.6 and signal_dir == "long":
                confidence_mult *= 0.60
                trend_reasons.append(f"bearish_flow+vw={vw_mom:.2f}_vs_long")

            # Check 5: MACD trend — don't short when MACD is strong positive in uptrend
            if macd > macd_hist and macd > 0 and sma20 > 0 and current_price > sma20 and signal_dir == "short":
                confidence_mult *= 0.55
                trend_reasons.append(f"macd_bullish+price_above_sma20")

            if confidence_mult < 0.3:
                return {"passed": False, "reason": "trend_awareness_block_" + "_".join(trend_reasons[-2:])}

        # ── Directional bias cap: penalize signals where >70% of strategies
        #     agree on the same direction (prevents entire portfolio going one way)
        dominant_weight = max(long_weight, short_weight)
        bias_ratio = dominant_weight / total_weight if total_weight > 0 else 0.5
        if bias_ratio > dg.get("gate_directional_bias_threshold", 0.70):
            penalty = dg.get("gate_directional_bias_penalty", 0.85)
            confidence_mult *= penalty
        return {"passed": True, "direction": signal_dir, "confidence_multiplier": confidence_mult, "bias_ratio": bias_ratio}

    def _check_signal_gate(self, active_strategies: list[dict], signal_direction: str, regime: dict, instr_type: str, sg: dict = None, confidence_mult: float = 1.0) -> dict:
        if sg is None:
            sg = self._get_signal_gate()
        min_conf_key = f"gate_min_confidence_{instr_type}"
        strat_min = sg.get(min_conf_key, sg.get("gate_min_confidence_stock", 0.50))
        strat_conf_key = f"gate_strategy_confidence_min_{instr_type}"
        strat_conf_min = sg.get(strat_conf_key, sg.get("gate_strategy_confidence_min", 0.12))
        # For options: don't filter by direction since option strategies have inherent biases
        # (theta_decay=short, iv_rv_spread=long when IV>RV, etc.)
        if instr_type == "option":
            votes = [s for s in active_strategies if s.get("confidence", 0) >= strat_conf_min]
        else:
            votes = [s for s in active_strategies if s.get("direction") == signal_direction and s.get("confidence", 0) >= strat_conf_min]
        strat_agree_key = f"gate_min_strategies_agree_{instr_type}"
        min_agree = sg.get(strat_agree_key, sg.get("gate_min_strategies_agree", 2))
        if len(votes) < min_agree:
            return {"passed": False, "reason": f"only_{len(votes)}_strategies_agree"}
        bonus = 1.0
        if len(votes) >= 6:
            bonus = sg.get("gate_bonus_6plus_strategies", 1.20)
        elif len(votes) >= 4:
            bonus = sg.get("gate_bonus_4plus_strategies", 1.10)
        regime_type = regime.get("primary_regime", "ranging")
        sig = StrategySignature(
            regime=regime_type,
            instrument_type=instr_type,
            active_strategies=tuple(s.get("strategy", "") for s in votes),
            directions=tuple(s.get("direction", signal_direction) for s in votes),
            confidences=tuple(s.get("confidence", 0) for s in votes),
            timeframe_hash="",
            direction=signal_direction,
        )
        posterior = self.posterior.get_probability(sig)
        # ── Startup penalty: with weak priors [1,4], initial posterior is ~0.20.
        #     Require 0.35 until 50+ real trades back the posterior statistics.
        #     Options have higher bar: 0.35 (0.25 startup) vs stocks/futures 0.15 (0.10 startup).
        post_key = f"gate_posterior_probability_min_{instr_type}"
        posterior_min = sg.get(post_key, sg.get("gate_posterior_probability_min", 0.20))
        startup_post_key = f"gate_posterior_startup_min_{instr_type}"
        startup_trades_key = f"gate_posterior_startup_trades_{instr_type}"
        if self.posterior.total_signals < sg.get(startup_trades_key, sg.get("gate_posterior_startup_trades", 50)):
            posterior_min = sg.get(startup_post_key, sg.get("gate_posterior_startup_min", 0.15))
        if posterior < posterior_min:
            return {"passed": False, "reason": f"posterior_too_low_{posterior:.3f}"}
        raw_conf = sum(v.get("confidence", 0) for v in votes) / max(len(votes), 1)
        adjusted = raw_conf * confidence_mult * bonus * (0.5 + posterior * 0.5)
        if adjusted < sg.get(min_conf_key, 0.50):
            return {"passed": False, "reason": f"confidence_too_low_{adjusted:.3f}"}
        return {"passed": True, "confidence": adjusted, "posterior": posterior}

    def _check_risk_gate(self, ticker: str, signal_direction: str, portfolio_state: dict, rg: dict = None) -> dict:
        if rg is None:
            rg = self._get_risk_gate()
        multipliers = [1.0]
        positions = portfolio_state.get("positions", [])
        for pos in positions:
            if pos.get("direction") == signal_direction:
                pos_ticker = pos.get("ticker", "")
                if pos_ticker and pos_ticker != ticker:
                    corr = compute_correlation(
                        portfolio_state.get("prices", {}).get(ticker, []),
                        portfolio_state.get("prices", {}).get(pos_ticker, []),
                    )
                    if abs(corr) > rg.get("gate_correlation_max", 0.70):
                        return {"passed": False, "reason": f"correlated_{pos_ticker}_{corr:.2f}"}
        sector = get_sector(ticker)
        if sector:
            sec_exposure = portfolio_state.get("sector_exposure", {}).get(sector, 0)
            new_pos_pct = 0.02
            if sec_exposure + new_pos_pct > rg.get("gate_sector_concentration_max", 0.25):
                return {"passed": False, "reason": f"sector_excess_{sector}"}
        # ── Portfolio-level consecutive loss halt ──
        portfolio_losses = self.loss_tracker.get_portfolio_consecutive_losses()
        if portfolio_losses >= rg.get("gate_portfolio_losses_cooldown", 5):
            return {"passed": False, "reason": f"{portfolio_losses}_portfolio_consecutive_losses"}
        if portfolio_losses >= 3:
            multipliers.append(0.50)
        account_value = portfolio_state.get("account_value", 50000)
        daily_loss_pct = self.loss_tracker.get_daily_loss_pct(account_value)
        if daily_loss_pct > rg.get("gate_daily_loss_pct_halt", 0.03):
            return {"passed": False, "reason": "daily_loss_halt"}
        if daily_loss_pct > rg.get("gate_daily_loss_pct_reduce", 0.02):
            multipliers.append(0.50)
        drawdown = portfolio_state.get("drawdown_pct", 0)
        if drawdown > rg.get("gate_drawdown_halt_pct", 0.10):
            return {"passed": False, "reason": "drawdown_halt"}
        if drawdown > rg.get("gate_drawdown_reduce_pct", 0.05):
            multipliers.append(0.50)
        daily_trades = self.loss_tracker.daily_trades
        if daily_trades >= rg.get("gate_max_positions_per_day", 10):
            return {"passed": False, "reason": "max_daily_trades"}
        return {"passed": True, "size_multipliers": multipliers}

    def _check_execution_gate(self, ticker_data: dict, signal_direction: str, position_size: float, eg: dict = None) -> dict:
        if eg is None:
            eg = self._get_execution_gate()
        ds = ticker_data.get("data_source", "")
        if ds and ds != "iex_depth":
            logger.debug(f"Execution gate: {ticker_data.get('ticker', '')} data_source={ds} (not IEX depth)")
        spread = ticker_data.get("spread", 0)
        current_price = ticker_data.get("current_price", 0)
        atr = ticker_data.get("indicators", {}).get("atr_14", 0)
        if spread > 0 and atr > 0 and current_price > 0:
            slippage = spread * eg.get("gate_slippage_multiplier", 1.5)
            if slippage > atr * eg.get("gate_slippage_max_atr", 0.5):
                return {"passed": False, "reason": f"slippage_too_high_{slippage:.4f}"}
        entry = ticker_data.get("entry_price", current_price)
        if entry > 0 and current_price > 0:
            if signal_direction == "long" and current_price > entry * (1 + eg.get("gate_max_price_movement_pct", 0.002)):
                return {"passed": False, "reason": "price_moved_against_long"}
            if signal_direction == "short" and current_price < entry * (1 - eg.get("gate_max_price_movement_pct", 0.002)):
                return {"passed": False, "reason": "price_moved_against_short"}
        return {"passed": True, "entry_price": current_price}

    def _decide_option_action(self, active_strategies: list[dict], regime: dict, signal_direction: str, dte: int = 14, iv: float = 0.0, hv: float = 0.0) -> str:
        """Decide whether to buy or sell options based on IV rank, regime, and DTE.

        Decision logic:
        - High IV (IV rank > 70%) → sell (premium expensive, collect theta)
        - Strong trend + low IV → buy (cheap directional exposure)
        - Short DTE (0-7) → sell (theta accelerates, don't buy gamma near expiry)
        - Ranging market → sell (collect theta in sideways markets)

        Conservative: only sell puts (cash-secured). Naked sell-calls deferred to phase 2.
        """
        regime_type = regime.get("primary_regime", "ranging")

        # Count strategy votes for buy vs sell
        buy_votes = 0
        sell_votes = 0
        for s in active_strategies:
            act = s.get("action", "")
            if act == "sell":
                sell_votes += s.get("confidence", 0)
            elif act == "buy":
                buy_votes += s.get("confidence", 0)

        # Strategy consensus: if strategies explicitly vote sell, follow them
        # Threshold is configurable via gate_option_action_ratio setting
        action_ratio = float(self.settings.get('gate_option_action_ratio', 1.5))
        if sell_votes > buy_votes * action_ratio:
            return "sell"
        if buy_votes > sell_votes * action_ratio:
            return "buy"

        # IV/HV spread: when IV is much higher than HV, options are overpriced → sell
        if iv > 0 and hv > 0:
            iv_hv_spread_pct = (iv - hv) / max(hv, 0.01)
            if iv_hv_spread_pct > 0.15:
                return "sell"
            if iv_hv_spread_pct < -0.10:
                return "buy"

        # Short DTE: theta accelerates in final days → sell, don't buy gamma near expiry
        # Use 7-day threshold (was 4) to capture weekly-expiry Friday trades
        if 0 <= dte <= 7:
            return "sell"

        # Ranging regime: sell options to collect theta in sideways markets
        if regime_type in ("ranging",):
            return "sell"

        # Strong trend regimes: buy for directional exposure
        if regime_type in ("strong_uptrend", "strong_downtrend", "uptrend", "downtrend"):
            return "buy"

        return "buy"  # default: buy (limited risk)

    def _calculate_position_size(self, confidence: float, account_value: float, multipliers: list[float], regime: dict, eg: dict = None) -> float:
        if eg is None:
            eg = self._get_execution_gate()
        if confidence >= 0.80:
            base_pct = eg.get("gate_size_aggressive_pct", 0.02)
        elif confidence >= 0.65:
            base_pct = eg.get("gate_size_normal_pct", 0.01)
        elif confidence >= 0.50:
            base_pct = eg.get("gate_size_conservative_pct", 0.005)
        else:
            base_pct = eg.get("gate_size_exploratory_pct", 0.0025)
        regime_mult = {"strong_uptrend": 0.80, "strong_downtrend": 0.70, "ranging": 1.00, "high_volatility": 0.50}.get(regime.get("primary_regime", "ranging"), 1.0)
        all_mults = multipliers + [regime_mult]
        final_mult = 1.0
        for m in all_mults:
            final_mult *= m
        size = account_value * base_pct * final_mult
        return max(eg.get("gate_min_position_value", 100.0), min(size, eg.get("gate_max_position_value", 50000.0)))

    def record_trade_outcome(self, ticker: str, pnl: float, strategy: str = ""):
        self.loss_tracker.record_trade(ticker, pnl, strategy)

    def reset_daily(self):
        self.loss_tracker.reset_daily()

    def get_metrics(self) -> dict:
        return self.gate_metrics
