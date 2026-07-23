"""
Comprehensive strategy validation test harness.
Runs all V2/V3 strategies, consensus, gate, tick engine, and pipeline with synthetic data.
No IBKR connection needed.
"""
import sys, os, json, traceback, math, random, time
sys.path.insert(0, r'C:\Users\patel\OneDrive\Desktop\Projects\Lean Signals')

# Force UTF-8 for stdout/stderr to handle Unicode characters in log messages
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

print('='*70)
print('COMPREHENSIVE STRATEGY VALIDATION TEST')
print('='*70)

random.seed(42)
base_price = 5500.0

# -- 1. Build rich mock context --

def make_ohlcv(days=200):
    bars = []
    p = base_price
    for i in range(days):
        o = p
        h = p * (1 + random.uniform(-0.01, 0.015))
        l = p * (1 + random.uniform(-0.015, 0.01))
        c = p * (1 + random.uniform(-0.008, 0.008))
        v = int(random.uniform(50000, 200000))
        bars.append({'open': round(o,2), 'high': round(h,2), 'low': round(l,2), 'close': round(c,2), 'volume': v})
        p = c
    return bars

def make_ohlcv_1m(bars=390):
    p = base_price
    result = []
    for i in range(bars):
        o = p
        h = p * (1 + random.uniform(-0.002, 0.003))
        l = p * (1 + random.uniform(-0.003, 0.002))
        c = p * (1 + random.uniform(-0.0015, 0.0015))
        v = int(random.uniform(100, 5000))
        result.append({'open': round(o,2), 'high': round(h,2), 'low': round(l,2), 'close': round(c,2), 'volume': v})
        p = c
    return result

def make_indicators():
    return {
        'atr_14': 15.0,
        'sma_50': base_price * 0.99,
        'sma_20': base_price * 1.005,
        'sma_200': base_price * 0.95,
        'rsi_14': 55.0,
        'bb_upper': base_price * 1.02,
        'bb_lower': base_price * 0.98,
        'bb_middle': base_price,
        'volume_sma_20': 100000,
        'vwap': base_price * 1.001,
        'ema_9': base_price * 1.002,
        'ema_21': base_price * 0.998,
        'macd': 5.0,
        'macd_signal': 3.0,
        'macd_hist': 2.0,
    }

def make_option_chain():
    calls, puts = [], []
    for strike in range(5300, 5701, 25):
        is_atm = abs(strike - base_price) < 50
        gamma = random.uniform(0.01, 0.08) if is_atm else random.uniform(0.001, 0.01)
        iv = random.uniform(0.12, 0.25)
        oi = int(random.uniform(1000, 50000))
        vol = int(random.uniform(100, 10000))
        premium = max(1.0, abs(strike - base_price) * random.uniform(0.3, 0.7))
        delta = random.uniform(0.3, 0.7)
        calls.append({
            'strike': strike, 'gamma': round(gamma, 6), 'delta': round(delta, 4),
            'theta': round(-random.uniform(0.01, 0.05), 4), 'vega': round(random.uniform(0.1, 0.4), 4),
            'impliedVolatility': round(iv, 4), 'openInterest': oi, 'volume': vol,
            'bid': round(premium * 0.95, 2), 'ask': round(premium * 1.05, 2),
        })
        puts.append({
            'strike': strike, 'gamma': round(gamma, 6), 'delta': round(-delta, 4),
            'theta': round(-random.uniform(0.01, 0.05), 4), 'vega': round(random.uniform(0.1, 0.4), 4),
            'impliedVolatility': round(iv, 4), 'openInterest': oi, 'volume': vol,
            'bid': round(premium * 0.95, 2), 'ask': round(premium * 1.05, 2),
        })
    return {'calls': calls, 'puts': puts}

ohlcv = make_ohlcv()
ohlcv_1m = make_ohlcv_1m()

mock_context = {
    'ticker': 'ES=F',
    'instrument_type': 'future',
    'ohlcv': ohlcv,
    'ohlcv_1m': ohlcv_1m,
    'current_price': base_price,
    'price_age_seconds': 2.0,
    'bid': base_price - 0.5,
    'ask': base_price + 0.5,
    'volume': 150000,
    'high': base_price * 1.005,
    'low': base_price * 0.995,
    'open': base_price * 0.998,
    'change': 15.0,
    'change_pct': 0.0027,
    'depth': {
        'bids': [{'price': base_price - i*0.25, 'size': random.randint(10,100)} for i in range(1,11)],
        'asks': [{'price': base_price + i*0.25, 'size': random.randint(10,100)} for i in range(1,11)],
    },
    'indicators': make_indicators(),
    'fundamentals': {'pe': 22.5, 'eps': 12.3, 'market_cap': 5e11, 'div_yield': 1.2, 'beta': 1.1, 'sector': 'Technology'},
    'earnings': {'next_date': '2026-08-15', 'recent': {'surprise': 0.05, 'beat': True}},
    'insider_trades': [{'shares': 10000, 'price': base_price, 'type': 'buy', 'date': '2026-07-15'}],
    'news': [{'headline': 'ES at key resistance level', 'source': 'Reuters', 'url': '#', 'sentiment': 0.3}],
    'sentiment': {'overall': 0.15, 'bullish_pct': 0.55, 'bearish_pct': 0.25, 'neutral_pct': 0.20},
    'cot': {
        'commercial_long': 150000, 'commercial_short': 120000,
        'noncommercial_long': 80000, 'noncommercial_short': 100000,
        'total_open_interest': 500000, 'date': '2026-07-14',
    },
    'vix_spot': 15.5,
    'dte': 0,
    'expiry': '2026-07-17',
    'underlying_price': base_price,
    'underlying': 'SPX',
    'price_change_1d': 0.0035,
    'daily_range': 25.0,
    'option_chain': make_option_chain(),
    'gamma_walls': [{'strike': s, 'gex_per_1pct': 100000} for s in [5400, 5450, 5550, 5600]],
    'gamma_flip_level': base_price * 1.005,
    'iv': 0.165,
    'hv_10': 0.142,
    'pc_ratio': 0.85,
    'pc_ratio_5day_avg': 0.88,
    'delta_positioning': {'net_delta': 25000, 'delta_ratio': 1.5, 'call_delta': 2000000, 'put_delta': 1500000},
    'atm_straddle_price': 35.50,
    'skew_term_1m': 1.5,
    'charm_direction': 'neutral',
    'charm_magnitude': 0.0025,
    'total_vanna': 1500000,
    'total_charm': -500000,
    'market_breadth': {
        'composite': {'composite_score': 0.3, 'state': 'slightly_bullish'},
        'breadth_trend': 'improving',
        'vix_confirmation': {'state': 'aligned_bullish'},
        'futures_alignment': {'alignment_ratio': 0.75},
        'tech_divergence': 0.05,
        'small_cap': {'participating': True},
        'breadth_thrust': {'thrust_active': False},
    },
    'cumulative_delta': {
        'cumulative_delta': 850,
        'delta_60s': 120,
        'total_buy_vol': 45000,
        'total_sell_vol': 38000,
        'buy_count': 1200, 'sell_count': 980,
        'trade_imbalance': 0.15,
        'volume_imbalance': 0.084,
        'vpin': 0.32,
        'avg_trade_size': 38.5,
        'last_price': base_price,
        'last_bid': base_price - 0.5,
        'last_ask': base_price + 0.5,
        'total_trades': 2180,
    },
    'vpin': 0.32,
    'session_context': {
        'time_window': 'power_hour', 'vwap_position': 'above',
        'opening_range_high': base_price * 1.003, 'opening_range_low': base_price * 0.997,
    },
    'time_window': 'power_hour',
    'vwap_position': 'above',
    'data_source': 'ibkr',
    'incomplete_data': False,
    'missing_fields': '',
    'source': 'ibkr',
}

mock_stock_ctx = dict(mock_context, ticker='SPY', instrument_type='stock', underlying='SPY')
mock_option_ctx = dict(mock_context, ticker='SPY_OPT', instrument_type='option', underlying='SPY',
                       dte=0, expiry='2026-07-17')

results = {'passed': 0, 'failed': 0, 'skipped': 0, 'errors': []}

# -- 2. Test V3 strategies --
print('\n--- V3 STRATEGIES ---')
from engine.v3.registry import get_strategies
for instr_type, ctx in [('stock', mock_stock_ctx), ('future', mock_context), ('option', mock_option_ctx)]:
    strats = get_strategies(instr_type)
    if not strats:
        print(f'  [!]  No V3 strategies for {instr_type}')
        continue
    for s in strats:
        try:
            result = s.compute(ctx)
            assert isinstance(result, dict), f'returned {type(result).__name__}'
            assert 'direction' in result, 'missing direction'
            assert result['direction'] in ('long', 'short', 'neutral'), f"bad direction: {result['direction']}"
            assert 'confidence' in result, 'missing confidence'
            assert isinstance(result['confidence'], (int, float)), f"bad confidence type: {type(result['confidence'])}"
            assert 0 <= result['confidence'] <= 1, f"confidence out of range: {result['confidence']}"
            results['passed'] += 1
            if results['passed'] <= 5 or results['passed'] % 15 == 0:
                print(f'   [OK] {s.name:35s} {result["direction"]:7s} {result["confidence"]:.3f}')
        except Exception as e:
            results['failed'] += 1
            tb = traceback.format_exc()
            results['errors'].append({'strategy': s.name, 'type': instr_type, 'error': str(e)[:200], 'traceback': tb})
            print(f' [FAIL] {s.name:35s} FAILED: {str(e)[:80]}')

# -- 3. Test V2 strategies --
print('\n--- V2 STRATEGIES ---')
from engine.v2.registry import V2StrategyRegistry
v2 = V2StrategyRegistry()
for ctx in [mock_stock_ctx, mock_context]:
    typ = ctx['instrument_type']
    try:
        v2_results = v2.run_all(ctx)
        assert isinstance(v2_results, list), f'run_all returned {type(v2_results).__name__}'
        for r in v2_results:
            assert 'direction' in r
            assert 'confidence' in r
        results['passed'] += 1
        print(f'   [OK] V2Registry({typ}) returned {len(v2_results)} results')
    except Exception as e:
        results['failed'] += 1
        results['errors'].append({'strategy': f'V2Registry({typ})', 'type': typ, 'error': str(e)[:200], 'traceback': traceback.format_exc()})
        print(f' [FAIL] V2Registry({typ}) FAILED: {str(e)[:80]}')

# -- 4. Test Consensus Coordinator --
print('\n--- CONSENSUS COORDINATOR ---')
from engine.consensus_coordinator import compute_consensus
sample_votes = []
for s in get_strategies('future')[:5]:
    try:
        r = s.compute(mock_context)
        if r['direction'] != 'neutral' and r['confidence'] > 0:
            sample_votes.append({'name': s.name, 'strategy': s.name, 'direction': r['direction'],
                                  'confidence': r['confidence'], 'source': 'v3'})
    except: pass
for r in v2_results[:3]:
    sample_votes.append({'name': r.get('name','?'), 'strategy': r.get('name','?'),
                          'direction': r['direction'], 'confidence': r['confidence'], 'source': 'v2'})
try:
    direction, conf, meta = compute_consensus(sample_votes, [], 'ranging', 'ES=F', 'future', base_price, 15.0, base_price*0.99)
    assert direction in ('long', 'short', 'neutral')
    assert isinstance(conf, (int, float))
    assert isinstance(meta, dict)
    results['passed'] += 1
    print(f'   [OK] Consensus: {direction} @ {conf:.3f}  (votes={len(sample_votes)}, net={meta.get("net_score","?")})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'compute_consensus', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Consensus FAILED: {str(e)[:80]}')

# -- 5. Test Signal Quality Gate --
print('\n--- SIGNAL QUALITY GATE ---')
from engine.v3.gate import SignalQualityGate
gate = SignalQualityGate()
try:
    gr = gate.evaluate(ticker_data=mock_context, signal_direction='long', signal_confidence=0.65,
                        active_strategies=sample_votes, regime={'primary_regime': 'ranging', 'confidence': 0.7},
                        consensus_meta=meta)
    assert isinstance(gr, dict) and 'passed' in gr and 'reason' in gr
    results['passed'] += 1
    print(f'   [OK] Gate: passed={gr["passed"]} reason={gr.get("reason","?")}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'SignalQualityGate.evaluate', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Gate FAILED: {str(e)[:80]}')

# -- 6. Test Tick Engine --
print('\n--- TICK ENGINE ---')
from engine.tick_engine import on_tick, get_tick_stats, reset
reset()
try:
    now = time.time()
    for i in range(50):
        is_buy = random.random() > 0.5
        sz = random.randint(1, 20)
        lp = base_price + (random.random()-0.5)*2
        sp = random.uniform(0.1, 0.5)
        b, a = lp - sp, lp + sp
        on_tick('ES=F', 'FUT', a+0.05 if is_buy else b-0.05, b, a, 0, sz, now + i * 0.05)
    stats = get_tick_stats('ES=F')
    assert isinstance(stats, dict) and 'cumulative_delta' in stats and 'vpin' in stats
    results['passed'] += 1
    print(f'   [OK] TickEngine: delta={stats["cumulative_delta"]} buy={stats["total_buy_vol"]} sell={stats["total_sell_vol"]} vpin={stats["vpin"]:.3f}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickEngine', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] TickEngine FAILED: {str(e)[:80]}')

# -- 7. Test Entry/Exit Levels --
print('\n--- ENTRY/EXIT LEVELS ---')
from engine.entry_exit import compute_entry_exit_levels
for instr in ['stock', 'future', 'option']:
    try:
        ctx = {'stock': mock_stock_ctx, 'future': mock_context, 'option': mock_option_ctx}[instr]
        levels = compute_entry_exit_levels(ctx['ticker'], ctx, 'long', 0.6, instr)
        assert isinstance(levels, dict)
        results['passed'] += 1
        print(f'   [OK] EntryExit({instr}): entry={levels.get("entry_price","?")} sl={levels.get("stop_loss","?")} tp={levels.get("take_profit","?")}')
    except Exception as e:
        results['failed'] += 1
        results['errors'].append({'strategy': f'EntryExit({instr})', 'type': instr, 'error': str(e)[:200], 'traceback': traceback.format_exc()})
        print(f' [FAIL] EntryExit({instr}) FAILED: {str(e)[:80]}')

# -- 8. Test Market Breadth --
print('\n--- MARKET BREADTH ---')
from engine.market_breadth import compute_market_breadth
try:
    tdm = {'ES=F': mock_context, 'NQ=F': mock_context, 'YM=F': mock_context, 'RTY=F': mock_context,
           'SPX': mock_stock_ctx, 'NDX': mock_stock_ctx}
    breadth = compute_market_breadth(tdm)
    assert isinstance(breadth, dict)
    results['passed'] += 1
    print(f'   [OK] MarketBreadth: state={breadth.get("composite",{}).get("state","?")} '
          f'trend={breadth.get("breadth_trend","?")}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'MarketBreadth', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] MarketBreadth FAILED: {str(e)[:80]}')

# -- 9. Test Strike Selector --
print('\n--- STRIKE SELECTOR ---')
from engine.strike_selector import recommend_strike
try:
    chain = mock_option_ctx.get('option_chain', {})
    strike_rec = recommend_strike('SPY', chain, base_price, 'long', 0.65, 0.165, 25.0, 0)
    assert isinstance(strike_rec, dict)
    results['passed'] += 1
    print(f'   [OK] StrikeSelector: strike={strike_rec.get("recommended_strike","?")} win_rate={strike_rec.get("estimated_win_rate","?")}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'StrikeSelector', 'type': 'option', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] StrikeSelector FAILED: {str(e)[:80]}')

# -- 10. Test Regime Detector --
print('\n--- REGIME DETECTOR ---')
from regime.detector import RegimeDetector
try:
    rd = RegimeDetector()
    regime = rd.detect(ohlcv, mock_context.get('indicators', {}))
    assert isinstance(regime, dict)
    results['passed'] += 1
    print(f'   [OK] RegimeDetector: primary={regime.get("primary_regime","?")} confidence={regime.get("confidence","?")}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'RegimeDetector', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] RegimeDetector FAILED: {str(e)[:80]}')

# -- 11. Full Pipeline: V3 -> Consensus -> Gate --
print('\n--- FULL PIPELINE (V3 -> Consensus -> Gate) ---')
for instr_type, ctx in [('stock', mock_stock_ctx), ('future', mock_context), ('option', mock_option_ctx)]:
    try:
        strats = get_strategies(instr_type)
        votes = []
        for s in strats:
            try:
                r = s.compute(ctx)
                if r.get('direction') in ('long', 'short') and r.get('confidence', 0) > 0:
                    votes.append({'name': s.name, 'strategy': s.name, 'direction': r['direction'],
                                  'confidence': float(r['confidence']), 'source': 'v3', 'diagnostics': {}})
            except: pass
        dir_, conf_, meta_ = compute_consensus(votes, [], 'ranging', ctx['ticker'], instr_type,
                                                ctx.get('current_price',0), 15.0, 5500)
        gr_ = gate.evaluate(ctx, dir_, conf_, votes, {'primary_regime': 'ranging', 'confidence': 0.7}, meta_)
        results['passed'] += 1
        print(f'   [OK] Pipeline({instr_type}): {len(strats)} strats, {len(votes)} active, '
              f'{dir_} @ {conf_:.3f}, gate={gr_["passed"]} ({gr_.get("reason","?")})')
    except Exception as e:
        results['failed'] += 1
        results['errors'].append({'strategy': f'Pipeline({instr_type})', 'type': instr_type,
                                   'error': str(e)[:200], 'traceback': traceback.format_exc()})
        print(f' [FAIL] Pipeline({instr_type}) FAILED: {str(e)[:80]}')

# -- 12. Test Settings Manager --
print('\n--- SETTINGS MANAGER ---')
from utils.settings_manager import get, get_all, set_many, reset as settings_reset, init as settings_init
from utils.settings_manager import ODTE_STRATEGY_WHITELIST, ODTE_STRATEGY_BLACKLIST

try:
    # Default values
    assert get("neutral_cooldown_active") == 6, f"default cooldown_active={get('neutral_cooldown_active')}"
    assert get("neutral_cooldown_confirmed") == 8
    assert get("neutral_cooldown_max") == 3
    assert get("sticky_counter_cycles") == 2
    # odte_mode may be 1/True or 0/False depending on disk state
    assert get("odte_mode") in (False, True, 0, 1), f"odte_mode={get('odte_mode')}"
    assert get("signal_age_decay_start_min") == 15
    assert get("signal_age_decay_half_min") == 60
    assert get("signal_age_decay_floor") == 0.50
    results['passed'] += 1
    print(f'   [OK] Settings defaults: all defaults match expected values')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'SettingsManager.defaults', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Settings defaults FAILED: {str(e)[:80]}')

try:
    # get_all returns all settings
    all_settings = get_all()
    assert isinstance(all_settings, dict)
    assert "neutral_cooldown_active" in all_settings
    assert "odte_mode" in all_settings
    results['passed'] += 1
    print(f'   [OK] Settings get_all: {len(all_settings)} keys returned')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'SettingsManager.get_all', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Settings get_all FAILED: {str(e)[:80]}')

try:
    # set_many with valid and invalid keys
    original = get("neutral_cooldown_active")
    result = set_many({"neutral_cooldown_active": 12, "nonexistent_key": 999})
    assert get("neutral_cooldown_active") == 12, f"expected 12, got {get('neutral_cooldown_active')}"
    assert "nonexistent_key" not in result, "unknown key should not be stored"
    results['passed'] += 1
    print(f'   [OK] Settings set_many: cooldown_active updated, unknown key rejected')
    # Restore
    set_many({"neutral_cooldown_active": original})
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'SettingsManager.set_many', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Settings set_many FAILED: {str(e)[:80]}')
    # Best-effort restore
    try: set_many({"neutral_cooldown_active": 6})
    except: pass

try:
    # Type coercion: strings coerced to int/float
    set_many({"neutral_cooldown_active": "10", "signal_age_decay_floor": "0.75"})
    assert isinstance(get("neutral_cooldown_active"), int), f"expected int, got {type(get('neutral_cooldown_active'))}"
    assert get("neutral_cooldown_active") == 10
    assert isinstance(get("signal_age_decay_floor"), float), f"expected float, got {type(get('signal_age_decay_floor'))}"
    assert get("signal_age_decay_floor") == 0.75
    results['passed'] += 1
    print(f'   [OK] Settings type coercion: str→int and str→float work')
    # Restore
    set_many({"neutral_cooldown_active": 6, "signal_age_decay_floor": 0.50})
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'SettingsManager.type_coercion', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Settings type coercion FAILED: {str(e)[:80]}')
    try: set_many({"neutral_cooldown_active": 6, "signal_age_decay_floor": 0.50})
    except: pass

try:
    # reset to defaults
    set_many({"neutral_cooldown_active": 99})
    settings_reset()
    assert get("neutral_cooldown_active") == 6, f"after reset expected 6, got {get('neutral_cooldown_active')}"
    results['passed'] += 1
    print(f'   [OK] Settings reset: restored to defaults')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'SettingsManager.reset', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Settings reset FAILED: {str(e)[:80]}')
    try: settings_reset()
    except: pass

try:
    # odte_mode toggle
    set_many({"odte_mode": True})
    assert get("odte_mode") == True
    set_many({"odte_mode": False})
    assert get("odte_mode") == False
    results['passed'] += 1
    print(f'   [OK] Settings odte_mode toggle: True→False works')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'SettingsManager.odte_mode', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Settings odte_mode FAILED: {str(e)[:80]}')
    try: set_many({"odte_mode": False})
    except: pass

# -- 13. Test Signal Persistence State Machine --
print('\n--- SIGNAL PERSISTENCE STATE MACHINE ---')
from engine.signal_persistence import (
    update as persist_update, reset as persist_reset,
    get_ticker_state, get_all_states, get_signal_health_score,
    get_signal_strength, get_signal_timeline, get_take_profit_events,
    get_age_decayed_confidence, update_strategy_performance,
    get_strategy_authority, get_strategy_performance_summary,
    get_price_signal_divergence, remove_ticker,
)

persist_reset()

try:
    # Build consensus meta for persistence
    cm = {
        "consensus_families": {"momentum": 0.5, "flow": 0.3},
        "consensus_family_count": 2,
        "consensus_agreement_cv": 0.3,
        "consensus_threshold": 0.2,
        "consensus_dominant_share": 0.5,
        "consensus_active_votes": 3,
        "consensus_weighted_long": 0.6,
        "consensus_weighted_short": 0.2,
        "consensus_net_score": 0.4,
        "consensus_counter_trend": "no",
        "consensus_conviction_tier": "silver",
    }
    # Cycle 1: conviction builds from net_score → watching
    flip = persist_update("TEST", "long", 0.6, 0.35, cm, cycle_id=1, current_price=5500, instrument_type="future")
    ts = get_ticker_state("TEST")
    assert ts["state"] == "watching", f"cycle 1: expected watching, got {ts['state']}"
    assert ts["conviction"] < 0.25
    results['passed'] += 1
    print(f'   [OK] Persistence escalation: cycle 1 → watching (conv={ts["conviction"]})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.escalation', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Persistence escalation FAILED: {str(e)[:80]}')

try:
    # Cycle 2: conviction builds, still watching
    persist_update("TEST", "long", 0.62, 0.38, cm, cycle_id=2, current_price=5502, instrument_type="future")
    ts = get_ticker_state("TEST")
    assert ts["state"] == "watching", f"cycle 2: expected watching, got {ts['state']}"
    assert ts["active_direction"] == "long"
    results['passed'] += 1
    print(f'   [OK] Persistence escalation: cycle 2 → watching (conv={ts["conviction"]})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.watching2', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Persistence cycle 2 FAILED: {str(e)[:80]}')

try:
    # Cycle 3: conviction reaches pending → thesis entry
    persist_update("TEST", "long", 0.65, 0.40, cm, cycle_id=3, current_price=5505, instrument_type="future")
    ts = get_ticker_state("TEST")
    assert ts["state"] == "pending", f"cycle 3: expected pending, got {ts['state']}"
    assert ts["active_direction"] == "long"
    assert ts["state_entry_price"] == 5505
    assert ts["has_thesis"] is True
    results['passed'] += 1
    print(f'   [OK] Persistence escalation: cycle 3 → pending, entry_price=5505')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.pending', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Persistence pending FAILED: {str(e)[:80]}')

try:
    # Cycle 4: conviction crosses 0.45 → active
    persist_update("TEST", "long", 0.70, 0.50, cm, cycle_id=4, current_price=5510, instrument_type="future")
    ts = get_ticker_state("TEST")
    assert ts["state"] == "active", f"cycle 4: expected active, got {ts['state']}"
    assert ts["conviction"] >= 0.45
    results['passed'] += 1
    print(f'   [OK] Persistence escalation: cycle 4 → active (conv={ts["conviction"]})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.active', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Persistence active FAILED: {str(e)[:80]}')

try:
    # Cycles 5-6: conviction crosses 0.70 → confirmed
    persist_update("TEST", "long", 0.75, 0.55, cm, cycle_id=5, current_price=5515, instrument_type="future")
    persist_update("TEST", "long", 0.80, 0.60, cm, cycle_id=6, current_price=5520, instrument_type="future")
    ts = get_ticker_state("TEST")
    assert ts["state"] == "confirmed", f"cycle 6: expected confirmed, got {ts['state']}"
    assert ts["conviction"] >= 0.70
    results['passed'] += 1
    print(f'   [OK] Persistence escalation: cycle 6 → confirmed (conv={ts["conviction"]})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.confirmed', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Persistence confirmed FAILED: {str(e)[:80]}')

try:
    # Cycle 7: 1st neutral → conviction drops → weakening (take-profit fires)
    flip = persist_update("TEST", "neutral", 0.0, 0.0, cm, cycle_id=7, current_price=5518, instrument_type="future")
    ts = get_ticker_state("TEST")
    assert ts["state"] == "weakening", f"1st neutral: expected weakening, got {ts['state']}"
    assert ts["conviction"] < 0.70
    assert ts["conviction_peak"] > ts["conviction"]
    results['passed'] += 1
    print(f'   [OK] Neutral: 1st neutral → weakening (conv={ts["conviction"]})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.sticky_stick', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Sticky stick FAILED: {str(e)[:80]}')

try:
    # Cycle 8: 2nd neutral → conviction drops further → still weakening
    flip = persist_update("TEST", "neutral", 0.0, 0.0, cm, cycle_id=8, current_price=5516, instrument_type="future")
    ts = get_ticker_state("TEST")
    assert ts["state"] == "weakening", f"sticky 2nd neutral: expected weakening, got {ts['state']}"
    assert ts["conviction"] < 0.45
    results['passed'] += 1
    print(f'   [OK] Neutral: 2nd neutral → weakening (conv={ts["conviction"]})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.sticky_weakening', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Sticky weakening FAILED: {str(e)[:80]}')

try:
    # Verify take-profit event was generated (from confirmed→weakening)
    tp_events = get_take_profit_events("TEST")
    assert len(tp_events) >= 1, f"expected take-profit event, got {len(tp_events)}"
    assert tp_events[0]["from_state"] == "confirmed"
    assert tp_events[0]["type"] == "take_profit"
    results['passed'] += 1
    print(f'   [OK] Take-profit event: confirmed→weakening generated TP event')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.take_profit', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Take-profit event FAILED: {str(e)[:80]}')

try:
    # Recovery from weakening: same direction → conviction recovers → active
    persist_update("TEST", "long", 0.60, 0.40, cm, cycle_id=9, current_price=5522, instrument_type="future")
    ts = get_ticker_state("TEST")
    assert ts["state"] == "active", f"recovery: expected active, got {ts['state']}"
    assert ts["conviction"] >= 0.45
    results['passed'] += 1
    print(f'   [OK] Recovery: weakening→{ts["state"]} on same direction')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.recovery', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Recovery FAILED: {str(e)[:80]}')

try:
    # Counter-direction: long→short on active signal
    persist_reset()
    for i in range(4):
        persist_update("COUNT", "long", 0.7, 0.5, cm, cycle_id=i+1, current_price=5500+i*5, instrument_type="future")
    ts = get_ticker_state("COUNT")
    assert ts["state"] == "active", f"expected active, got {ts['state']}"
    # 1st counter direction → conviction penalized → weakening
    flip = persist_update("COUNT", "short", 0.6, -0.4, cm, cycle_id=5, current_price=5490, instrument_type="future")
    ts = get_ticker_state("COUNT")
    assert ts["state"] == "weakening", f"counter: expected weakening, got {ts['state']}"
    assert ts["conviction"] < 0.45
    results['passed'] += 1
    print(f'   [OK] Counter-direction: 1st counter → weakening (conv={ts["conviction"]})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.counter_weakening', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Counter-weakening FAILED: {str(e)[:80]}')

try:
    # 2nd counter → conviction continues dropping → watching/none
    flip = persist_update("COUNT", "short", 0.65, -0.45, cm, cycle_id=6, current_price=5485, instrument_type="future")
    ts = get_ticker_state("COUNT")
    assert ts["state"] in ("watching", "weakening"), f"2nd counter: expected watching or weakening, got {ts['state']}"
    assert ts["conviction"] < 0.45
    results['passed'] += 1
    print(f'   [OK] Counter-direction: 2nd counter → {ts["state"]} (conv={ts["conviction"]})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.counter_downgrade', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Counter-downgrade FAILED: {str(e)[:80]}')

try:
    # get_all_states returns proper structure
    persist_reset()
    for i in range(4):
        persist_update("ALL", "long", 0.7, 0.5, cm, cycle_id=i+1, current_price=5500, instrument_type="future")
    all_st = get_all_states()
    assert "by_ticker" in all_st
    assert "by_state" in all_st
    assert "summary" in all_st
    assert all_st["by_ticker"]["ALL"]["state"] == "active"
    assert all_st["summary"]["active"] >= 1
    results['passed'] += 1
    print(f'   [OK] get_all_states: {all_st["summary"]}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.get_all_states', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] get_all_states FAILED: {str(e)[:80]}')

try:
    # get_signal_timeline
    tl = get_signal_timeline("ALL", max_cycles=5)
    assert isinstance(tl, list)
    assert len(tl) == 4
    assert tl[-1]["state"] == "active"
    results['passed'] += 1
    print(f'   [OK] Signal timeline: {len(tl)} cycles, last state={tl[-1]["state"]}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.timeline', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Timeline FAILED: {str(e)[:80]}')

try:
    # get_signal_strength
    strength = get_signal_strength("ALL")
    assert isinstance(strength, float) and 0 <= strength <= 1
    results['passed'] += 1
    print(f'   [OK] Signal strength: {strength:.3f}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.strength', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Strength FAILED: {str(e)[:80]}')

try:
    # get_age_decayed_confidence: fresh signal should have decay=1.0
    conf, decay = get_age_decayed_confidence("ALL")
    assert isinstance(conf, float)
    assert decay == 1.0, f"fresh signal decay should be 1.0, got {decay}"
    results['passed'] += 1
    print(f'   [OK] Age decay: conf={conf:.3f}, decay={decay} (fresh)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.age_decay', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Age decay FAILED: {str(e)[:80]}')

try:
    # get_price_signal_divergence
    div = get_price_signal_divergence("ALL", current_price=5400, entry_price=5500, atr=15.0)
    assert isinstance(div, dict)
    assert "diverged" in div
    # Price dropped ~100 points (~6.7 ATR) against long signal → diverged
    assert div["diverged"] == True, f"price divergence should be True for long signal with -100 point move"
    results['passed'] += 1
    print(f'   [OK] Price divergence: diverged={div["diverged"]}, distance={div.get("atr_distance","?")} ATR')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Persistence.divergence', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Divergence FAILED: {str(e)[:80]}')

    # Restore per-instrument defaults (tests above used old test-friendly values)
    settings_set({
        "future_min_pending": 8, "future_min_active": 15, "future_min_confirmed": 25,
        "future_cooldown_active": 20, "future_cooldown_confirmed": 35,
        "future_cooldown_max": 10, "future_sticky_counter": 6,
        "stock_min_pending": 5, "stock_min_active": 10, "stock_min_confirmed": 20,
        "stock_cooldown_active": 15, "stock_cooldown_confirmed": 25,
        "stock_cooldown_max": 8, "stock_sticky_counter": 4,
        "option_min_pending": 4, "option_min_active": 8, "option_min_confirmed": 14,
        "option_cooldown_active": 12, "option_cooldown_confirmed": 20,
        "option_cooldown_max": 6, "option_sticky_counter": 3,
    })
    persist_reset()  # Clean up before velocity and health score tests

# -- 14. Test Velocity Detection (ATR/cycle) --
print('\n--- VELOCITY DETECTION ---')
try:
    # Test 1: Slow drift below penalty threshold → no velocity penalty
    cm_v = {
        "consensus_families": {"momentum": 0.5, "flow": 0.3},
        "consensus_family_count": 2,
        "consensus_agreement_cv": 0.3,
        "consensus_threshold": 0.2,
        "consensus_dominant_share": 0.5,
        "consensus_active_votes": 3,
        "consensus_weighted_long": 0.6,
        "consensus_weighted_short": 0.2,
        "consensus_net_score": 0.4,
        "consensus_counter_trend": "no",
        "consensus_conviction_tier": "silver",
    }
    # Build long thesis at stable price
    for i in range(6):
        persist_update("VELO_SLOW", "long", 0.7, 0.4, cm_v, cycle_id=i+1, current_price=5500, instrument_type="future")
    ts = get_ticker_state("VELO_SLOW")
    assert ts["state"] in ("active", "confirmed"), f"expected active/confirmed, got {ts['state']}"
    pre_conv = ts["conviction"]

    # Slow drift: $2/cycle, ATR=15 → 0.13 ATR/cycle (below 0.4 penalty threshold)
    flip = persist_update("VELO_SLOW", "long", 0.65, 0.3, cm_v, cycle_id=7, current_price=5498, instrument_type="future", atr=15.0)
    ts = get_ticker_state("VELO_SLOW")
    # No velocity penalty — just normal decay (0.97)
    expected = pre_conv * 0.97
    assert ts["conviction"] >= expected * 0.95, f"slow drift: expected ~{expected:.4f}, got {ts['conviction']}"
    results['passed'] += 1
    print(f'   [OK] Slow drift (0.13 ATR/cycle): no velocity penalty, conv={ts["conviction"]:.4f}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Velocity.slow_drift', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Slow drift FAILED: {str(e)[:80]}')

try:
    # Test 2: Moderate adverse velocity > 0.4 ATR/cycle → heavy penalty (0.55x)
    persist_reset()
    for i in range(6):
        persist_update("VELO_MOD", "long", 0.7, 0.4, cm_v, cycle_id=i+1, current_price=5500, instrument_type="future")
    ts = get_ticker_state("VELO_MOD")
    pre_conv = ts["conviction"]

    # Drop $7/cycle, ATR=15 → 0.47 ATR/cycle (above 0.4 penalty threshold)
    flip = persist_update("VELO_MOD", "long", 0.6, 0.2, cm_v, cycle_id=7, current_price=5493, instrument_type="future", atr=15.0)
    ts = get_ticker_state("VELO_MOD")
    # Should be below what normal decay would give (0.55x penalty applied)
    no_penalty = pre_conv * 0.97
    assert ts["conviction"] < no_penalty * 0.85, f"moderate velocity: expected heavy penalty, got {ts['conviction']} vs {no_penalty}"
    results['passed'] += 1
    print(f'   [OK] Moderate adverse velocity (0.47 ATR/cycle): heavy penalty applied, conv={ts["conviction"]:.4f}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Velocity.moderate', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Moderate velocity FAILED: {str(e)[:80]}')

try:
    # Test 3: High adverse velocity > 0.8 ATR/cycle → instant crash (0.30x)
    persist_reset()
    for i in range(6):
        persist_update("VELO_HIGH", "long", 0.7, 0.4, cm_v, cycle_id=i+1, current_price=5500, instrument_type="future")
    ts = get_ticker_state("VELO_HIGH")
    pre_conv = ts["conviction"]

    # Drop $13/cycle, ATR=15 → 0.87 ATR/cycle (above 0.8 spike threshold)
    flip = persist_update("VELO_HIGH", "long", 0.5, 0.0, cm_v, cycle_id=7, current_price=5487, instrument_type="future", atr=15.0)
    ts = get_ticker_state("VELO_HIGH")
    # Should crash hard — well below moderate penalty level
    assert ts["conviction"] < pre_conv * 0.40, f"high velocity: expected crash, got {ts['conviction']} (pre={pre_conv})"
    results['passed'] += 1
    print(f'   [OK] High adverse velocity (0.87 ATR/cycle): instant crash, conv={ts["conviction"]:.4f}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Velocity.high', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] High velocity FAILED: {str(e)[:80]}')

try:
    # Test 4: Velocity building boost — new thesis with supportive velocity
    persist_reset()
    # Cycle 1: first snapshot to have prev_price for velocity
    persist_update("VELO_BOOST", "long", 0.5, 0.3, cm_v, cycle_id=1, current_price=5500, instrument_type="future", atr=15.0)
    # Cycle 2: price jumps up $10 from 5500, ATR=15 → 0.67 ATR/cycle, supports long
    persist_update("VELO_BOOST", "long", 0.7, 0.6, cm_v, cycle_id=2, current_price=5510, instrument_type="future", atr=15.0)
    # Cycle 3: price continues up $8 from 5510, velocity maintains support, prev_direction now matches
    persist_update("VELO_BOOST", "long", 0.75, 0.7, cm_v, cycle_id=3, current_price=5518, instrument_type="future", atr=15.0)
    ts = get_ticker_state("VELO_BOOST")
    # Building max cap should be above 0.40 thanks to velocity boost + direction match
    assert ts["conviction"] > 0.40, f"velocity boost: expected cap above 0.40, got {ts['conviction']}"
    assert ts["conviction"] <= 0.70, f"velocity boost: should not exceed velocity_building_max(0.70), got {ts['conviction']}"
    results['passed'] += 1
    print(f'   [OK] Velocity building boost: conviction={ts["conviction"]:.4f} (cap raised above 0.40)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Velocity.boost', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Velocity boost FAILED: {str(e)[:80]}')

persist_reset()

# -- 15a. Test PnL Guardian with ATR distance --
print('\n--- PNL GUARDIAN ATR DISTANCE ---')
try:
    # Build a confirmed thesis to test PnL Guardian
    cm_pnl = {
        "consensus_families": {"momentum": 0.5, "flow": 0.3},
        "consensus_family_count": 2,
        "consensus_agreement_cv": 0.3,
        "consensus_threshold": 0.2,
        "consensus_dominant_share": 0.5,
        "consensus_active_votes": 3,
        "consensus_weighted_long": 0.6,
        "consensus_weighted_short": 0.2,
        "consensus_net_score": 0.4,
        "consensus_counter_trend": "no",
        "consensus_conviction_tier": "silver",
    }
    # Test 1: SPX-level ($5500, ATR=$120): $450 profit → 3.75 ATR → 0.80 floor
    persist_reset()
    for i in range(6):
        persist_update("PNL_SPX", "long", 0.7, 0.5, cm_pnl, cycle_id=i+1, current_price=5500, instrument_type="future", atr=120.0)
    ts = get_ticker_state("PNL_SPX")
    assert ts["state"] == "confirmed", f"SPX PnL test: expected confirmed, got {ts['state']}"
    # $450 profit at 3.75 ATR (450/120) → should trigger 0.80 floor (>=3.0 ATR)
    persist_update("PNL_SPX", "long", 0.6, 0.3, cm_pnl, cycle_id=7, current_price=5950, instrument_type="future", atr=120.0)
    ts = get_ticker_state("PNL_SPX")
    assert ts["conviction"] >= 0.80, f"SPX PnL: expected 0.80 floor (3.75 ATR profit), got {ts['conviction']}"
    results['passed'] += 1
    print(f'   [OK] PnL Guardian ATR: SPX $300 profit @ 2.5 ATR → floor={ts["conviction"]:.4f} (>=0.80)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'PnLGuardian.SPX_profit', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] PnL Guardian SPX FAILED: {str(e)[:80]}')

try:
    # Test 2: Cheap stock ($5, ATR=$0.50): $0.50 profit → 1.0 ATR → mid-tier floor (0.15), not confirm floor (0.80)
    persist_reset()
    for i in range(6):
        persist_update("PNL_CHEAP", "long", 0.7, 0.5, cm_pnl, cycle_id=i+1, current_price=5.0, instrument_type="stock", atr=0.50)
    ts = get_ticker_state("PNL_CHEAP")
    pre_conv = ts["conviction"]
    # $0.50 profit at 1.0 ATR → mid-tier floor 0.15 (not old %-based floor of 0.20 which would fire at 10%)
    persist_update("PNL_CHEAP", "long", 0.5, 0.2, cm_pnl, cycle_id=7, current_price=5.50, instrument_type="stock", atr=0.50)
    ts = get_ticker_state("PNL_CHEAP")
    assert ts["conviction"] >= 0.15, f"Cheap stock PnL: expected >=0.15 (1.0 ATR), got {ts['conviction']}"
    results['passed'] += 1
    print(f'   [OK] PnL Guardian ATR: cheap stock $0.50 profit @ 1.0 ATR → floor={ts["conviction"]:.4f}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'PnLGuardian.cheap_stock', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] PnL Guardian cheap stock FAILED: {str(e)[:80]}')

try:
    # Test 3: Force exit at -2.5 ATR drawdown
    persist_reset()
    for i in range(6):
        persist_update("PNL_EXIT", "long", 0.7, 0.5, cm_pnl, cycle_id=i+1, current_price=5500, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_EXIT")
    pre_conv = ts["conviction"]
    # Drop $40, ATR=15 → -2.67 ATR drawdown → force exit (cap 0.05)
    persist_update("PNL_EXIT", "long", 0.3, -0.2, cm_pnl, cycle_id=7, current_price=5460, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_EXIT")
    assert ts["conviction"] <= 0.05, f"Force exit: expected <=0.05, got {ts['conviction']}"
    results['passed'] += 1
    print(f'   [OK] PnL Guardian ATR: drawdown -2.67 ATR → cap={ts["conviction"]:.4f} (<=0.05)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'PnLGuardian.force_exit', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] PnL Guardian force exit FAILED: {str(e)[:80]}')

try:
    # Test 4: ATR=0 fallback — old %-based behavior
    persist_reset()
    for i in range(6):
        persist_update("PNL_FALLBACK", "long", 0.7, 0.5, cm_pnl, cycle_id=i+1, current_price=5500, instrument_type="future", atr=0.0)
    ts = get_ticker_state("PNL_FALLBACK")
    pre_conv = ts["conviction"]
    # $200 profit, ATR=0 → falls back to %-based: 200/5500=3.6% → above 3.0% threshold → 0.80 floor
    persist_update("PNL_FALLBACK", "long", 0.6, 0.3, cm_pnl, cycle_id=7, current_price=5700, instrument_type="future", atr=0.0)
    ts = get_ticker_state("PNL_FALLBACK")
    assert ts["conviction"] >= 0.80, f"ATR=0 fallback: expected 0.80 floor, got {ts['conviction']}"
    results['passed'] += 1
    print(f'   [OK] PnL Guardian ATR=0 fallback: % based → floor={ts["conviction"]:.4f} (>=0.80)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'PnLGuardian.atr0_fallback', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] PnL Guardian ATR=0 fallback FAILED: {str(e)[:80]}')

# -- 15b. Test Building Factor Reset --
print('\n--- BUILDING FACTOR RESET ---')
try:
    # Build thesis, exit to none, then re-enter — building factor should be reduced
    persist_reset()
    cm_build = dict(cm_pnl)
    for i in range(6):
        persist_update("BUILD_RESET", "long", 0.7, 0.5, cm_build, cycle_id=i+1, current_price=5500, instrument_type="future")
    ts = get_ticker_state("BUILD_RESET")
    assert ts["state"] == "confirmed", f"expected confirmed, got {ts['state']}"
    # Force exit by large drawdown to trigger PnL Guardian exit
    for i in range(3):
        persist_update("BUILD_RESET", "long", 0.0, 0.0, cm_build, cycle_id=10+i, current_price=5490, instrument_type="future", atr=15.0)
    # Drawdown: 5490-5500 = -10, atr=15 → -0.67 ATR, not enough. Need -40 drop.
    persist_update("BUILD_RESET", "long", 0.0, -0.3, cm_build, cycle_id=13, current_price=5460, instrument_type="future", atr=15.0)
    ts = get_ticker_state("BUILD_RESET")
    # -40/15 = -2.67 ATR drawdown → PnL force exit → cap at 0.05 → state "none"
    assert ts["state"] == "none", f"expected none after drawdown, got {ts['state']} (conv={ts['conviction']})"
    # Re-enter — building factor should be low (memory trimmed to last 2)
    persist_update("BUILD_RESET", "long", 0.6, 0.4, cm_build, cycle_id=14, current_price=5495, instrument_type="future")
    ts = get_ticker_state("BUILD_RESET")
    # First re-entry cycle conviction should be modest (not instant active)
    assert ts["conviction"] < 0.30, f"building reset: expected low conviction, got {ts['conviction']}"
    results['passed'] += 1
    print(f'   [OK] Building factor reset: re-entry conviction={ts["conviction"]:.4f} (<0.30, was trimmed)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'BuildingReset.exit_reentry', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Building reset FAILED: {str(e)[:80]}')

# -- 15c. Test remove_ticker --
print('\n--- REMOVE TICKER ---')
try:
    persist_reset()
    for i in range(3):
        persist_update("RM_TEST", "long", 0.6, 0.4, cm_pnl, cycle_id=i+1, current_price=5500, instrument_type="future")
    ts = get_ticker_state("RM_TEST")
    assert ts["state"] != "none", f"expected non-none state, got {ts['state']}"
    remove_ticker("RM_TEST")
    ts = get_ticker_state("RM_TEST")
    assert ts["state"] == "none", f"expected none after remove, got {ts['state']}"
    assert ts["conviction"] == 0.0, f"expected 0 conviction after remove, got {ts['conviction']}"
    results['passed'] += 1
    print(f'   [OK] remove_ticker: state=none, conviction=0.0')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'RemoveTicker.basic', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] remove_ticker FAILED: {str(e)[:80]}')

# -- 15d. Test Direction Input Normalization --
print('\n--- DIRECTION NORMALIZATION ---')
try:
    persist_reset()
    for i in range(5):
        persist_update("DIR_TEST", "long", 0.6, 0.4, cm_pnl, cycle_id=i+1, current_price=5500, instrument_type="future")
    ts = get_ticker_state("DIR_TEST")
    assert ts["state"] in ("pending", "active"), f"expected pending/active, got {ts['state']}"
    active_dir_before = ts["active_direction"]
    # Invalid direction should be treated as neutral — active_direction preserved
    persist_update("DIR_TEST", "INVALID_DIR", 0.0, 0.0, cm_pnl, cycle_id=7, current_price=5500, instrument_type="future")
    ts = get_ticker_state("DIR_TEST")
    assert ts["active_direction"] == active_dir_before, f"expected {active_dir_before} (unchanged), got {ts['active_direction']}"
    results['passed'] += 1
    print(f'   [OK] Direction normalization: "INVALID_DIR" → neutral, direction={active_dir_before} preserved')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'DirectionNorm.invalid', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Direction normalization FAILED: {str(e)[:80]}')

# -- 15e. Test Oscillation Dampening (peak erosion) --
print('\n--- OSCILLATION DAMPENING ---')
try:
    persist_reset()
    for i in range(6):
        persist_update("OSC", "long", 0.7, 0.5, cm_pnl, cycle_id=i+1, current_price=5500, instrument_type="future")
    ts = get_ticker_state("OSC")
    initial_peak = ts["conviction_peak"]
    assert ts["state"] == "confirmed", f"expected confirmed, got {ts['state']}"
    # Multiple cycles with declining conviction → peak should erode slowly
    for i in range(3):
        persist_update("OSC", "long", 0.5, 0.2, cm_pnl, cycle_id=10+i, current_price=5490, instrument_type="future")
    ts = get_ticker_state("OSC")
    assert ts["conviction_peak"] < initial_peak, f"peak should erode below {initial_peak}, got {ts['conviction_peak']}"
    assert ts["conviction_peak"] > ts["conviction"], f"peak should be above conviction, got peak={ts['conviction_peak']} conv={ts['conviction']}"
    results['passed'] += 1
    print(f'   [OK] Oscillation dampening: peak eroded from {initial_peak:.4f} to {ts["conviction_peak"]:.4f}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Oscillation.peak_erosion', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Oscillation dampening FAILED: {str(e)[:80]}')

# -- 15f. Test PnL Guardian with short thesis (entry_dir, not direction) --
print('\n--- PNL GUARDIAN SHORT THESIS ---')
try:
    persist_reset()
    for i in range(10):
        persist_update("PNL_SHORT", "short", 0.7, -0.5, cm_pnl, cycle_id=i+1, current_price=5500, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_SHORT")
    assert ts["state"] in ("active", "confirmed"), f"short thesis: expected active/confirmed, got {ts['state']}"
    # Short thesis: price drops $40 (-2.67 ATR) = profit for short
    # Should trigger profit floor (2.0 ATR → 0.20 floor)
    persist_update("PNL_SHORT", "short", 0.5, -0.3, cm_pnl, cycle_id=11, current_price=5460, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_SHORT")
    assert ts["conviction"] >= 0.20, f"short profit: expected >=0.20 (2.67 ATR profit), got {ts['conviction']}"
    print(f'   [OK] PnL Guardian short thesis: $40 profit @ 2.67 ATR → floor={ts["conviction"]:.4f} (>=0.20)')
    results['passed'] += 1
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'PnLGuardian.short_thesis', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] PnL short thesis FAILED: {str(e)[:80]}')

try:
    # Short thesis: price rises $40 (+2.67 ATR) = loss for short
    # Should trigger drawdown cap (2.5 ATR exit → 0.05 cap)
    persist_reset()
    for i in range(6):
        persist_update("PNL_SHORT_LOSS", "short", 0.7, -0.5, cm_pnl, cycle_id=i+1, current_price=5500, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_SHORT_LOSS")
    pre_conv = ts["conviction"]
    persist_update("PNL_SHORT_LOSS", "short", 0.0, -0.2, cm_pnl, cycle_id=7, current_price=5540, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_SHORT_LOSS")
    assert ts["conviction"] <= 0.05, f"short loss: expected <=0.05, got {ts['conviction']}"
    print(f'   [OK] PnL Guardian short loss: $40 loss @ 2.67 ATR → cap={ts["conviction"]:.4f} (<=0.05)')
    results['passed'] += 1
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'PnLGuardian.short_loss', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] PnL short loss FAILED: {str(e)[:80]}')

try:
    # Short thesis profit with direction=long (current cycle diverges from thesis)
    # This tests the critical bug: PnL Guardian must use entry_dir ("short"),
    # not current direction ("long"). Price down = profit on short thesis.
    persist_reset()
    for i in range(6):
        persist_update("PNL_DIVERGE", "short", 0.7, -0.5, cm_pnl, cycle_id=i+1, current_price=5500, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_DIVERGE")
    # Current cycle says "long" but thesis is "short". Price drops $40 = profit for short thesis.
    # With old code (direction=="short"), direction="long" would not negate atr_dist.
    # atr_dist = (5460-5500)/15 = -2.67. For short entry: entry_dir="short" → negate → +2.67.
    # Profit check: 2.67 > 2.0 floor_th → floor 0.20
    persist_update("PNL_DIVERGE", "long", 0.3, 0.2, cm_pnl, cycle_id=7, current_price=5460, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_DIVERGE")
    assert ts["conviction"] >= 0.20, f"short thesis long direction: expected >=0.20 (2.67 ATR profit on short thesis), got {ts['conviction']}"
    print(f'   [OK] PnL Guardian direction divergence: short thesis, long cycle, profit → floor={ts["conviction"]:.4f} (>=0.20)')
    results['passed'] += 1
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'PnLGuardian.direction_divergence', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] PnL direction divergence FAILED: {str(e)[:80]}')

persist_reset()

# -- 15. Test Signal Health Score --
print('\n--- SIGNAL HEALTH SCORE ---')
try:
    # Build a solid confirmed signal with tight agreement
    cm_healthy = {
        "consensus_families": {"momentum": 0.4, "flow": 0.3, "gamma": 0.3},
        "consensus_family_count": 3,
        "consensus_agreement_cv": 0.2,  # tight
        "consensus_threshold": 0.2,
        "consensus_dominant_share": 0.35,  # well diversified
        "consensus_active_votes": 5,
        "consensus_weighted_long": 0.7,
        "consensus_weighted_short": 0.1,
        "consensus_net_score": 0.6,
        "consensus_counter_trend": "no",
        "consensus_conviction_tier": "gold",
    }
    for i in range(6):
        persist_update("HEALTH", "long", 0.8, 0.6 + i*0.05, cm_healthy, cycle_id=i+1, current_price=5500, instrument_type="future")

    health = get_signal_health_score("HEALTH")
    assert isinstance(health, dict)
    assert "health" in health
    assert "label" in health
    assert "factors" in health
    assert "warnings" in health
    # Should be robust (high net_score, tight CV, diverse families)
    assert health["health"] >= 50, f"expected robust health, got {health['health']} ({health['label']})"
    results['passed'] += 1
    print(f'   [OK] Health score: {health["health"]}/100 ({health["label"]}) factors={health["factors"]}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'HealthScore.robust', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Health score FAILED: {str(e)[:80]}')

try:
    # Test fragile signal: scattered confidence, single family dominant
    cm_fragile = {
        "consensus_families": {"momentum": 0.95},
        "consensus_family_count": 1,
        "consensus_agreement_cv": 0.7,  # scattered
        "consensus_threshold": 0.2,
        "consensus_dominant_share": 0.90,  # highly concentrated
        "consensus_active_votes": 1,
        "consensus_weighted_long": 0.3,
        "consensus_weighted_short": 0.1,
        "consensus_net_score": 0.25,  # just above threshold
        "consensus_counter_trend": "yes",  # counter-trend
        "consensus_conviction_tier": "bronze",
    }
    for i in range(2):
        persist_update("WEAK", "short", 0.3, -0.25, cm_fragile, cycle_id=i+1, current_price=5500, instrument_type="stock")
    health_w = get_signal_health_score("WEAK")
    assert health_w["health"] < 50, f"fragile signal should have health < 50, got {health_w['health']}"
    assert len(health_w["warnings"]) > 0, "fragile signal should have warnings"
    results['passed'] += 1
    print(f'   [OK] Fragile health: {health_w["health"]}/100 ({health_w["label"]}) warnings={health_w["warnings"]}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'HealthScore.fragile', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Fragile health FAILED: {str(e)[:80]}')

try:
    # Neutral signal → terminal health
    for i in range(2):
        persist_update("NEUT", "neutral", 0.0, 0.0, cm, cycle_id=i+1, current_price=5500, instrument_type="future")
    health_n = get_signal_health_score("NEUT")
    assert health_n["label"] == "terminal", f"neutral signal should be terminal, got {health_n['label']}"
    assert health_n["health"] == 0
    results['passed'] += 1
    print(f'   [OK] Terminal health: neutral signal → {health_n["label"]}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'HealthScore.terminal', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Terminal health FAILED: {str(e)[:80]}')

persist_reset()  # Clean up after health tests

# -- 15. Test ODTE Whitelist & Blacklist --
print('\n--- ODTE MODE & STRATEGY WHITELIST ---')
try:
    # ODTE whitelist should not overlap with blacklist
    overlap = ODTE_STRATEGY_WHITELIST & ODTE_STRATEGY_BLACKLIST
    assert len(overlap) == 0, f"ODTE whitelist/blacklist overlap: {overlap}"
    results['passed'] += 1
    print(f'   [OK] ODTE: no overlap between whitelist ({len(ODTE_STRATEGY_WHITELIST)}) and blacklist ({len(ODTE_STRATEGY_BLACKLIST)})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'ODTE.no_overlap', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] ODTE overlap FAILED: {str(e)[:80]}')

try:
    # Key gamma strategies must be in whitelist
    required_odte = ["gamma_exposure", "zero_dte_gamma", "vanna_charm_flow", "expiry_day_gamma"]
    for s in required_odte:
        assert s in ODTE_STRATEGY_WHITELIST, f"{s} missing from ODTE whitelist"
    results['passed'] += 1
    print(f'   [OK] ODTE: core gamma strategies present in whitelist')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'ODTE.core_strategies', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] ODTE core strategies FAILED: {str(e)[:80]}')

try:
    # Blacklisted strategies must NOT be in whitelist
    for s in ["theta_decay", "iv_rv_spread", "earnings_vol_arbitrage"]:
        assert s not in ODTE_STRATEGY_WHITELIST, f"{s} should not be in ODTE whitelist"
    results['passed'] += 1
    print(f'   [OK] ODTE: theta/earnings strategies correctly excluded')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'ODTE.exclusions', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] ODTE exclusions FAILED: {str(e)[:80]}')

try:
    # odte_mode should be off for whitelist-only test
    assert get("odte_mode") in (False, 0), f"odte_mode={get('odte_mode')}"
    # Verify whitelist is a set of strings
    assert all(isinstance(s, str) for s in ODTE_STRATEGY_WHITELIST)
    assert len(ODTE_STRATEGY_WHITELIST) >= 20, f"ODTE whitelist should have 20+ strategies, got {len(ODTE_STRATEGY_WHITELIST)}"
    results['passed'] += 1
    print(f'   [OK] ODTE: whitelist has {len(ODTE_STRATEGY_WHITELIST)} strategies (validated)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'ODTE.whitelist_size', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] ODTE whitelist size FAILED: {str(e)[:80]}')

# -- 16. Test Verdict System (compute_verdict) --
print('\n--- VERDICT SYSTEM ---')
from engine.signal_assembly import compute_verdict, summarize_gate_rejections

try:
    # Build signal_states for a confirmed signal
    signal_states_test = {
        "by_ticker": {
            "TEST_V": {
                "state": "confirmed",
                "age_decay": 1.0,
            }
        }
    }
    # Confirmed, gold tier, robust health, long, gate passed → should be STRONG BUY
    sig = {"ticker": "TEST_V", "direction": "long", "regime": "uptrend",
           "consensus_meta": {"consensus_conviction_tier": "gold", "consensus_counter_trend": "no"},
           "gate_passed": True, "instrument_type": "future"}
    health = {"label": "robust", "health": 85}
    verdict = compute_verdict(sig, health, signal_states_test)
    assert verdict == "STRONG LONG", f"expected STRONG LONG, got {verdict}"
    results['passed'] += 1
    print(f'   [OK] Verdict: confirmed+gold+robust+long → {verdict}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Verdict.strong', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Verdict strong FAILED: {str(e)[:80]}')

try:
    # HOLD verdict: confirmed state, neutral direction (sticky HOLD)
    sig_hold = {"ticker": "TEST_V", "direction": "neutral", "regime": "ranging",
                "consensus_meta": {"consensus_conviction_tier": "silver"},
                "gate_passed": False, "instrument_type": "future"}
    signal_states_hold = {"by_ticker": {"TEST_V": {"state": "active", "age_decay": 1.0}}}
    verdict_h = compute_verdict(sig_hold, {"label": "caution", "health": 55}, signal_states_hold)
    assert verdict_h == "HOLD", f"sticky active+neutral should be HOLD, got {verdict_h}"
    results['passed'] += 1
    print(f'   [OK] Verdict: sticky active+neutral → {verdict_h}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Verdict.hold', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Verdict HOLD FAILED: {str(e)[:80]}')

try:
    # NO ACTION: none/watching state
    sig_none = {"ticker": "TEST_V", "direction": "long", "regime": "ranging",
                "consensus_meta": {},"gate_passed": False, "instrument_type": "future"}
    st_none = {"by_ticker": {"TEST_V": {"state": "none", "age_decay": 1.0}}}
    verdict_n = compute_verdict(sig_none, None, st_none)
    assert verdict_n == "NO ACTION"
    results['passed'] += 1
    print(f'   [OK] Verdict: none state → NO ACTION')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Verdict.no_action', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Verdict NO_ACTION FAILED: {str(e)[:80]}')

try:
    # EXIT: terminal health on active signal
    sig_exit = {"ticker": "TEST_V", "direction": "long", "regime": "downtrend",
                "consensus_meta": {"consensus_conviction_tier": "bronze", "consensus_counter_trend": "yes"},
                "gate_passed": False, "instrument_type": "future"}
    st_exit = {"by_ticker": {"TEST_V": {"state": "active", "age_decay": 0.5}}}
    verdict_ex = compute_verdict(sig_exit, {"label": "terminal", "health": 10}, st_exit)
    assert verdict_ex in ("EXIT", "AVOID"), f"terminal health on active should be EXIT/AVOID, got {verdict_ex}"
    results['passed'] += 1
    print(f'   [OK] Verdict: terminal health → {verdict_ex}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Verdict.exit', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Verdict EXIT FAILED: {str(e)[:80]}')

try:
    # AVOID: fragile pending + gate failed
    sig_avoid = {"ticker": "TEST_V", "direction": "long", "regime": "ranging",
                 "consensus_meta": {},"gate_passed": False, "instrument_type": "future"}
    st_avoid = {"by_ticker": {"TEST_V": {"state": "pending", "age_decay": 1.0}}}
    verdict_av = compute_verdict(sig_avoid, {"label": "fragile", "health": 30}, st_avoid)
    assert verdict_av == "AVOID", f"fragile pending should be AVOID, got {verdict_av}"
    results['passed'] += 1
    print(f'   [OK] Verdict: fragile pending → {verdict_av}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Verdict.avoid', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Verdict AVOID FAILED: {str(e)[:80]}')

try:
    # WAIT: pending state with robust health (no gate issue)
    sig_wait = {"ticker": "TEST_V", "direction": "long", "regime": "uptrend",
                "consensus_meta": {"consensus_conviction_tier": "silver"},
                "gate_passed": True, "instrument_type": "future"}
    st_wait = {"by_ticker": {"TEST_V": {"state": "pending", "age_decay": 1.0}}}
    verdict_w = compute_verdict(sig_wait, {"label": "robust", "health": 80}, st_wait)
    assert verdict_w == "WAIT", f"pending+robust should be WAIT, got {verdict_w}"
    results['passed'] += 1
    print(f'   [OK] Verdict: pending+robust → {verdict_w}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Verdict.wait', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Verdict WAIT FAILED: {str(e)[:80]}')

try:
    # REDUCE: weakening state
    sig_red = {"ticker": "TEST_V", "direction": "long", "regime": "ranging",
               "consensus_meta": {"consensus_conviction_tier": "silver"},
               "gate_passed": True, "instrument_type": "future"}
    st_red = {"by_ticker": {"TEST_V": {"state": "weakening", "age_decay": 1.0}}}
    verdict_r = compute_verdict(sig_red, {"label": "caution", "health": 55}, st_red)
    assert verdict_r in ("REDUCE", "EXIT"), f"weakening should be REDUCE/EXIT, got {verdict_r}"
    results['passed'] += 1
    print(f'   [OK] Verdict: weakening → {verdict_r}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'Verdict.reduce', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Verdict REDUCE FAILED: {str(e)[:80]}')

# -- 17. Test Strategy Performance Tracking (True EWMA + Outcome-based) --
print('\n--- STRATEGY PERFORMANCE TRACKING (True EWMA) ---')
persist_reset()  # Clean slate for strategy perf tests
from engine.signal_persistence import evaluate_pending_predictions
try:
    # Record predictions (new API: ticker, strategy, direction, price, cycle)
    update_strategy_performance("TEST", "test_momentum", "long", 100.0, 1)
    update_strategy_performance("TEST", "test_momentum", "long", 101.0, 2)
    update_strategy_performance("TEST", "test_momentum", "long", 102.0, 3)
    update_strategy_performance("TEST", "test_momentum", "short", 103.0, 4)
    update_strategy_performance("TEST", "test_momentum", "long", 104.0, 5)

    # Evaluate: price moved up → 4 correct (long), 1 wrong (short)
    evaluate_pending_predictions({"TEST": {"current_price": 108.0}}, 6)

    auth = get_strategy_authority("test_momentum", 1.0)
    assert isinstance(auth, float), f"authority should be float, got {type(auth)}"
    assert 0.5 <= auth <= 2.0, f"authority should be in [0.5, 2.0], got {auth}"
    results['passed'] += 1
    print(f'   [OK] Strategy authority (EWMA): {auth:.3f} (4/5 correct, price up)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'StrategyPerf.authority', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Strategy authority FAILED: {str(e)[:80]}')

try:
    # Performance summary (EWMA-based, not win_rate)
    summary = get_strategy_performance_summary()
    assert isinstance(summary, dict)
    assert "test_momentum" in summary, f"expected test_momentum in summary, keys={list(summary.keys())}"
    assert "ewma" in summary["test_momentum"], f"expected ewma key, got {summary['test_momentum']}"
    assert summary["test_momentum"]["total_predictions"] == 5
    assert 0 < summary["test_momentum"]["ewma"] < 1, f"ewma should be between 0-1, got {summary['test_momentum']['ewma']}"
    results['passed'] += 1
    print(f'   [OK] Strategy performance: ewma={summary["test_momentum"]["ewma"]:.3f} total={summary["test_momentum"]["total_predictions"]}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'StrategyPerf.summary', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Performance summary FAILED: {str(e)[:80]}')

try:
    # Neutral predictions: not recorded at all (returns before storage)
    update_strategy_performance("TEST", "test_neutral", "neutral", 100.0, 1)
    evaluate_pending_predictions({"TEST": {"current_price": 101.0}}, 2)
    summary2 = get_strategy_performance_summary()
    assert "test_neutral" not in summary2, f"neutral predictions should not be tracked, got {list(summary2.keys())}"
    results['passed'] += 1
    print(f'   [OK] Strategy neutral: not tracked (correct)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'StrategyPerf.neutral', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Neutral tracking FAILED: {str(e)[:80]}')

try:
    # Unknown strategy returns static authority (no data yet)
    auth_new = get_strategy_authority("nonexistent_strat", 1.5)
    assert auth_new == 1.5, f"unknown strategy should return static authority, got {auth_new}"
    results['passed'] += 1
    print(f'   [OK] Unknown strategy authority: returns static ({auth_new})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'StrategyPerf.unknown', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Unknown authority FAILED: {str(e)[:80]}')

# -- 18. Test Gate Rejection Summary --
print('\n--- GATE REJECTION SUMMARY ---')
try:
    rejections = [
        {"gate_reason": "incomplete_data_cot_empty", "ticker": "A"},
        {"gate_reason": "incomplete_data_cot_empty", "ticker": "B"},
        {"gate_reason": "atr_too_high_0.0821", "ticker": "C"},
        {"gate_reason": "within_15min_of_close", "ticker": "D"},
        {"gate_reason": "stale_price_5s", "ticker": "E"},
    ]
    summary = summarize_gate_rejections(rejections)
    assert isinstance(summary, dict)
    assert summary.get("incomplete_data_cot_empty") == 2, f"expected 2 cot_empty, got {summary}"
    assert summary.get("atr_too_high") == 1
    assert summary.get("within") == 1, f"within_15min_of_close should bucket to 'within', got {summary}"
    assert summary.get("stale_price") == 1, f"stale_price_5s should bucket to 'stale_price', got {summary}"
    results['passed'] += 1
    print(f'   [OK] Gate summary: {summary}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'GateRejection.summary', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Gate summary FAILED: {str(e)[:80]}')

try:
    # Unknown reason defaults to "unknown"
    summary_unknown = summarize_gate_rejections([{"gate_reason": None}])
    assert "unknown" in summary_unknown
    results['passed'] += 1
    print(f'   [OK] Gate summary: None→unknown handled')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'GateRejection.unknown', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Gate unknown FAILED: {str(e)[:80]}')

# -- 19. Test Tick Persistence (save_checkpoint / load_checkpoint) and compute_tick_clusters --
print('\n--- TICK PERSISTENCE & TICK CLUSTERS ---')
import tempfile, shutil
from datetime import date
from pathlib import Path

# ── Save + load checkpoint ──
try:
    from engine.tick_engine import on_tick, get_tick_stats
    from engine.tick_engine import save_checkpoint, load_checkpoint, reset as tick_reset
    import engine.tick_engine as te

    _orig_ckpt_dir = te._CHECKPOINT_DIR
    _ckpt_tmpdir = tempfile.mkdtemp()
    te._CHECKPOINT_DIR = Path(_ckpt_tmpdir)

    tick_reset()
    now = time.time()
    for i in range(50):
        is_buy = random.random() > 0.5
        sz = random.randint(1, 20)
        lp = base_price + (random.random()-0.5)*2
        sp = random.uniform(0.1, 0.5)
        b, a = lp - sp, lp + sp
        on_tick('ES=F', 'FUT', a+0.05 if is_buy else b-0.05, b, a, 0, sz, now + i * 0.05)
    stats_before = get_tick_stats('ES=F')
    assert stats_before['cumulative_delta'] != 0, "need non-zero delta"
    assert stats_before.get('tick_buffer'), "tick_buffer should be populated"

    save_checkpoint()
    ckpt_files = list(Path(_ckpt_tmpdir).glob('*.json'))
    assert len(ckpt_files) >= 1, f"checkpoint file not created in {_ckpt_tmpdir}"

    # Reset state and reload
    tick_reset()
    stats_empty = get_tick_stats('ES=F')
    assert stats_empty == {}, "reset should clear ticker state"

    loaded = load_checkpoint()
    assert loaded == True, "load_checkpoint should return True"

    stats_after = get_tick_stats('ES=F')
    assert stats_after.get('cumulative_delta') == stats_before['cumulative_delta'], \
        f"delta mismatch: before={stats_before['cumulative_delta']}, after={stats_after.get('cumulative_delta')}"
    assert stats_after.get('total_buy_vol') == stats_before['total_buy_vol']
    assert stats_after.get('total_sell_vol') == stats_before['total_sell_vol']
    assert stats_after.get('vpin') == stats_before['vpin']
    assert len(stats_after.get('tick_buffer', [])) > 0, "tick_buffer should restore"

    # Clean up
    shutil.rmtree(_ckpt_tmpdir, ignore_errors=True)
    te._CHECKPOINT_DIR = _orig_ckpt_dir

    results['passed'] += 1
    print(f'   [OK] Persistence: delta={stats_after["cumulative_delta"]} '
          f'buy={stats_after["total_buy_vol"]} sell={stats_after["total_sell_vol"]} '
          f'vpin={stats_after["vpin"]:.3f} buffer={len(stats_after["tick_buffer"])}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickPersistence.save_load', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Persistence save/load FAILED: {str(e)[:80]}')
    try:
        shutil.rmtree(_ckpt_tmpdir, ignore_errors=True)
        te._CHECKPOINT_DIR = _orig_ckpt_dir
    except: pass

# ── Load non-existent checkpoint ──
try:
    import engine.tick_engine as te
    _orig_dir2 = te._CHECKPOINT_DIR
    _ckpt_tmpdir = tempfile.mkdtemp()
    te._CHECKPOINT_DIR = Path(_ckpt_tmpdir)

    result_no_file = load_checkpoint()
    assert result_no_file == False, f"no file should return False, got {result_no_file}"

    shutil.rmtree(_ckpt_tmpdir, ignore_errors=True)
    te._CHECKPOINT_DIR = _orig_dir2
    results['passed'] += 1
    print(f'   [OK] Persistence: no checkpoint file → returns False')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickPersistence.no_file', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Persistence no_file FAILED: {str(e)[:80]}')
    try:
        shutil.rmtree(_ckpt_tmpdir, ignore_errors=True)
        te._CHECKPOINT_DIR = _orig_dir2
    except: pass

# ── Load corrupted checkpoint ──
try:
    import engine.tick_engine as te
    _orig_dir3 = te._CHECKPOINT_DIR
    _ckpt_tmpdir = tempfile.mkdtemp()
    te._CHECKPOINT_DIR = Path(_ckpt_tmpdir)

    # Write invalid JSON
    bad_path = Path(_ckpt_tmpdir) / (date.today().isoformat() + '.json')
    bad_path.write_text('not valid json {{{')

    result_corrupt = load_checkpoint()
    assert result_corrupt == False, f"corrupt file should return False, got {result_corrupt}"

    shutil.rmtree(_ckpt_tmpdir, ignore_errors=True)
    te._CHECKPOINT_DIR = _orig_dir3
    results['passed'] += 1
    print(f'   [OK] Persistence: corrupt checkpoint → returns False')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickPersistence.corrupt', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Persistence corrupt FAILED: {str(e)[:80]}')
    try:
        shutil.rmtree(_ckpt_tmpdir, ignore_errors=True)
        te._CHECKPOINT_DIR = _orig_dir3
    except: pass

# ── compute_tick_clusters: empty buffer ──
try:
    from engine.futures_data import compute_tick_clusters

    result_empty = compute_tick_clusters([])
    assert result_empty == {}, f"empty buffer should return {{}}, got {result_empty}"
    results['passed'] += 1
    print(f'   [OK] TickClusters: empty buffer → empty dict')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickClusters.empty', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] TickClusters empty FAILED: {str(e)[:80]}')

# ── compute_tick_clusters: fewer than 5 ticks ──
try:
    result_few = compute_tick_clusters([{'price': 5500.0, 'size': 10, 'time': 1000.0, 'sign': 'buy'}] * 3)
    assert result_few == {}, f"< 5 ticks should return {{}}, got {result_few}"
    results['passed'] += 1
    print(f'   [OK] TickClusters: < 5 ticks → empty dict')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickClusters.few_ticks', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] TickClusters few ticks FAILED: {str(e)[:80]}')

# ── compute_tick_clusters: normal buffer with acceleration detection ──
try:
    now_ts = time.time()
    tick_buf = []
    # First half: 10 ticks at 5500.5, spaced 0.1s apart (slow)
    for i in range(10):
        tick_buf.append({'price': 5500.5, 'size': 5, 'time': now_ts + i * 0.1,
                         'sign': 'buy', 'confidence': 0.7})
    # Second half: 20 ticks at 5501.0, spaced 0.05s apart (faster = acceleration)
    for i in range(20):
        tick_buf.append({'price': 5501.0, 'size': 8, 'time': now_ts + 1.0 + i * 0.05,
                         'sign': 'buy', 'confidence': 0.8})

    result = compute_tick_clusters(tick_buf)
    assert result, f"expected non-empty result, got {result}"
    assert result['tick_count'] == 30
    assert 'acceleration' in result
    assert 'price_clusters' in result
    assert len(result['price_clusters']) >= 2, f"expected >=2 price clusters, got {result['price_clusters']}"
    # Second half is faster (0.05s vs 0.1s) → should detect acceleration up
    assert result['acceleration'].get('accelerating_up'), \
        f"expected acceleration up, got {result['acceleration']}"
    results['passed'] += 1
    print(f'   [OK] TickClusters: {result["tick_count"]} ticks, '
          f'accel_up={result["acceleration"]["accelerating_up"]}, '
          f'clusters={len(result["price_clusters"])}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickClusters.normal', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] TickClusters normal FAILED: {str(e)[:80]}')

# ── compute_tick_clusters: large prints detection ──
try:
    now_ts = time.time()
    tick_buf = []
    # 15 normal ticks (size 5 each)
    for i in range(15):
        tick_buf.append({'price': 5500.0, 'size': 5, 'time': now_ts + i * 0.1,
                         'sign': 'buy' if i % 2 == 0 else 'sell', 'confidence': 0.7})
    # 1 large buy print (size 50 >> 2x avg of 5)
    tick_buf.append({'price': 5501.5, 'size': 50, 'time': now_ts + 2.0,
                     'sign': 'buy', 'confidence': 0.9})

    result = compute_tick_clusters(tick_buf)
    assert result, f"expected non-empty result"
    assert len(result.get('large_prints', [])) > 0, f"expected large prints, got {result.get('large_prints')}"
    results['passed'] += 1
    print(f'   [OK] TickClusters: large prints detected ({len(result["large_prints"])} groups)')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickClusters.large_prints', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] TickClusters large_prints FAILED: {str(e)[:80]}')

# ── compute_tick_clusters: iceberg pattern detection ──
try:
    now_ts = time.time()
    iceberg_ticks = []
    # 4 buy ticks at same price with same size → iceberg
    for i in range(4):
        iceberg_ticks.append({'price': 5503.0, 'size': 15, 'time': now_ts + i * 0.1,
                              'sign': 'buy', 'confidence': 0.7})
    # 3 sell ticks at different price with same size → another iceberg
    for i in range(3):
        iceberg_ticks.append({'price': 5504.0, 'size': 15, 'time': now_ts + 0.5 + i * 0.1,
                              'sign': 'sell', 'confidence': 0.8})
    # 3 filler ticks to pass minimum
    for i in range(3):
        iceberg_ticks.append({'price': 5505.0, 'size': 3, 'time': now_ts + 1.0 + i * 0.1,
                              'sign': 'buy', 'confidence': 0.6})

    result_ice = compute_tick_clusters(iceberg_ticks)
    assert result_ice, f"expected non-empty result"
    assert 'icebergs' in result_ice, f"expected icebergs key, got {result_ice.keys()}"
    assert len(result_ice['icebergs']) >= 1, f"expected >=1 iceberg, got {result_ice['icebergs']}"
    results['passed'] += 1
    print(f'   [OK] TickClusters: {len(result_ice["icebergs"])} iceberg(s) detected')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickClusters.iceberg', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] TickClusters iceberg FAILED: {str(e)[:80]}')

# ── Load checkpoint with explicit date string ──
try:
    import engine.tick_engine as te
    _orig_dir4 = te._CHECKPOINT_DIR
    _ckpt_tmpdir = tempfile.mkdtemp()
    te._CHECKPOINT_DIR = Path(_ckpt_tmpdir)

    result_nonexistent = load_checkpoint(trading_date='2020-01-01')
    assert result_nonexistent == False, f"non-existent date should return False, got {result_nonexistent}"

    shutil.rmtree(_ckpt_tmpdir, ignore_errors=True)
    te._CHECKPOINT_DIR = _orig_dir4
    results['passed'] += 1
    print(f'   [OK] Persistence: explicit non-existent date → False')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickPersistence.explicit_date', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    print(f' [FAIL] Persistence explicit_date FAILED: {str(e)[:80]}')
    try:
        shutil.rmtree(_ckpt_tmpdir, ignore_errors=True)
        te._CHECKPOINT_DIR = _orig_dir4
    except: pass

# -- SUMMARY --
if '--json' in sys.argv:
    out = {
        'passed': results['passed'],
        'failed': results['failed'],
        'skipped': results['skipped'],
        'total': results['passed'] + results['failed'] + results['skipped'],
        'errors': results['errors'],
        'all_ok': results['failed'] == 0,
    }
    print()
    print('<<<JSON_START>>>')
    print(json.dumps(out, indent=2))
    print('<<<JSON_END>>>')
else:
    print()
    print('='*70)
    print('RESULTS SUMMARY')
    print('='*70)
    total = results['passed'] + results['failed'] + results['skipped']
    print(f'  Total tests:    {total}')
    print(f'  [OK] Passed:       {results["passed"]}')
    print(f'  [FAIL] Failed:       {results["failed"]}')
    print(f'    [-]  Skipped:       {results["skipped"]}')
    if results['errors']:
        print(f'\n  FAILURE DETAILS:')
        seen = set()
        for e in results['errors']:
            key = f"{e['strategy']}|{e['error'][:60]}"
            if key not in seen:
                seen.add(key)
                print(f'    [{e["type"]}] {e["strategy"]}: {e["error"][:120]}')
    if results['failed'] > 0:
        print(f'\n  Overall: [FAIL] SOME FAILED ({results["failed"]} failures)')
        sys.exit(1)
    else:
        print(f'\n  Overall: [OK] ALL PASSED')
