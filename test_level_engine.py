"""
Dedicated unit tests for engine/level_engine.py.

Covers:
  - Fibonacci computation with pivot-detected swings
  - Confluence zone detection with overlapping levels
  - Suggested entry scoring with multiple candidates
  - Proximity warning generation for all direction+scenario combinations
  - Key level aggregation with all level types
  - Level confluence confidence boost
  - Edge cases and utility functions

Run: python test_level_engine.py
"""
import sys, os, math, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import level_engine as le

results = {'passed': 0, 'failed': 0, 'skipped': 0, 'errors': []}
test_num = 0

def t(description):
    global test_num
    test_num += 1
    print(f'  [{test_num:02d}] {description}...', end=' ')

def ok():
    global results
    results['passed'] += 1
    print('PASS')

def fail(msg):
    global results
    results['failed'] += 1
    results['errors'].append({'test': test_num, 'error': msg})
    print(f'FAIL: {msg}')


# ═══════════════════════════════════════════════════════════════
# Helper: create synthetic OHLCV data
# ═══════════════════════════════════════════════════════════════

def make_uptrend_ohlcv(n=20):
    """Steady uptrend with clear pivot points."""
    bars = []
    p = 6000.0
    for i in range(n):
        o = p
        # Create a visible swing: dip at bar 5, peak at bar 15
        if i == 5:
            l = p - 50
            h = p + 10
            c = p + 5
        elif i == 15:
            h = p + 60
            l = p - 5
            c = p + 50
        elif i == 10:
            h = p + 15
            l = p - 30
            c = p - 10
        else:
            r = 5 + (i % 3) * 3
            h = p + r
            l = p - r * 0.6
            c = p + 2
        bars.append({'open': round(o, 2), 'high': round(h, 2), 'low': round(l, 2),
                      'close': round(c, 2), 'volume': 100000 + i * 1000})
        p = c
    return bars


def make_downtrend_ohlcv(n=20):
    """Steady downtrend with clear pivot points."""
    bars = []
    p = 6000.0
    for i in range(n):
        o = p
        if i == 5:
            h = p + 50
            l = p - 10
            c = p - 5
        elif i == 15:
            l = p - 60
            h = p + 5
            c = p - 50
        elif i == 10:
            l = p + 15
            h = p + 30
            c = p + 10
        else:
            r = 5 + (i % 3) * 3
            h = p + r * 0.6
            l = p - r
            c = p - 2
        bars.append({'open': round(o, 2), 'high': round(h, 2), 'low': round(l, 2),
                      'close': round(c, 2), 'volume': 100000 + i * 1000})
        p = c
    return bars


def make_ranging_ohlcv(n=20):
    """Sideways market."""
    bars = []
    p = 6000.0
    for i in range(n):
        o = p
        r = 15
        h = p + r
        l = p - r
        c = p + (i % 3 - 1) * 5
        bars.append({'open': round(o, 2), 'high': round(h, 2), 'low': round(l, 2),
                      'close': round(c, 2), 'volume': 100000})
        p = c
    return bars


# ═══════════════════════════════════════════════════════════════
# Tests: _find_recent_swing (pivot detection)
# ═══════════════════════════════════════════════════════════════

print('\n=== FIBONACCI SWING DETECTION ===')

t("Detects pivot swing in uptrend")
ohlcv = make_uptrend_ohlcv(20)
sh, sl, direction = le._find_recent_swing(ohlcv)
if sh <= 0 or sl <= 0:
    fail(f"swing_high={sh}, swing_low={sl}")
elif sh <= sl:
    fail(f"swing_high ({sh}) <= swing_low ({sl})")
elif direction not in ('up', 'down'):
    fail(f"direction={direction}, expected up or down")
else:
    ok()

t("Detects pivot swing in downtrend")
ohlcv = make_downtrend_ohlcv(20)
sh, sl, direction = le._find_recent_swing(ohlcv)
if sh <= 0 or sl <= 0:
    fail(f"swing_high={sh}, swing_low={sl}")
elif sh <= sl:
    fail(f"swing_high ({sh}) <= swing_low ({sl})")
else:
    ok()

t("Returns zeroes for insufficient data (< 5 bars)")
sh, sl, direction = le._find_recent_swing([{'high': 100, 'low': 99, 'close': 99.5}])
if sh != 0.0 or sl != 0.0:
    fail(f"expected zeros, got sh={sh}, sl={sl}")
else:
    ok()

t("Returns zeroes for empty OHLCV")
sh, sl, direction = le._find_recent_swing([])
if sh != 0.0:
    fail(f"expected 0, got {sh}")
else:
    ok()

t("Swing high is higher than swing low by meaningful amount")
ohlcv = make_uptrend_ohlcv(20)
sh, sl, direction = le._find_recent_swing(ohlcv)
if sh - sl < 10:
    fail(f"swing range too small: {sh - sl}")
else:
    ok()

t("Direction matches close position relative to swing midpoint")
ohlcv = make_uptrend_ohlcv(20)
sh, sl, direction = le._find_recent_swing(ohlcv)
mid = (sh + sl) / 2
last_close = ohlcv[-1]['close']
if direction == 'up' and last_close <= mid:
    fail(f"direction=up but close={last_close} <= mid={mid}")
elif direction == 'down' and last_close >= mid:
    fail(f"direction=down but close={last_close} >= mid={mid}")
else:
    ok()

# ═══════════════════════════════════════════════════════════════
# Tests: compute_fibonacci_levels
# ═══════════════════════════════════════════════════════════════

print('\n=== FIBONACCI COMPUTATION ===')

t("Computes all 5 retracement levels in uptrend")
ohlcv = make_uptrend_ohlcv(20)
fib = le.compute_fibonacci_levels(ohlcv, 6000)
ret = fib.get('retracements', {})
if len(ret) != 5:
    fail(f"expected 5 retracements, got {len(ret)}")
else:
    ok()

t("Computes all 4 extension levels in uptrend")
fib = le.compute_fibonacci_levels(ohlcv, 6000)
ext = fib.get('extensions', {})
if len(ext) != 4:
    fail(f"expected 4 extensions, got {len(ext)}")
else:
    ok()

t("Fibonacci 61.8% retracement is between swing high and low")
ohlcv = make_uptrend_ohlcv(20)
fib = le.compute_fibonacci_levels(ohlcv, 6000)
ret = fib.get('retracements', {})
sh = fib['swing_high']
sl = fib['swing_low']
fib618 = ret.get('fib_618', 0)
if fib618 <= sl or fib618 >= sh:
    fail(f"fib_618={fib618} not between {sl} and {sh}")
else:
    ok()

t("Returns empty dict for insufficient OHLCV data")
fib = le.compute_fibonacci_levels([{'high': 100, 'low': 99, 'close': 99.5}], 100)
if fib.get('retracements') != {} or fib.get('trend_direction') != 'none':
    fail(f"expected empty, got {fib}")
else:
    ok()

t("Fibonacci levels are rounded to 2 decimal places")
ohlcv = make_uptrend_ohlcv(20)
fib = le.compute_fibonacci_levels(ohlcv, 6000)
for key, val in fib.get('retracements', {}).items():
    if round(val, 2) != val:
        fail(f"{key}={val} not rounded to 2dp")
        break
else:
    ok()

t("Retracement levels are correctly ordered for trend direction")
ohlcv = make_uptrend_ohlcv(20)
fib = le.compute_fibonacci_levels(ohlcv, 6000)
ret = fib.get('retracements', {})
trend = fib.get('trend_direction', 'none')
# Verify all 5 levels exist
if len(ret) != 5:
    fail(f"expected 5 retracement levels, got {len(ret)}: {list(ret.keys())}")
elif trend not in ('up', 'down'):
    fail(f"unexpected trend direction: {trend}")
else:
    # In uptrend: retracements go from high downwards: fib_236 > fib_382 > fib_50 > fib_618 > fib_786
    # In downtrend: retracements go from low upwards: fib_236 < fib_382 < fib_50 < fib_618 < fib_786
    def check_order(keys, ascending):
        for i in range(len(keys) - 1):
            a, b = ret.get(keys[i]), ret.get(keys[i+1])
            if a is None or b is None:
                return False
            if ascending and a >= b:
                return False
            if not ascending and a <= b:
                return False
        return True
    fib_keys = ['fib_236', 'fib_382', 'fib_500', 'fib_618', 'fib_786']
    ascending = trend == 'down'
    if check_order(fib_keys, ascending):
        ok()
    else:
        vals = {k: ret.get(k) for k in fib_keys}
        fail(f"wrong order for {trend} trend: {vals}")


# ═══════════════════════════════════════════════════════════════
# Tests: _find_confluence_zones
# ═══════════════════════════════════════════════════════════════

print('\n=== CONFLUENCE ZONE DETECTION ===')

t("Detects 2 levels within 0.3% as a confluence zone")
levels = [
    {'price': 6000.00, 'type': 'fib_618', 'priority': 6, 'label': 'Fib 61.8%', 'side': 'support', 'source': 'fibonacci'},
    {'price': 6005.00, 'type': 'prior_day_low', 'priority': 7, 'label': 'Prior Day Low', 'side': 'support', 'source': 'price_structure'},
]
zones = le._find_confluence_zones(levels, 6000)
# 6000 vs 6005 diff = 5/6000 = 0.083% < 0.3% -> should cluster
if len(zones) != 1:
    fail(f"expected 1 zone, got {len(zones)}")
elif zones[0]['count'] != 2:
    fail(f"expected count=2, got {zones[0]['count']}")
else:
    ok()

t("Does not cluster levels more than 0.3% apart")
levels = [
    {'price': 6000.00, 'type': 'fib_618', 'priority': 6, 'label': 'Fib 61.8%', 'side': 'support', 'source': 'fibonacci'},
    {'price': 6100.00, 'type': 'sma_50', 'priority': 4, 'label': 'SMA 50', 'side': 'resistance', 'source': 'moving_average'},
]
zones = le._find_confluence_zones(levels, 6000)
# 6000 vs 6100 diff = 100/6000 = 1.67% > 0.3% -> should NOT cluster
if len(zones) != 0:
    fail(f"expected 0 zones, got {len(zones)}")
else:
    ok()

t("Single level produces no confluence zones")
levels = [
    {'price': 6000.00, 'type': 'poc', 'priority': 5, 'label': 'Volume POC', 'side': 'support', 'source': 'volume_profile'},
]
zones = le._find_confluence_zones(levels, 6000)
if len(zones) != 0:
    fail(f"expected 0 zones, got {len(zones)}")
else:
    ok()

t("Empty levels produces no zones")
zones = le._find_confluence_zones([], 6000)
if len(zones) != 0:
    fail(f"expected 0 zones, got {len(zones)}")
else:
    ok()

t("3 levels at same price produce confluence zone with count=3")
levels = [
    {'price': 6000.00, 'type': 'fib_618', 'priority': 6, 'label': 'Fib 61.8%', 'side': 'support', 'source': 'fibonacci'},
    {'price': 6000.50, 'type': 'prior_day_low', 'priority': 7, 'label': 'Prior Day Low', 'side': 'support', 'source': 'price_structure'},
    {'price': 6000.20, 'type': 'sma_20', 'priority': 3, 'label': 'SMA 20', 'side': 'support', 'source': 'moving_average'},
]
zones = le._find_confluence_zones(levels, 6000)
if len(zones) != 1:
    fail(f"expected 1 zone, got {len(zones)}")
elif zones[0]['count'] != 3:
    fail(f"expected count=3, got {zones[0]['count']}")
else:
    ok()

t("Confluence zone includes correct level types")
levels = [
    {'price': 6000.00, 'type': 'fib_618', 'priority': 6, 'label': 'Fib 61.8%', 'side': 'support', 'source': 'fibonacci'},
    {'price': 6002.00, 'type': 'prior_day_low', 'priority': 7, 'label': 'Prior Day Low', 'side': 'support', 'source': 'price_structure'},
]
zones = le._find_confluence_zones(levels, 6000)
if zones and 'fib_618' in zones[0]['types'] and 'prior_day_low' in zones[0]['types']:
    ok()
else:
    fail(f"expected types to include fib_618 and prior_day_low, got {zones[0].get('types', []) if zones else []}")

t("Zones are sorted by count (most confluence first)")
levels = [
    {'price': 6000.00, 'type': 'fib_618', 'priority': 6, 'label': 'Fib 61.8%', 'side': 'support', 'source': 'fibonacci'},
    {'price': 6001.00, 'type': 'prior_day_low', 'priority': 7, 'label': 'PD Low', 'side': 'support', 'source': 'price_structure'},
    {'price': 6100.00, 'type': 'sma_50', 'priority': 4, 'label': 'SMA 50', 'side': 'resistance', 'source': 'moving_average'},
    {'price': 6101.00, 'type': 'fib_236', 'priority': 3, 'label': 'Fib 23.6%', 'side': 'resistance', 'source': 'fibonacci'},
    {'price': 6100.50, 'type': 'poc', 'priority': 5, 'label': 'POC', 'side': 'resistance', 'source': 'volume_profile'},
]
zones = le._find_confluence_zones(levels, 6000)
if len(zones) >= 2:
    if zones[0]['count'] >= zones[1]['count']:
        ok()
    else:
        fail(f"zones not sorted by count: {[z['count'] for z in zones]}")
else:
    fail(f"expected at least 2 zones, got {len(zones)}")

# ═══════════════════════════════════════════════════════════════
# Tests: _compute_proximity_warning
# ═══════════════════════════════════════════════════════════════

print('\n=== PROXIMITY WARNING GENERATION ===')

t("LONG at resistance -> AT_RESISTANCE warning")
# Price=6000, Resistance=6005, distance = 5/6000 = 0.083% < 0.15% (AT_LEVEL_PCT)
warn = le._compute_proximity_warning('long', 6000.0, 5990.0, 6005.0)
if warn and 'AT_RESISTANCE' in warn:
    ok()
else:
    fail(f"expected AT_RESISTANCE, got {warn}")

t("LONG near resistance -> NEAR_RESISTANCE warning")
# Price=6000, Resistance=6025, distance = 25/6000 = 0.42% (between AT and NEAR)
warn = le._compute_proximity_warning('long', 6000.0, 5990.0, 6025.0)
if warn and 'NEAR_RESISTANCE' in warn:
    ok()
else:
    fail(f"expected NEAR_RESISTANCE, got {warn}")

t("LONG approaching resistance -> approaching_resistance info")
# Price=6000, Resistance=6050, distance = 50/6000 = 0.83% (between NEAR and CLOSE)
warn = le._compute_proximity_warning('long', 6000.0, 5990.0, 6050.0)
if warn and 'approaching_resistance' in warn:
    ok()
else:
    fail(f"expected approaching_resistance, got {warn}")

t("LONG far from resistance -> no warning")
warn = le._compute_proximity_warning('long', 6000.0, 5990.0, 6100.0)
# 100/6000 = 1.67% > 1% (CLOSE_LEVEL_PCT) -> no warning
if warn is None:
    ok()
else:
    fail(f"expected None, got {warn}")

t("SHORT at support -> AT_SUPPORT warning")
# Price=6000, Support=5993, distance = 7/6000 = 0.12% < 0.15%
warn = le._compute_proximity_warning('short', 6000.0, 5993.0, 6100.0)
if warn and 'AT_SUPPORT' in warn:
    ok()
else:
    fail(f"expected AT_SUPPORT, got {warn}")

t("SHORT near support -> NEAR_SUPPORT warning")
# Price=6000, Support=5975, distance = 25/6000 = 0.42%
warn = le._compute_proximity_warning('short', 6000.0, 5975.0, 6100.0)
if warn and 'NEAR_SUPPORT' in warn:
    ok()
else:
    fail(f"expected NEAR_SUPPORT, got {warn}")

t("SHORT approaching support -> approaching_support info")
warn = le._compute_proximity_warning('short', 6000.0, 5950.0, 6100.0)
if warn and 'approaching_support' in warn:
    ok()
else:
    fail(f"expected approaching_support, got {warn}")

t("SHORT with no support data -> no warning")
warn = le._compute_proximity_warning('short', 6000.0, None, 6100.0)
if warn is None:
    ok()
else:
    fail(f"expected None, got {warn}")

t("LONG with no resistance data -> no warning")
warn = le._compute_proximity_warning('long', 6000.0, 5990.0, None)
if warn is None:
    ok()
else:
    fail(f"expected None, got {warn}")

t("Neutral direction -> no warning (no direction to evaluate)")
warn = le._compute_proximity_warning('neutral', 6000.0, 5990.0, 6010.0)
if warn is None:
    ok()
else:
    fail(f"expected None for neutral, got {warn}")

# ═══════════════════════════════════════════════════════════════
# Tests: _compute_suggested_entry
# ═══════════════════════════════════════════════════════════════

print('\n=== SUGGESTED ENTRY SCORING ===')

t("LONG: suggests nearest support as entry")
supports = [
    {'price': 5950.00, 'type': 'fib_618', 'priority': 6, 'label': 'Fib 61.8%', 'side': 'support', 'source': 'fibonacci'},
    {'price': 5980.00, 'type': 'prior_day_low', 'priority': 7, 'label': 'PD Low', 'side': 'support', 'source': 'price_structure'},
]
resists = [
    {'price': 6020.00, 'type': 'pivot_r1', 'priority': 2, 'label': 'R1', 'side': 'resistance', 'source': 'price_structure'},
]
suggested, stype, quality = le._compute_suggested_entry('long', 6000.0, supports, resists, [], 'stock')
# 5980 is the nearest support, should be suggested
if suggested and suggested < 6000:
    ok()
else:
    fail(f"expected suggested < 6000, got {suggested}")

t("LONG: confluence zone preferred over single level")
supports = [
    {'price': 5940.00, 'type': 'fib_618', 'priority': 6, 'label': 'Fib 61.8%', 'side': 'support', 'source': 'fibonacci'},
]
confluence = [
    {'price': 5950.00, 'count': 3, 'types': ['fib_50', 'poc', 'sma_20'], 'max_priority': 5, 'distance_pct': 0.83},
]
suggested, stype, quality = le._compute_suggested_entry('long', 6000.0, supports, [], confluence, 'stock')
# Confluence zone at 5950 (0.83% below) should score higher than single level at 5940 (1% below)
if suggested == 5950.0:
    ok()
else:
    fail(f"expected confluence zone 5950, got {suggested}")

t("SHORT: suggests nearest resistance as entry")
supports = [
    {'price': 5990.00, 'type': 'sma_20', 'priority': 3, 'label': 'SMA 20', 'side': 'support', 'source': 'moving_average'},
]
resists = [
    {'price': 6030.00, 'type': 'prior_day_high', 'priority': 7, 'label': 'PD High', 'side': 'resistance', 'source': 'price_structure'},
    {'price': 6015.00, 'type': 'fib_382', 'priority': 5, 'label': 'Fib 38.2%', 'side': 'resistance', 'source': 'fibonacci'},
]
suggested, stype, quality = le._compute_suggested_entry('short', 6000.0, supports, resists, [], 'stock')
if suggested and suggested > 6000:
    ok()
else:
    fail(f"expected suggested > 6000, got {suggested}")

t("Returns market entry when no support/resistance available")
suggested, stype, quality = le._compute_suggested_entry('long', 6000.0, [], [], [], 'stock')
if stype == 'market' or stype == 'neutral':
    ok()
else:
    fail(f"expected market/neutral, got {stype}")

t("Returns market entry for neutral direction")
suggested, stype, quality = le._compute_suggested_entry('neutral', 6000.0, [], [], [], 'stock')
if stype == 'neutral':
    ok()
else:
    fail(f"expected neutral, got {stype}")

t("Quality score is higher for higher-priority level")
# Both at same distance below 0.995*price (so not filtered by market fallback)
supports_high = [
    {'price': 5940.00, 'type': 'gamma_flip', 'priority': 10, 'label': 'Gamma Flip', 'side': 'support', 'source': 'options_gex'},
]
supports_low = [
    {'price': 5940.00, 'type': 'fib_236', 'priority': 3, 'label': 'Fib 23.6%', 'side': 'support', 'source': 'fibonacci'},
]
s1, t1, q1 = le._compute_suggested_entry('long', 6000.0, supports_high, [], [], 'stock')
s2, t2, q2 = le._compute_suggested_entry('long', 6000.0, supports_low, [], [], 'stock')
if q1 > q2:
    ok()
else:
    fail(f"expected high-priority quality ({q1}) > low-priority ({q2})")

t("Max-distance filter: ignores levels beyond 2% for stocks")
far_support = [
    {'price': 5700.00, 'type': 'fib_618', 'priority': 6, 'label': 'Fib 61.8%', 'side': 'support', 'source': 'fibonacci'},
]
_s, _stype, _q = le._compute_suggested_entry('long', 6000.0, far_support, [], [], 'stock')
# 5700 is 5% below 6000 > 2% max, should fall back to market
if _stype == 'market':
    ok()
else:
    fail(f"expected market for far support, got type={_stype} price={_s}")

t("Max-distance filter: accepts level within 3% for futures")
far_support = [
    {'price': 5850.00, 'type': 'fib_50', 'priority': 5, 'label': 'Fib 50%', 'side': 'support', 'source': 'fibonacci'},
]
_s2, _stype2, _q2 = le._compute_suggested_entry('long', 6000.0, far_support, [], [], 'future')
# 5850 is 2.5% below 6000 < 3% max for futures, should suggest it
if _stype2 != 'market' and _s2 < 6000:
    ok()
else:
    fail(f"expected level entry for futures, got type={_stype2} price={_s2}")

# ═══════════════════════════════════════════════════════════════
# Tests: compute_level_confluence_boost
# ═══════════════════════════════════════════════════════════════

print('\n=== LEVEL CONFLUENCE BOOST ===')

t("Boost > 1.0 when strong confluence in signal direction")
# Strong confluence below price for long, resistance far away so no penalty
key_levels = {
    'nearest_support': 5980.0,
    'nearest_resistance': 6200.0,  # Far enough (3.3%) to avoid counter-directional penalty
    'confluence_zones': [
        {'price': 5980.0, 'count': 3, 'types': ['fib_618', 'prior_day_low', 'poc'], 'max_priority': 7, 'distance_pct': 0.3},
    ],
    'at_level': None,
}
boost, label = le.compute_level_confluence_boost('long', 6000.0, key_levels)
if boost >= 1.08:
    ok()
else:
    fail(f"expected boost >= 1.08, got {boost}")

t("Penalty < 1.0 when near counter-directional level")
key_levels = {
    'nearest_support': 5990.0,
    'nearest_resistance': 6005.0,
    'confluence_zones': [],
    'at_level': None,
}
boost, label = le.compute_level_confluence_boost('long', 6000.0, key_levels)
# at 6005 with price 6000 = 0.083% < AT_LEVEL_PCT -> penalty
if boost < 1.0:
    ok()
else:
    fail(f"expected boost < 1.0, got {boost}")

t("Returns 1.0 for neutral direction")
boost, label = le.compute_level_confluence_boost('neutral', 6000.0, {})
if boost == 1.0:
    ok()
else:
    fail(f"expected 1.0, got {boost}")

t("Returns 1.0 for empty key_levels")
boost, label = le.compute_level_confluence_boost('long', 6000.0, {})
if boost == 1.0:
    ok()
else:
    fail(f"expected 1.0, got {boost}")

# ═══════════════════════════════════════════════════════════════
# Tests: aggregate_key_levels (integration)
# ═══════════════════════════════════════════════════════════════

print('\n=== KEY LEVEL AGGREGATION ===')

t("Returns all expected top-level keys")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {'atr_14': 15.0, 'sma_20': 5990.0, 'sma_50': 5950.0},
}
result = le.aggregate_key_levels('ES=F', data, 6000.0, 'long', 'future')
expected_keys = ['levels', 'nearest_support', 'nearest_resistance', 'support_levels',
                  'resistance_levels', 'fibonacci', 'suggested_entry',
                  'suggested_entry_type', 'entry_zone_quality', 'proximity_warning',
                  'confluence_zones', 'at_level']
missing = [k for k in expected_keys if k not in result]
if missing:
    fail(f"missing keys: {missing}")
else:
    ok()

t("Returns empty result for zero price")
result = le.aggregate_key_levels('ES=F', {}, 0.0, 'long', 'stock')
if result['levels'] == [] and result['nearest_support'] is None:
    ok()
else:
    fail(f"expected empty result, got {result}")

t("Includes fibonacci levels in result")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {},
}
result = le.aggregate_key_levels('SPY', data, 6000.0, 'long', 'stock')
fib = result.get('fibonacci', {})
if fib.get('swing_high', 0) > 0 and fib.get('retracements', {}):
    ok()
else:
    fail(f"fibonacci missing from result: {fib}")

t("Includes prior day high/low levels")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {},
}
result = le.aggregate_key_levels('SPY', data, 6000.0, 'long', 'stock')
types = [l['type'] for l in result.get('levels', [])]
if 'prior_day_high' in types and 'prior_day_low' in types:
    ok()
else:
    fail(f"prior day HL missing from types: {types}")

t("Includes pivot R1/S1 levels")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {},
}
result = le.aggregate_key_levels('SPY', data, 6000.0, 'long', 'stock')
types = [l['type'] for l in result.get('levels', [])]
if 'pivot_r1' in types and 'pivot_s1' in types:
    ok()
else:
    fail(f"pivot levels missing from types: {types}")

t("Includes SMA levels when provided")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {'sma_20': 5990.0, 'sma_50': 5950.0},
}
result = le.aggregate_key_levels('SPY', data, 6000.0, 'long', 'stock')
types = [l['type'] for l in result.get('levels', [])]
if 'sma_20' in types and 'sma_50' in types:
    ok()
else:
    fail(f"SMA levels missing from types: {types}")

t("Includes gamma flip level when provided")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {},
    'gamma_flip_level': 6020.0,
}
result = le.aggregate_key_levels('SPY_OPT', data, 6000.0, 'long', 'option')
types = [l['type'] for l in result.get('levels', [])]
if 'gamma_flip' in types:
    ok()
else:
    fail(f"gamma_flip missing from types: {types}")

t("Includes gamma walls when provided")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {},
    'gamma_walls': [{'strike': 6050.0, 'gamma': 0.05, 'openInterest': 5000}],
}
result = le.aggregate_key_levels('SPY_OPT', data, 6000.0, 'long', 'option')
types = [l['type'] for l in result.get('levels', [])]
if 'gamma_wall' in types:
    ok()
else:
    fail(f"gamma_wall missing from types: {types}")

t("Includes GEX magnet when provided")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {},
    'gex_magnet': {'strike': 6080.0, 'direction': 'up', 'strength': 5000.0, 'distance_pct': 1.3},
}
result = le.aggregate_key_levels('SPY_OPT', data, 6000.0, 'long', 'option')
types = [l['type'] for l in result.get('levels', [])]
if 'gex_magnet' in types:
    ok()
else:
    fail(f"gex_magnet missing from types: {types}")

t("Nearest support is the highest level below price")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {'sma_20': 5950.0},
}
result = le.aggregate_key_levels('SPY', data, 6000.0, 'long', 'stock')
ns = result.get('nearest_support')
if ns is not None and ns < 6000 and ns > 5940:
    ok()
else:
    fail(f"nearest_support={ns}, expected below 6000")

t("Nearest resistance is the lowest level above price")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {},
}
result = le.aggregate_key_levels('SPY', data, 6000.0, 'long', 'stock')
nr = result.get('nearest_resistance')
# The uptrend data has prior day high and pivot R1 above 6000
if nr is not None and nr > 6000:
    ok()
else:
    fail(f"nearest_resistance={nr}, expected above 6000")

t("Proximity warning generated for long near resistance")
data = {
    'ohlcv': make_uptrend_ohlcv(25),
    'indicators': {'sma_20': 5990.0},
    'gamma_flip_level': 6008.0,  # Only 0.13% above = AT_RESISTANCE
}
result = le.aggregate_key_levels('SPY_OPT', data, 6000.0, 'long', 'option')
warn = result.get('proximity_warning')
if warn and 'RESISTANCE' in warn:
    ok()
else:
    fail(f"expected resistance warning, got {warn}")

t("No proximity warning for long with resistance far away")
# Craft data where ALL levels (fib, prior HL, pivots, SMAs) are below price.
# With no levels above price at all, no resistance warning can fire.
below_ohlcv = [{'high': 5910, 'low': 5890, 'close': 5900, 'open': 5895, 'volume': 100000} for _ in range(25)]
data = {
    'ohlcv': below_ohlcv,
    'indicators': {'sma_20': 5900.0, 'sma_50': 5850.0},
}
result = le.aggregate_key_levels('SPY', data, 6000.0, 'long', 'stock')
warn = result.get('proximity_warning')
if warn is None:
    ok()
else:
    fail(f"unexpected warning: {warn}")

# ═══════════════════════════════════════════════════════════════
# Report
# ═══════════════════════════════════════════════════════════════

print('\n' + '='*50)
print('LEVEL ENGINE TESTS: ' + str(results['passed']) + ' passed, ' + str(results['failed']) + ' failed')
print('='*50)

if results['failed'] > 0:
    print('\nFAILURES:')
    for e in results['errors']:
        print('  Test ' + str(e['test']) + ': ' + e['error'])

sys.exit(1 if results['failed'] > 0 else 0)
