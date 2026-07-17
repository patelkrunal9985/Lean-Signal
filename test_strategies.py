"""
Comprehensive strategy validation test harness.
Runs all V2/V3 strategies, consensus, gate, tick engine, and pipeline with synthetic data.
No IBKR connection needed.
"""
import sys, os, json, traceback, math, random, time
sys.path.insert(0, r'C:\Users\patel\OneDrive\Desktop\Projects\Lean Signals')

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

results = {'passed': 0, 'failed': 0, 'skipped': 0, 'errors': [], 'details': []}

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
            results['details'].append({
                'name': s.name, 'type': instr_type, 'direction': result['direction'],
                'confidence': round(float(result['confidence']), 4), 'passed': True, 'error': '',
            })
            if results['passed'] <= 5 or results['passed'] % 15 == 0:
                print(f'   [OK] {s.name:35s} {result["direction"]:7s} {result["confidence"]:.3f}')
        except Exception as e:
            results['failed'] += 1
            tb = traceback.format_exc()
            err_msg = str(e)[:200]
            results['errors'].append({'strategy': s.name, 'type': instr_type, 'error': err_msg, 'traceback': tb})
            results['details'].append({
                'name': s.name, 'type': instr_type, 'direction': 'neutral',
                'confidence': 0.0, 'passed': False, 'error': err_msg,
            })
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
        results['details'].append({
            'name': f'V2Registry({typ})', 'type': typ, 'direction': 'neutral',
            'confidence': 0.0, 'passed': True, 'error': '',
        })
        print(f'   [OK] V2Registry({typ}) returned {len(v2_results)} results')
    except Exception as e:
        results['failed'] += 1
        results['errors'].append({'strategy': f'V2Registry({typ})', 'type': typ, 'error': str(e)[:200], 'traceback': traceback.format_exc()})
        results['details'].append({
            'name': f'V2Registry({typ})', 'type': typ, 'direction': 'neutral',
            'confidence': 0.0, 'passed': False, 'error': str(e)[:200],
        })
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
    results['details'].append({
        'name': 'ConsensusCoordinator', 'type': 'future', 'direction': direction,
        'confidence': round(float(conf), 4), 'passed': True, 'error': '',
    })
    print(f'   [OK] Consensus: {direction} @ {conf:.3f}  (votes={len(sample_votes)}, net={meta.get("net_score","?")})')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'compute_consensus', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    results['details'].append({
        'name': 'ConsensusCoordinator', 'type': 'future', 'direction': 'neutral',
        'confidence': 0.0, 'passed': False, 'error': str(e)[:200],
    })
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
    results['details'].append({
        'name': 'SignalQualityGate', 'type': 'future', 'direction': 'neutral',
        'confidence': 0.0, 'passed': True, 'error': '',
    })
    print(f'   [OK] Gate: passed={gr["passed"]} reason={gr.get("reason","?")}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'SignalQualityGate.evaluate', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    results['details'].append({
        'name': 'SignalQualityGate', 'type': 'future', 'direction': 'neutral',
        'confidence': 0.0, 'passed': False, 'error': str(e)[:200],
    })
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
    results['details'].append({
        'name': 'TickEngine', 'type': 'future', 'direction': 'neutral',
        'confidence': 0.0, 'passed': True, 'error': '',
    })
    print(f'   [OK] TickEngine: delta={stats["cumulative_delta"]} buy={stats["total_buy_vol"]} sell={stats["total_sell_vol"]} vpin={stats["vpin"]:.3f}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'TickEngine', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    results['details'].append({
        'name': 'TickEngine', 'type': 'future', 'direction': 'neutral',
        'confidence': 0.0, 'passed': False, 'error': str(e)[:200],
    })
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
        results['details'].append({
            'name': f'EntryExit({instr})', 'type': instr, 'direction': 'long',
            'confidence': 0.6, 'passed': True, 'error': '',
        })
        print(f'   [OK] EntryExit({instr}): entry={levels.get("entry_price","?")} sl={levels.get("stop_loss","?")} tp={levels.get("take_profit","?")}')
    except Exception as e:
        results['failed'] += 1
        results['errors'].append({'strategy': f'EntryExit({instr})', 'type': instr, 'error': str(e)[:200], 'traceback': traceback.format_exc()})
        results['details'].append({
            'name': f'EntryExit({instr})', 'type': instr, 'direction': 'neutral',
            'confidence': 0.0, 'passed': False, 'error': str(e)[:200],
        })
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
    results['details'].append({
        'name': 'MarketBreadth', 'type': 'future', 'direction': 'neutral',
        'confidence': 0.0, 'passed': True, 'error': '',
    })
    print(f'   [OK] MarketBreadth: state={breadth.get("composite",{}).get("state","?")} '
          f'trend={breadth.get("breadth_trend","?")}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'MarketBreadth', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    results['details'].append({
        'name': 'MarketBreadth', 'type': 'future', 'direction': 'neutral',
        'confidence': 0.0, 'passed': False, 'error': str(e)[:200],
    })
    print(f' [FAIL] MarketBreadth FAILED: {str(e)[:80]}')

# -- 9. Test Strike Selector --
print('\n--- STRIKE SELECTOR ---')
from engine.strike_selector import recommend_strike
try:
    chain = mock_option_ctx.get('option_chain', {})
    strike_rec = recommend_strike('SPY', chain, base_price, 'long', 0.65, 0.165, 25.0, 0)
    assert isinstance(strike_rec, dict)
    results['passed'] += 1
    results['details'].append({
        'name': 'StrikeSelector', 'type': 'option', 'direction': 'long',
        'confidence': 0.65, 'passed': True, 'error': '',
    })
    print(f'   [OK] StrikeSelector: strike={strike_rec.get("recommended_strike","?")} win_rate={strike_rec.get("estimated_win_rate","?")}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'StrikeSelector', 'type': 'option', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    results['details'].append({
        'name': 'StrikeSelector', 'type': 'option', 'direction': 'neutral',
        'confidence': 0.0, 'passed': False, 'error': str(e)[:200],
    })
    print(f' [FAIL] StrikeSelector FAILED: {str(e)[:80]}')

# -- 10. Test Regime Detector --
print('\n--- REGIME DETECTOR ---')
from regime.detector import RegimeDetector
try:
    rd = RegimeDetector()
    regime = rd.detect(ohlcv, mock_context.get('indicators', {}))
    assert isinstance(regime, dict)
    results['passed'] += 1
    results['details'].append({
        'name': 'RegimeDetector', 'type': 'future', 'direction': 'neutral',
        'confidence': 0.0, 'passed': True, 'error': '',
    })
    print(f'   [OK] RegimeDetector: primary={regime.get("primary_regime","?")} confidence={regime.get("confidence","?")}')
except Exception as e:
    results['failed'] += 1
    results['errors'].append({'strategy': 'RegimeDetector', 'type': '-', 'error': str(e)[:200], 'traceback': traceback.format_exc()})
    results['details'].append({
        'name': 'RegimeDetector', 'type': 'future', 'direction': 'neutral',
        'confidence': 0.0, 'passed': False, 'error': str(e)[:200],
    })
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
        results['details'].append({
            'name': f'Pipeline({instr_type})', 'type': instr_type, 'direction': dir_,
            'confidence': round(float(conf_), 4), 'passed': True, 'error': '',
        })
        print(f'   [OK] Pipeline({instr_type}): {len(strats)} strats, {len(votes)} active, '
              f'{dir_} @ {conf_:.3f}, gate={gr_["passed"]} ({gr_.get("reason","?")})')
    except Exception as e:
        results['failed'] += 1
        results['errors'].append({'strategy': f'Pipeline({instr_type})', 'type': instr_type,
                                   'error': str(e)[:200], 'traceback': traceback.format_exc()})
        results['details'].append({
            'name': f'Pipeline({instr_type})', 'type': instr_type, 'direction': 'neutral',
            'confidence': 0.0, 'passed': False, 'error': str(e)[:200],
        })
        print(f' [FAIL] Pipeline({instr_type}) FAILED: {str(e)[:80]}')

# -- SUMMARY --
if '--json' in sys.argv:
    out = {
        'passed': results['passed'],
        'failed': results['failed'],
        'skipped': results['skipped'],
        'total': results['passed'] + results['failed'] + results['skipped'],
        'errors': results['errors'],
        'details': results['details'],
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
