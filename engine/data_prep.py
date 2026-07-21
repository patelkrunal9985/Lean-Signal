"""
Data Preparation Phase — Fetches all market data and builds the ticker data map.

Extracted from engine/runner.py during the 3-way refactor.
Stateless: takes cycle metadata, returns ticker_data_map and associated context.
"""
import time
from typing import Any

from utils.logger import get_logger
from utils.time_utils import now_ny

logger = get_logger("engine.data_prep")


def prepare_all_data(
    cycle_id: int,
    start_time: float,
) -> dict[str, Any]:
    """Fetch and prepare all market data for the cycle.

    Returns a dict with keys:
      - ticker_data_map: dict[ticker] -> per-ticker context
      - all_tickers: list of (ticker, instr_type) tuples (including any options added)
      - account_data: account summary dict
      - positions_data: position summary dict
      - daytype_prediction: daytype result dict
    """
    from engine.ibkr_data_feed import (
        fetch_historical_bars, get_live_price, get_market_depth,
        get_order_book_imbalance, subscribe_ticker,
    )
    from engine.real_time_data_feeds import RealTimeDataFeedsSkill
    from indicators.advanced_indicators import compute_all_advanced
    from engine.cot_fetcher import get_cot_for_ticker, _TICKER_TO_CFTC
    from engine.tpo_engine import compute_tpo_profile, compute_volume_profile
    from engine.order_book import compute_order_book_summary, reset as reset_order_book
    from engine.account_monitor import (
        fetch_account_summary, fetch_positions, get_position_summary,
        reset_daily_pnl,
    )
    from utils.config import FIXED_STOCKS, FIXED_FUTURES

    reset_order_book()
    reset_daily_pnl()

    # ── Build ticker list ──
    all_tickers: list[tuple[str, str]] = []
    for t in FIXED_STOCKS:
        all_tickers.append((t, "stock"))
    for t in FIXED_FUTURES:
        all_tickers.append((t, "future"))

    data_feeds = RealTimeDataFeedsSkill()
    ticker_data_map: dict[str, dict] = {}

    # ── Phase 1: Per-ticker data fetch ──
    for ticker, instr_type in all_tickers:
        incomplete = False
        missing: list[str] = []

        try:
            subscribe_ticker(ticker)
        except Exception:
            pass

        # ── OHLCV (IBKR primary) ──
        ohlcv: list = []
        ohlcv_1m: list = []
        try:
            bars = fetch_historical_bars(ticker, "1 M", "1 day")
            if bars:
                ohlcv = bars
        except Exception:
            pass

        # ── 1-minute OHLCV for MTF/FVG/Order Flow strategies ──
        try:
            bars_1m = fetch_historical_bars(ticker, "1 D", "1 min")
            if bars_1m:
                ohlcv_1m = bars_1m
        except Exception:
            pass

        # ── TPO/Market Profile from 1m bars (futures only) ──
        tpo_profile: dict = {}
        if instr_type == "future" and ohlcv_1m:
            try:
                tpo_profile = compute_tpo_profile(ohlcv_1m)
            except Exception:
                pass

        # ── Volume profile from 1m bars ──
        vol_profile: dict = {}
        if instr_type == "future" and ohlcv_1m:
            try:
                vol_profile = compute_volume_profile(ohlcv_1m)
            except Exception:
                pass

        # ── Live price with freshness tracking ──
        live_price = 0
        price_age = 999.0
        try:
            q = get_live_price(ticker)
            if q:
                live_price = q.get("price", 0)
                price_age = q.get("age_seconds", 999.0)
        except Exception:
            pass
        # Fallback to last OHLCV close if live price not available yet
        if live_price <= 0 and ohlcv:
            last_bar = ohlcv[-1] if isinstance(ohlcv[-1], dict) else None
            if last_bar:
                live_price = float(last_bar.get("close", last_bar.get(4, 0)) or 0)
                price_age = 0.0  # OHLCV-derived price is not streamed; reset age to avoid stale_price rejection

        # ── Indicators ──
        indicators: dict = {}
        if ohlcv:
            try:
                indicators = compute_all_advanced(ohlcv)
            except Exception:
                pass

        # ── Market depth (IBKR) ──
        depth_data: dict = {}
        try:
            depth_data = get_market_depth(ticker)
        except Exception:
            pass

        # ── Enriched order book analysis ──
        ob_analysis: dict = {}
        ob_imbalance: dict = {}
        if instr_type == "future" and depth_data:
            try:
                ob_analysis = compute_order_book_summary(
                    depth_data.get("bids", []),
                    depth_data.get("asks", []),
                    ticker,
                )
                ob_imbalance = get_order_book_imbalance(ticker)
            except Exception:
                pass

        # ── COT data (futures only, from CFTC.gov, zero IBKR calls) ──
        cot_data: dict = {}
        if instr_type == "future":
            try:
                cot_data = get_cot_for_ticker(ticker)
            except Exception:
                pass
            if ticker in _TICKER_TO_CFTC:
                total_oi = cot_data.get("total_open_interest", 0) if cot_data else 0
                comm_long = cot_data.get("commercial_long", 0) if cot_data else 0
                comm_short = cot_data.get("commercial_short", 0) if cot_data else 0
                if total_oi == 0 and comm_long == 0 and comm_short == 0:
                    incomplete = True
                    missing.append("cot_empty")

        # ── Fundamentals, news, sentiment ──
        fundamentals = data_feeds.get_fundamentals(ticker)
        news = data_feeds.get_news(ticker, max_items=3)
        sentiment = data_feeds.get_sentiment(ticker)
        earnings = data_feeds.get_earnings(ticker)
        insider = data_feeds.get_insider_trades(ticker)

        # ── Data source: streaming (price_age between 0 and 999) vs OHLCV fallback ──
        # price_age=0.0 means OHLCV fallback, 999.0 means no streaming available
        data_source = "ibkr" if (0 < price_age < 999.0) else "ibkr_ohlcv"

        # ── Session context (VWAP, opening range) for market structure ──
        session_context: dict = {}
        try:
            from engine.time_of_day import get_market_session_context
            session_context = get_market_session_context(ohlcv, live_price)
        except Exception:
            pass

        ticker_data_map[ticker] = {
            "ticker": ticker,
            "instrument_type": instr_type,
            "ohlcv": ohlcv,
            "ohlcv_1m": ohlcv_1m,
            "candles_1m": ohlcv_1m,
            "current_price": live_price,
            "price_age_seconds": price_age,
            "depth": depth_data,
            "order_book_depth": depth_data,
            "order_book_imbalance": ob_imbalance if ob_imbalance else {},
            "order_book_summary": ob_analysis if ob_analysis and ob_analysis.get("available") else {},
            "tpo_profile": tpo_profile if instr_type == "future" else {},
            "volume_profile_intraday": vol_profile if instr_type == "future" else {},
            "intraday_vwap": vol_profile,
            "ob_analysis": ob_analysis,
            "indicators": indicators,
            "cot": cot_data,
            "fundamentals": fundamentals,
            "earnings": earnings,
            "insider_trades": insider,
            "news": news,
            "sentiment": sentiment,
            "data_source": data_source,
            "incomplete_data": incomplete,
            "missing_fields": ",".join(missing) if missing else "",
            "session_context": session_context,
        }

    # ── Phase 2: Futures cumulative delta ──
    from engine.tick_engine import get_tick_stats
    from engine.futures_data import compute_cumulative_delta
    for t, instr in all_tickers:
        if instr != "future":
            continue
        td = ticker_data_map.get(t, {})
        if not td:
            continue
        try:
            tick_stats = get_tick_stats(t)
            if tick_stats and tick_stats.get("cumulative_delta", 0) != 0:
                td["cumulative_delta"] = tick_stats
                td["vpin"] = tick_stats.get("vpin", 0)
            else:
                ohlcv_1m = td.get("ohlcv_1m", td.get("ohlcv", []))
                depth = td.get("depth", {})
                cd = compute_cumulative_delta(ohlcv_1m, depth)
                if cd:
                    td["cumulative_delta"] = cd
        except Exception:
            pass

    # ── Phase 3: Globex range + session classifier (futures only) ──
    from engine.futures_data import get_globex_range
    from engine.session_classifier import compute_session_context
    globex_ranges: dict[str, dict] = {}
    for t, instr in all_tickers:
        if instr != "future":
            continue
        try:
            gr = get_globex_range(t)
            if gr:
                globex_ranges[t] = gr
                td = ticker_data_map.get(t, {})
                if td:
                    td["globex_range"] = gr
        except Exception:
            pass
        # Compute enriched session context
        td = ticker_data_map.get(t, {})
        if td and td.get("ohlcv_1m"):
            try:
                enriched = compute_session_context(
                    ohlcv_1m=td["ohlcv_1m"],
                    cumulative_delta=td.get("cumulative_delta", {}),
                    globex_range=globex_ranges.get(t, {}),
                    current_price=td.get("current_price", 0),
                )
                if enriched:
                    td["session_context"] = enriched
            except Exception:
                pass

    # ── Phase 4: Options processing ──
    from engine.option_metrics import compute_option_metrics
    from engine.ibkr_data_feed import cancel_all_option_subscriptions
    for opt_underlying in ["SPY", "QQQ"]:
        try:
            underlying_data = ticker_data_map.get(opt_underlying, {})
            underlying_price = underlying_data.get("current_price", 0)
            if underlying_price <= 0:
                continue
            opt_ctx = compute_option_metrics(opt_underlying, underlying_price, ticker_data_map)
            if opt_ctx:
                opt_ctx["session_context"] = underlying_data.get("session_context", {})
                ticker_data_map[opt_ctx["ticker"]] = opt_ctx
                all_tickers.append((opt_ctx["ticker"], "option"))
        except Exception as exc:
            logger.warning("Option metrics for %s failed: %s", opt_underlying, exc)
        cancel_all_option_subscriptions()
        time.sleep(1.5)

    # ── Phase 5: Market breadth ──
    try:
        from engine.market_breadth import compute_market_breadth
        breadth_ctx = compute_market_breadth(ticker_data_map)
        for key, data in ticker_data_map.items():
            if data.get("instrument_type") == "option":
                data["market_breadth"] = breadth_ctx
    except Exception as exc:
        logger.warning("Market breadth computation failed: %s", exc)

    # ── Phase 6: Time-of-day profile injection ──
    logger.info("Cycle #%d: injecting time_of_day_profile (phase 4/7)", cycle_id)
    now_et = now_ny()
    try:
        from engine.time_of_day import (
            get_time_window, get_stop_tightness, get_min_confidence,
        )
        current_window = get_time_window()
        tod_profile = {
            "window": current_window,
            "session": current_window,
            "minutes_from_open": (
                (now_et.hour * 60 + now_et.minute) - 570
            ) if current_window not in ("pre_market", "after_hours", "closed") else 0,
            "stop_tightness": get_stop_tightness(),
            "min_confidence": get_min_confidence(),
        }
        for key in ticker_data_map:
            ticker_data_map[key]["time_of_day_profile"] = tod_profile
    except Exception:
        pass

    # ── Phase 7: Account monitoring ──
    logger.info("Cycle #%d: fetching account summary (phase 5/7)", cycle_id)
    account_data: dict = {}
    positions_data: dict = {}
    try:
        account_data = fetch_account_summary()
        logger.info("Cycle #%d: account summary done, fetching positions", cycle_id)
        pos_list = fetch_positions()
        positions_data = get_position_summary(pos_list)
    except Exception:
        logger.debug("Cycle #%d: account monitoring failed", cycle_id)

    # ── Phase 8: Day-type prediction ──
    logger.info("Cycle #%d: day-type prediction (phase 6/7)", cycle_id)
    daytype_prediction: dict = {"prediction": "unknown", "confidence": 0, "trained": False}
    try:
        from engine.daytype_model import predict_day_type
        es_data = ticker_data_map.get("ES=F", {})
        vx_data = ticker_data_map.get("VX=F", {})
        es_ohlcv_1m = es_data.get("ohlcv_1m", [])
        es_cd = es_data.get("cumulative_delta", {})
        es_tpo = es_data.get("tpo_profile", {})
        es_globex = globex_ranges.get("ES=F", {})
        vix_spot = vx_data.get("current_price", 0)
        daytype_prediction = predict_day_type(
            ohlcv_1m=es_ohlcv_1m,
            cumulative_delta=es_cd,
            tpo_profile=es_tpo,
            globex_range=es_globex,
            vix_spot=float(vix_spot),
            vix_1m=float(vix_spot),
            vix_2m=float(vix_spot) * 1.05,
            current_price=es_data.get("current_price", 0),
        )
    except Exception:
        pass

    return {
        "ticker_data_map": ticker_data_map,
        "all_tickers": all_tickers,
        "account_data": account_data,
        "positions_data": positions_data,
        "daytype_prediction": daytype_prediction,
    }
