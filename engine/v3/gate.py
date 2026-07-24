"""
Signal Quality Gate — Pure Signal Generation Filter.

Four stateless layers:
  1. INTEGRITY    — Data source is IBKR? Fresh? Complete? Tradeable?
  2. ALIGNMENT    — Signal direction vs regime, trend, COT?
  3. CONVICTION   — Enough strategies agree? Confidence above threshold?
  4. PLATINUM     — Conviction tier = platinum? MTF aligned? No macro conflicts?

No portfolio. No risk management. No position sizing.
Just: is this a high-quality trade idea?
"""
from typing import Any
from datetime import datetime, timedelta
from utils.logger import get_logger

logger = get_logger("engine.v3.gate")


# ═══════════════════════════════════════════════════════════════
# Layer 1: Integrity Gate
# ═══════════════════════════════════════════════════════════════
INTEGRITY_GATE = {
    "gate_market_open_delay_min": 5,
    "gate_close_buffer_min": 5,
    "gate_atr_sweet_spot_min": 0.0,
    "gate_atr_sweet_spot_max": 0.050,
    "gate_atr_sweet_spot_max_option": 0.080,
    "gate_volume_ratio_min": 0.01,
    "gate_spread_pct_max": 0.005,
    "gate_options_min_contract_volume": 10,
    "gate_options_spread_pct_max": 0.10,
    # ── New: data quality ──
    "gate_max_price_age_seconds": 5.0,
    "gate_max_option_age_seconds": 60.0,
}

# ═══════════════════════════════════════════════════════════════
# Layer 2: Alignment Gate
# ═══════════════════════════════════════════════════════════════
ALIGNMENT_GATE = {
    "gate_regime_high_confidence": 0.7,
    "gate_regime_low_confidence": 0.20,
    "gate_directional_bias_threshold": 0.70,
    "gate_directional_bias_penalty": 0.85,
    # ── New: COT alignment ──
    "gate_cot_counter_penalty": 0.70,
}

ALIGNMENT_MATRIX: dict[str, dict[str, str]] = {
    "strong_uptrend": {"long": "allowed", "short": "blocked"},
    "strong_downtrend": {"long": "blocked", "short": "allowed"},
    "ranging": {"long": "allowed", "short": "allowed"},
    "high_volatility": {"long": "allowed", "short": "allowed"},
}

# ═══════════════════════════════════════════════════════════════
# Layer 3: Conviction Gate
# ═══════════════════════════════════════════════════════════════
CONVICTION_GATE = {
    "gate_min_strategies_agree": 1,
    "gate_min_strategies_agree_option": 3,
    "gate_strategy_confidence_min": 0.12,
    "gate_strategy_confidence_min_option": 0.20,
    "gate_min_confidence_stock": 0.25,
    "gate_min_confidence_future": 0.12,
    "gate_min_confidence_option": 0.20,
    "gate_bonus_4plus_strategies": 1.10,
    "gate_bonus_6plus_strategies": 1.20,
}

# ═══════════════════════════════════════════════════════════════
# Layer 4: Platinum Gate — 95% target filters
# ═══════════════════════════════════════════════════════════════
PLATINUM_GATE = {
    "gate_require_platinum_tier": False,
    "gate_require_mtf_alignment": False,
    "gate_require_regime_alignment": False,
    "gate_block_counter_trend": False,
}

# Macro economic calendar — high-impact events to avoid
MACRO_EVENTS = {
    "FOMC": ["fomc", "federal reserve", "fed meeting", "fed decision", "interest rate"],
    "NFP": ["nonfarm", "nfp", "payroll", "jobs report", "employment"],
    "CPI": ["cpi", "consumer price", "inflation"],
    "PPI": ["ppi", "producer price"],
    "GDP": ["gdp", "gross domestic product"],
    "ISM": ["ism", "pmi", "manufacturing index", "services index"],
    "RETAIL": ["retail sales"],
    "UNEMPLOYMENT": ["unemployment", "jobless claims"],
    "CONSUMER_CONFIDENCE": ["consumer confidence", "confidence index"],
}

# Correlation conflict pairs — if both move opposite directions, block
CORRELATION_CONFLICTS = {
    "ES=F": ["NQ=F", "RTY=F", "YM=F"],
    "NQ=F": ["ES=F", "RTY=F", "YM=F"],
    "RTY=F": ["ES=F", "NQ=F", "YM=F"],
    "YM=F": ["ES=F", "NQ=F", "RTY=F"],
    "GC=F": ["CL=F", "SI=F"],
    "CL=F": ["GC=F"],
}

# Volume/liquidity minimums per instrument type
VOLUME_MINIMUMS = {
    "stock": 500000,      # avg daily volume
    "future": 2000,       # contracts (scaled to 300 during Globex via get_globex_volume_scale)
    "option": 500,        # contracts
}


def _current_et_minutes() -> int:
    from utils.time_utils import now_ny
    t = now_ny()
    if t.weekday() >= 5:
        return 9999
    return t.hour * 60 + t.minute


def _is_macro_event_window(ticker: str, ticker_data: dict) -> tuple[bool, str]:
    """Check if we're within 30 minutes of a high-impact macro event."""
    news = ticker_data.get("news", [])
    now = datetime.utcnow()
    for item in news:
        headline = (item.get("headline", "") or "").lower()
        for event_type, keywords in MACRO_EVENTS.items():
            if any(kw in headline for kw in keywords):
                # Could add timestamp parsing here to check if event is within 30 min
                return True, f"macro_event_{event_type}"
    return False, ""


def _check_correlation_conflict(ticker: str, signal_dir: str, ticker_data: dict) -> tuple[bool, str]:
    """Check if correlated instruments have active opposing signals.

    Queries the signal persistence engine for active directions of correlated
    tickers. If ES is LONG and NQ is SHORT/ACTIVE in the same cycle, flag
    both with reduced confidence.
    """
    conflicts = CORRELATION_CONFLICTS.get(ticker, [])
    if not conflicts:
        return False, ""

    if signal_dir == "neutral":
        return False, ""

    try:
        from engine.signal_persistence import get_ticker_state
    except ImportError:
        return False, ""

    opposite_count = 0
    conflict_tickers = []
    for ct in conflicts:
        ct_state = get_ticker_state(ct)
        ct_dir = ct_state.get("active_direction", "neutral")
        ct_sig_state = ct_state.get("state", "none")

        # Only count active/confirmed opposing signals (not watching/pending noise)
        if ct_sig_state in ("active", "confirmed") and ct_dir in ("long", "short"):
            if ct_dir != signal_dir:
                opposite_count += 1
                conflict_tickers.append(f"{ct}={ct_dir}")

    if opposite_count >= 2:
        return True, f"correlation_conflict_{opposite_count}_opposing_{','.join(conflict_tickers)}"
    if opposite_count >= 1:
        # Single conflict: reduce confidence via correlation_info, don't block
        return False, f"correlation_warning_{conflict_tickers[0]}"

    return False, ""


def _check_volume_liquidity(ticker: str, ticker_data: dict, instr_type: str) -> tuple[bool, str]:
    """Check if there's sufficient volume/liquidity for the trade.

    Futures volume minimums are scaled down during Globex (after-hours)
    using get_globex_volume_scale() from time_of_day (default: 0.15x).
    Globex futures volume is structurally 5-15% of RTH — filtering at
    RTH thresholds would block all valid after-hours signals.
    """
    min_vol = VOLUME_MINIMUMS.get(instr_type, 0)
    if min_vol <= 0:
        return True, ""

    # ── Globex volume scaling for futures ──
    if instr_type == "future":
        try:
            from engine.time_of_day import get_globex_volume_scale
            min_vol = int(min_vol * get_globex_volume_scale())
        except ImportError:
            pass

    indicators = ticker_data.get("indicators", {})
    ohlcv = ticker_data.get("ohlcv", [])
    
    if instr_type == "future":
        # Use recent volume from ohlcv
        if ohlcv and len(ohlcv) > 0:
            last_bar = ohlcv[-1]
            vol = last_bar.get("volume", 0) if isinstance(last_bar, dict) else 0
            if vol < min_vol:
                return False, f"volume_too_low_{vol}_lt_{min_vol}"
    elif instr_type == "option":
        opt_vol = indicators.get("option_contract_volume", 0)
        if opt_vol < min_vol:
            return False, f"option_volume_too_low_{opt_vol}_lt_{min_vol}"
    elif instr_type == "stock":
        if ohlcv and len(ohlcv) > 0:
            last_bar = ohlcv[-1]
            vol = last_bar.get("volume", 0) if isinstance(last_bar, dict) else 0
            if vol < min_vol:
                return False, f"volume_too_low_{vol}_lt_{min_vol}"
    return True, ""


def _check_news_sentiment(ticker_data: dict) -> tuple[bool, str]:
    """Check if news sentiment strongly contradicts the signal direction."""
    news = ticker_data.get("news", [])
    if not news:
        return True, ""
    
    sentiment_score = 0.0
    count = 0
    for item in news:
        s = item.get("sentiment", 0)
        if isinstance(s, (int, float)):
            sentiment_score += s
            count += 1
    
    if count == 0:
        return True, ""
    
    avg_sentiment = sentiment_score / count
    # Strong negative news = potential short, strong positive = potential long
    # This is a soft filter - just logging for now
    return True, f"news_sentiment_{avg_sentiment:.2f}"


# Time-of-day reason constants (avoid fragile string matching)
TOD_REASON_MIDDAY_REDUCED = "midday_reduced_conviction"


class SignalQualityGate:
    """Stateless 4-layer quality filter for trade signals.

    Usage:
        gate = SignalQualityGate()
        result = gate.evaluate(ticker_data, signal_direction, confidence,
                               active_strategies, regime, consensus_meta)
        if result["passed"]:
            signal = result  # contains direction, confidence, reasoning
    """

    def __init__(self, settings: dict = None):
        self.settings = settings or {}

    def _apply_settings(self, gate_dict: dict) -> dict:
        g = dict(gate_dict)
        for key in g:
            if key in self.settings:
                try:
                    g[key] = type(g[key])(self.settings[key])
                except (ValueError, TypeError):
                    pass
        return g

    def evaluate(
        self,
        ticker_data: dict,
        signal_direction: str,
        signal_confidence: float,
        active_strategies: list[dict],
        regime: dict,
        consensus_meta: dict = None,
    ) -> dict:
        """Run the signal through all 3 quality layers.

        Returns dict with at minimum:
            {passed, ticker, direction, confidence, reasoning}
        """
        ticker = ticker_data.get("ticker", "")
        instr_type = ticker_data.get("instrument_type", "stock")

        # ── Layer 1: Integrity ──
        ig = self._apply_settings(INTEGRITY_GATE)
        integrity = self._check_integrity(ticker_data, instr_type, ig)
        if not integrity["passed"]:
            return {
                "passed": False, "ticker": ticker,
                "direction": signal_direction, "confidence": signal_confidence,
                "reason": integrity["reason"],
            }

        # ── Layer 2: Alignment ──
        ag = self._apply_settings(ALIGNMENT_GATE)
        alignment = self._check_alignment(
            active_strategies, regime, ticker_data, instr_type, ag, signal_direction
        )
        if not alignment["passed"]:
            return {
                "passed": False, "ticker": ticker,
                "direction": signal_direction, "confidence": signal_confidence,
                "reason": alignment["reason"],
            }
        confidence_mult = alignment.get("confidence_multiplier", 1.0)

        # ── Layer 2.5: Time-of-Day + VWAP structural checks (0DTE-aware) ──
        tod_vwap = self._check_tod_vwap(ticker_data, instr_type, alignment["direction"])
        if not tod_vwap["passed"]:
            return {
                "passed": False, "ticker": ticker,
                "direction": signal_direction, "confidence": signal_confidence,
                "reason": tod_vwap["reason"],
            }
        confidence_mult *= tod_vwap.get("confidence_multiplier", 1.0)

        # ── Layer 3: Conviction (uses alignment-derived direction for consistency) ──
        cg = self._apply_settings(CONVICTION_GATE)
        # ── Globex adjustment: lower conviction minimums for after-hours futures ──
        if instr_type == "future":
            try:
                from engine.time_of_day import is_globex_session, get_globex_adjusted_confidence_min
                if is_globex_session():
                    globex_min = get_globex_adjusted_confidence_min()
                    cg["gate_min_confidence_future"] = max(globex_min, 0.06)
                    cg["gate_strategy_confidence_min"] = max(globex_min * 0.5, 0.06)
            except ImportError:
                pass
        conviction = self._check_conviction(
            active_strategies, alignment["direction"], regime, instr_type,
            cg, confidence_mult,
        )
        if not conviction["passed"]:
            return {
                "passed": False, "ticker": ticker,
                "direction": signal_direction, "confidence": signal_confidence,
                "reason": conviction["reason"],
            }

        # ── Layer 4: Platinum Gate — 95% target filters ──
        pg = self._apply_settings(PLATINUM_GATE)
        platinum = self._check_platinum(
            ticker, ticker_data, active_strategies, consensus_meta,
            instr_type, pg, conviction["confidence"],
        )
        if not platinum["passed"]:
            return {
                "passed": False, "ticker": ticker,
                "direction": signal_direction, "confidence": signal_confidence,
                "reason": platinum["reason"],
            }
        confidence_mult *= platinum.get("confidence_multiplier", 1.0)

        # ── Macro event window filter ──
        macro_event_detected, macro_reason = _is_macro_event_window(ticker, ticker_data)
        if macro_event_detected:
            return {
                "passed": False, "ticker": ticker,
                "direction": signal_direction, "confidence": signal_confidence,
                "reason": f"macro_window_{macro_reason}",
            }

        # ── Volume/liquidity filter ──
        vol_ok, vol_reason = _check_volume_liquidity(ticker, ticker_data, instr_type)
        if not vol_ok:
            return {
                "passed": False, "ticker": ticker,
                "direction": signal_direction, "confidence": signal_confidence,
                "reason": f"volume_liquidity_{vol_reason}",
            }

        # ── News sentiment filter (soft — just logs) ──
        _, news_reason = _check_news_sentiment(ticker_data)

        # ── Correlation conflict check (futures: ES vs NQ, etc.) ──
        corr_blocked, corr_reason = _check_correlation_conflict(ticker, alignment["direction"], ticker_data)
        if corr_blocked:
            confidence_mult *= 0.70  # Reduce confidence but don't block
        # Surface single-conflict warnings: apply confidence discount for 1 opposing signal
        if corr_reason and not corr_blocked and corr_reason.startswith("correlation_warning"):
            confidence_mult *= 0.85  # Mild discount for single opposing signal
        # Store correlation info for transparency
        correlation_info = corr_reason if corr_reason else "none"

        return {
            "passed": True,
            "ticker": ticker,
            "direction": alignment["direction"],
            "confidence": conviction["confidence"] * confidence_mult,
            "regime": regime.get("primary_regime", "ranging"),
            "entry_price": ticker_data.get("current_price", 0),
            "consensus_meta": consensus_meta or {},
            "reason": "passed_all_layers",
            "atr_pct": integrity.get("atr_pct", 0),
            "time_window": tod_vwap.get("window", "unknown"),
            "vwap_position": tod_vwap.get("vwap_position", "unknown"),
            "conviction_tier": platinum.get("tier", "unknown"),
            "macro_filter": macro_reason or "clear",
            "volume_filter": vol_reason or "ok",
            "news_filter": news_reason or "neutral",
            "correlation_filter": correlation_info,
        }

    # -- Layer 2.5: Time-of-Day + VWAP structural check --
    def _check_tod_vwap(
        self, ticker_data: dict, instr_type: str, direction: str
    ) -> dict:
        """Check time-of-day trade eligibility and VWAP/opening range alignment.

        Returns {passed, reason, confidence_multiplier, window, vwap_position}.
        """
        try:
            from engine.time_of_day import (
                can_enter_new_trade, get_time_window, get_min_confidence,
                vwap_alignment_ok,
            )
        except ImportError:
            return {"passed": True, "reason": "tod_module_missing",
                    "confidence_multiplier": 1.0, "window": "unknown", "vwap_position": "unknown"}

        ticker = ticker_data.get("ticker", "")
        dte = ticker_data.get("dte", 0)
        window = get_time_window()
        confidence_mult = 1.0

        # -- Check if new entries are allowed at this time --
        # Globex futures (instr_type="future") bypass the cash-session close
        # buffer in can_enter_new_trade.
        allowed, reason = can_enter_new_trade(ticker, dte, instr_type=instr_type)
        if not allowed:
            return {"passed": False, "reason": reason, "window": window,
                    "vwap_position": "unknown", "confidence_multiplier": 1.0}

        # -- Check min confidence for this time window --
        min_conf = get_min_confidence()
        # Note: confidence check applied in conviction layer, flag here
        if min_conf > 0.25:
            confidence_mult *= 0.85  # higher bar means reduce effective confidence

        # -- VWAP/opening range alignment (only for option instruments) --
        session_ctx = ticker_data.get("session_context", {})
        vwap_pos = "unknown"
        if session_ctx and instr_type == "option":
            vwap_ok, vwap_reason = vwap_alignment_ok(session_ctx, direction)
            vwap_pos = session_ctx.get("vwap_position", "unknown")
            if "counter" in vwap_reason.split():
                confidence_mult *= 0.85  # counter-VWAP trades need extra conviction
            if not vwap_ok:
                return {"passed": False, "reason": vwap_reason, "window": window,
                        "vwap_position": vwap_pos, "confidence_multiplier": confidence_mult}

        return {"passed": True, "reason": reason, "window": window,
                "vwap_position": vwap_pos, "confidence_multiplier": confidence_mult}

    # ── Layer 1: Integrity ──────────────────────────────────────
    def _check_integrity(
        self, ticker_data: dict, instr_type: str, ig: dict
    ) -> dict:
        ticker = ticker_data.get("ticker", "")
        ohlcv = ticker_data.get("ohlcv", [])
        indicators = ticker_data.get("indicators", {})

        # ── Data source: IBKR only ──
        ds = ticker_data.get("data_source", "")
        if ds and ds not in ("ibkr", "ibkr_ohlcv"):
            return {"passed": False, "reason": f"non_ibkr_source_{ds}"}

        # ── Data freshness ──
        # Skip freshness check for OHLCV-only data (no streaming price available yet).
        # The data_source guard above already verified the source is legit.
        if ds != "ibkr_ohlcv":
            price_age = ticker_data.get("price_age_seconds", 0)
            # Use option-specific max age for options, generic for others
            if instr_type == "option":
                max_age = ig.get("gate_max_option_age_seconds", 60.0)
            else:
                max_age = ig["gate_max_price_age_seconds"]
            # ── Globex: relax price age for futures (tick frequency is lower after-hours) ──
            if instr_type == "future":
                try:
                    from engine.time_of_day import is_globex_session
                    if is_globex_session():
                        max_age = 30.0  # futures update less frequently during Globex; 5s is too strict
                except ImportError:
                    pass
            if price_age > max_age:
                return {"passed": False, "reason": f"stale_price_{price_age:.0f}s"}

        # ── Completeness ──
        if ticker_data.get("incomplete_data"):
            missing = ticker_data.get("missing_fields", "unknown")
            return {"passed": False, "reason": f"incomplete_data_{missing}"}

        # ── OHLCV required ──
        if not ohlcv:
            return {"passed": False, "reason": "no_data"}

        close = (
            ohlcv[-1].get("close", 0)
            if isinstance(ohlcv[-1], dict)
            else ohlcv[-1]
        )

        # ── Market hours (skip futures — CME trades 24/5) ──
        if instr_type != "future":
            now_et = _current_et_minutes()
            market_open_et = 9 * 60 + 30
            market_close_et = 16 * 60
            if now_et < market_open_et + ig["gate_market_open_delay_min"]:
                return {"passed": False, "reason": "market_not_yet_open"}
            if now_et >= market_close_et - ig["gate_close_buffer_min"]:
                return {"passed": False, "reason": "too_close_to_close"}

        # ── ATR sweet spot (skip if ATR not computable, e.g. too few bars) ──
        atr = indicators.get("atr_14", 0)
        atr_pct = atr / close if close > 0 and atr > 0 else -1
        _atr_max = ig["gate_atr_sweet_spot_max"]
        if instr_type == "option":
            _atr_max = ig.get("gate_atr_sweet_spot_max_option", 0.080)
        if atr_pct >= 0 and atr_pct < ig["gate_atr_sweet_spot_min"]:
            return {"passed": False, "reason": f"atr_too_low_{atr_pct:.4f}"}
        if atr_pct >= 0 and atr_pct > _atr_max:
            return {"passed": False, "reason": f"atr_too_high_{atr_pct:.4f}"}

        # ── Volume (skip for futures — yfinance reports 0) ──
        if isinstance(ohlcv[-1], dict) and instr_type != "future":
            volume = ohlcv[-1].get("volume", 0)
            recent_vols = (
                [b.get("volume", 0) for b in ohlcv[-20:]]
                if len(ohlcv) >= 20
                else [volume]
            )
            avg_vol = sum(recent_vols) / max(len(recent_vols), 1)
            vol_ratio = volume / max(avg_vol, 1)
            if vol_ratio < ig["gate_volume_ratio_min"]:
                return {"passed": False, "reason": f"volume_too_low_{vol_ratio:.2f}x"}

        # ── Option contract volume ──
        if instr_type == "option":
            opt_vol = indicators.get("option_contract_volume", -1)
            min_opt_vol = ig.get("gate_options_min_contract_volume", 10)
            if 0 <= opt_vol < min_opt_vol:
                opt_oi = indicators.get("option_chain_oi", 0)
                min_oi = ig.get("gate_options_min_oi_fallback", 1000)
                if opt_oi < min_oi:
                    return {
                        "passed": False,
                        "reason": f"option_volume_too_low_{opt_vol}_lt_{min_opt_vol}_oi_{opt_oi}",
                    }

        # ── Spread check ──
        bid = indicators.get("bid", 0)
        ask = indicators.get("ask", 0)
        if bid > 0 and ask > 0:
            spread_pct = (ask - bid) / max(close, 0.01)
            if spread_pct > ig["gate_spread_pct_max"]:
                return {"passed": False, "reason": f"spread_too_wide_{spread_pct:.5f}"}

        if instr_type == "option":
            opt_bid = indicators.get("option_bid", 0)
            opt_ask = indicators.get("option_ask", 0)
            if opt_bid > 0 and opt_ask > 0:
                opt_spread_pct = (opt_ask - opt_bid) / max(opt_bid, 0.01)
                max_opt_spread = ig.get("gate_options_spread_pct_max", 0.10)
                if opt_spread_pct > max_opt_spread:
                    return {
                        "passed": False,
                        "reason": f"option_spread_too_wide_{opt_spread_pct:.2%}",
                    }

        return {"passed": True, "atr_pct": atr_pct}

    # ── Layer 2: Alignment ──────────────────────────────────────
    def _check_alignment(
        self,
        active_strategies: list[dict],
        regime: dict,
        ticker_data: dict,
        instr_type: str,
        ag: dict,
        consensus_direction: str = "",
    ) -> dict:
        regime_type = regime.get("primary_regime", "ranging")
        regime_conf = regime.get("confidence", 0.5)
        ohlcv = ticker_data.get("ohlcv", [])
        indicators = ticker_data.get("indicators", {})

        # ── Determine signal direction: prefer consensus, fall back to raw votes ──
        long_weight = sum(
            s.get("confidence", 0) * 0.5
            for s in active_strategies
            if s.get("direction") == "long"
        )
        short_weight = sum(
            s.get("confidence", 0) * 0.5
            for s in active_strategies
            if s.get("direction") == "short"
        )
        total_weight = long_weight + short_weight
        derived_dir = "long" if long_weight >= short_weight else "short"
        # Use consensus direction when available and non-neutral
        signal_dir = consensus_direction if consensus_direction in ("long", "short") else derived_dir

        if total_weight == 0:
            return {"passed": False, "reason": "no_strategy_direction"}

        # ── Regime alignment ──
        if instr_type not in ("option", "future_with_option"):
            allowed = (
                ALIGNMENT_MATRIX.get(regime_type, {}).get(signal_dir, "allowed")
            )
            if allowed == "blocked":
                return {
                    "passed": False,
                    "reason": f"regime_blocks_{signal_dir}_in_{regime_type}",
                }

        confidence_mult = 1.0
        if regime_conf < ag["gate_regime_low_confidence"]:
            confidence_mult = 0.85

        # ── Initialize trend_reasons before conditional block ──
        trend_reasons: list[str] = []

        # ── Trend awareness checks (stocks + futures, skip options) ──
        if indicators and ohlcv and instr_type != "option":
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

            # Check 1: Strong SMA20 trend → block counter-trade
            if sma20 > 0 and current_price > 0:
                price_vs_sma20 = (current_price - sma20) / sma20
                if price_vs_sma20 > 0.03 and signal_dir == "short":
                    return {
                        "passed": False,
                        "reason": f"price_+{price_vs_sma20:.1%}_above_sma20_blocks_short",
                    }
                if price_vs_sma20 < -0.03 and signal_dir == "long":
                    return {
                        "passed": False,
                        "reason": f"price_{price_vs_sma20:.1%}_below_sma20_blocks_long",
                    }

                # Check 2: Moderate SMA20 trend → penalize counter-trade
                if price_vs_sma20 > 0.015 and signal_dir == "short":
                    confidence_mult *= 0.50
                    trend_reasons.append(
                        f"price_+{price_vs_sma20:.1%}_above_sma20"
                    )
                if price_vs_sma20 < -0.015 and signal_dir == "long":
                    confidence_mult *= 0.50
                    trend_reasons.append(
                        f"price_{price_vs_sma20:.1%}_below_sma20"
                    )

            # Check 3: Overbought/oversold RSI + BB
            if rsi < 35 and bb_pct < 0.25 and signal_dir == "short":
                confidence_mult *= 0.40
                trend_reasons.append(f"oversold_rsi={rsi}_bb={bb_pct:.2f}")
            if rsi > 65 and bb_pct > 0.75 and signal_dir == "long":
                confidence_mult *= 0.40
                trend_reasons.append(f"overbought_rsi={rsi}_bb={bb_pct:.2f}")

            # Check 4: Contradictory order flow + VW momentum
            if of_bias == "bullish" and vw_mom > 0.6 and signal_dir == "short":
                confidence_mult *= 0.60
                trend_reasons.append(f"bullish_flow+vw={vw_mom:.2f}_vs_short")
            if of_bias == "bearish" and vw_mom < -0.6 and signal_dir == "long":
                confidence_mult *= 0.60
                trend_reasons.append(f"bearish_flow+vw={vw_mom:.2f}_vs_long")

            # Check 5: MACD trend — don't short into bullish MACD, don't long into bearish MACD
            if (
                macd_hist > 0
                and macd > 0
                and sma20 > 0
                and current_price > sma20
                and signal_dir == "short"
            ):
                confidence_mult *= 0.55
                trend_reasons.append("macd_bullish+price_above_sma20")
            if (
                macd_hist < 0
                and macd < 0
                and sma20 > 0
                and current_price < sma20
                and signal_dir == "long"
            ):
                confidence_mult *= 0.55
                trend_reasons.append("macd_bearish+price_below_sma20")

            if confidence_mult < 0.3:
                return {
                    "passed": False,
                    "reason": "trend_awareness_block_"
                    + "_".join(trend_reasons[-2:]),
                }

        # ── COT alignment (futures only) ──
        cot_data = ticker_data.get("cot")
        if cot_data and instr_type == "future":
            comm_long = cot_data.get("commercial_long", 0)
            comm_short = cot_data.get("commercial_short", 0)
            if comm_long > 0 or comm_short > 0:
                cot_bias = comm_long - comm_short
                total = comm_long + comm_short
                if total > 0:
                    cot_ratio = cot_bias / total
                    # Commercials are net-short and signal is long → penalize
                    if cot_ratio < -0.1 and signal_dir == "long":
                        confidence_mult *= ag["gate_cot_counter_penalty"]
                        trend_reasons.append(
                            f"cot_commercials_net_short_{abs(cot_ratio):.1%}"
                        )
                    # Commercials are net-long and signal is short → penalize
                    elif cot_ratio > 0.1 and signal_dir == "short":
                        confidence_mult *= ag["gate_cot_counter_penalty"]
                        trend_reasons.append(
                            f"cot_commercials_net_long_{cot_ratio:.1%}"
                        )

        # ── Directional bias cap (only penalize when opposition exists) ──
        dominant_weight = max(long_weight, short_weight)
        bias_ratio = dominant_weight / total_weight if total_weight > 0 else 0.5
        has_opposition = min(long_weight, short_weight) > total_weight * 0.05
        if has_opposition and bias_ratio > ag.get("gate_directional_bias_threshold", 0.70):
            confidence_mult *= ag.get("gate_directional_bias_penalty", 0.85)

        return {
            "passed": True,
            "direction": signal_dir,
            "confidence_multiplier": confidence_mult,
            "bias_ratio": bias_ratio,
        }

    # ── Layer 3: Conviction ─────────────────────────────────────
    def _check_conviction(
        self,
        active_strategies: list[dict],
        signal_direction: str,
        regime: dict,
        instr_type: str,
        cg: dict,
        confidence_mult: float = 1.0,
    ) -> dict:
        min_conf_key = f"gate_min_confidence_{instr_type}"
        strat_conf_key = f"gate_strategy_confidence_min_{instr_type}"
        strat_conf_min = cg.get(
            strat_conf_key, cg.get("gate_strategy_confidence_min", 0.12)
        )

        # Filter strategies by confidence AND direction (all types including options)
        votes = [
            s
            for s in active_strategies
            if s.get("direction") == signal_direction
            and s.get("confidence", 0) >= strat_conf_min
        ]

        strat_agree_key = f"gate_min_strategies_agree_{instr_type}"
        min_agree = cg.get(
            strat_agree_key, cg.get("gate_min_strategies_agree", 2)
        )
        if len(votes) < min_agree:
            return {
                "passed": False,
                "reason": f"only_{len(votes)}_strategies_agree",
            }

        # Agreement bonus
        bonus = 1.0
        if len(votes) >= 6:
            bonus = cg.get("gate_bonus_6plus_strategies", 1.20)
        elif len(votes) >= 4:
            bonus = cg.get("gate_bonus_4plus_strategies", 1.10)

        # Raw confidence = average of agreeing strategy confidences
        raw_conf = sum(v.get("confidence", 0) for v in votes) / max(len(votes), 1)

        # Adjusted = raw × alignment_multiplier × agreement_bonus
        adjusted = raw_conf * confidence_mult * bonus

        if adjusted < cg.get(min_conf_key, 0.50):
            return {
                "passed": False,
                "reason": f"confidence_too_low_{adjusted:.3f}",
            }

        return {"passed": True, "confidence": adjusted}

    # -- Layer 4: Platinum Gate --
    def _check_platinum(
        self,
        ticker: str,
        ticker_data: dict,
        active_strategies: list[dict],
        consensus_meta: dict,
        instr_type: str,
        pg: dict,
        confidence: float,
    ) -> dict:
        """Platinum tier requirements for 95% target:
        - Conviction tier = platinum (from consensus_meta)
        - MTF alignment confirmed
        - Regime aligned with signal
        - No counter-trend signals
        """
        tier = consensus_meta.get("consensus_conviction_tier", "bronze")
        
        if pg.get("gate_require_platinum_tier") and tier != "platinum":
            return {
                "passed": False,
                "reason": f"platinum_required_tier_{tier}",
            }
        
        if pg.get("gate_require_mtf_alignment"):
            mtf_dir = consensus_meta.get("consensus_mtf_direction")
            if mtf_dir and mtf_dir != consensus_meta.get("consensus_direction"):
                return {
                    "passed": False,
                    "reason": f"mtf_mismatch_{mtf_dir}_vs_{consensus_meta.get('consensus_direction')}",
                }
        
        if pg.get("gate_require_regime_alignment"):
            regime = consensus_meta.get("consensus_regime", "ranging")
            signal_dir = consensus_meta.get("consensus_direction", "neutral")
            trend_dir = {"strong_uptrend": "long", "uptrend": "long",
                         "downtrend": "short", "strong_downtrend": "short"}.get(regime)
            if trend_dir and signal_dir != trend_dir:
                return {
                    "passed": False,
                    "reason": f"regime_mismatch_{regime}_vs_{signal_dir}",
                }
        
        if pg.get("gate_block_counter_trend"):
            if consensus_meta.get("consensus_counter_trend") == "yes":
                return {
                    "passed": False,
                    "reason": "counter_trend_blocked",
                }
        
        # Platinum bonus multiplier
        bonus = 1.0
        if tier == "platinum":
            bonus = 1.15
        elif tier == "gold":
            bonus = 1.05
        
        return {"passed": True, "confidence_multiplier": bonus, "tier": tier}
