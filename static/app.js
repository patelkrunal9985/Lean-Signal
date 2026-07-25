// ── Lean Signals Dashboard — Full State Management ──
// Every render function handles 4 states: loading, error, empty, data
// Signal flips require 2+ cycle confirmation via persistence engine
// Data freshness indicators on all displayed data

let _currentTab = 'signals';
let _currentSubTab = 'stock';
let _status = {};
let _autoRunActive = false;
let _pollTimer = null;
let _priceTimer = null;
let _flowTimer = null;
let _livePrices = {};
let _flowData = {};
let _notifyPermitted = false;
let _lastFlipCount = -1;
let _lastTpCount = -1;
let _firstLoad = true;
let _historyPages = [];   // paginated history data (merged with _status.history)
let _historyPageLoaded = 0;  // how many cycles have been fetched so far

// ── Sticky event persistence: TP banners and flip badges linger for N cycles ──
var _stickyTps = {};   // { ticker: { cyclesLeft, direction, verdict, timestamp } }
var _stickyFlips = {}; // { ticker: { cyclesLeft, from, to, score } }
var _STICKY_TTL = 5;   // cycles to persist after the event ends

function formatAge(sec) {
  if (!sec || sec <= 0) return '';
  if (sec < 60) return Math.round(sec) + 's';
  if (sec < 3600) return Math.round(sec / 60) + 'm';
  return Math.round(sec / 3600) + 'h';
}

function stateAge(ts) {
  if (!ts) return '';
  var age = (Date.now() / 1000) - ts;
  return formatAge(age);
}

function formatSignalTime(epochSec) {
  if (!epochSec || epochSec <= 0) return '—';
  var d = new Date(epochSec * 1000);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString();
}

function formatBigNum(n) {
  if (!n || n === 0) return '0';
  var abs = Math.abs(n);
  var sign = n < 0 ? '-' : '';
  if (abs >= 1e9) return sign + (abs / 1e9).toFixed(1) + 'B';
  if (abs >= 1e6) return sign + (abs / 1e6).toFixed(1) + 'M';
  if (abs >= 1e3) return sign + (abs / 1e3).toFixed(1) + 'K';
  return sign + abs.toFixed(0);
}

function formatTinyNum(n) {
  if (!n || n === 0) return '0';
  if (Math.abs(n) < 0.0001) return n.toExponential(2);
  if (Math.abs(n) < 0.01) return n.toFixed(6);
  return n.toFixed(4);
}

function dataFreshness(timestamp) {
  if (!timestamp) return { cls: 'stale', label: 'unknown' };
  var age = (Date.now() - new Date(timestamp).getTime()) / 1000;
  if (age < 15) return { cls: 'fresh', label: 'Live' };
  if (age < 120) return { cls: 'recent', label: Math.round(age) + 's ago' };
  return { cls: 'stale', label: '⚠ ' + Math.round(age / 60) + 'm ago' };
}

function toggleCollapse(bodyId, headerEl) {
  var body = document.getElementById(bodyId);
  if (!body) return;
  var icon = headerEl.querySelector('.collapse-icon');
  if (body.style.display === 'none') {
    body.style.display = 'block';
    if (icon) icon.textContent = '▲';
  } else {
    body.style.display = 'none';
    if (icon) icon.textContent = '▼';
  }
}

/* ═══════════════════════════════════════════════════════════════
   Order Flow Ticker
   ═══════════════════════════════════════════════════════════════ */

async function loadFlowData() {
  try {
    var controller = new AbortController();
    var timeout = setTimeout(function() { controller.abort(); }, 3000);
    var resp = await fetch('/api/order-flow-ticker', { signal: controller.signal });
    clearTimeout(timeout);
    if (resp.ok) _flowData = await resp.json();
    renderPriceTicker();
  } catch(e) { /* silent */ }
}

/* ═══════════════════════════════════════════════════════════════
   Desktop Notifications
   ═══════════════════════════════════════════════════════════════ */

function checkNotifyPermission() {
  if (!('Notification' in window)) return;
  _notifyPermitted = Notification.permission === 'granted';
  if (Notification.permission === 'default') {
    // Show a small prompt in the header
    var ctrls = document.querySelector('.header-controls');
    if (ctrls && !document.getElementById('notify-perm-btn')) {
      var btn = document.createElement('button');
      btn.id = 'notify-perm-btn';
      btn.className = 'notify-perm-btn';
      btn.textContent = '🔔 Enable Alerts';
      btn.onclick = function() {
        Notification.requestPermission().then(function(p) {
          _notifyPermitted = p === 'granted';
          btn.className = 'notify-perm-btn ' + p;
          btn.textContent = p === 'granted' ? '🔔 Alerts On' : '🔕 Denied';
        });
      };
      ctrls.appendChild(btn);
    }
  }
}

function sendDesktopNotification(title, body, tag) {
  if (!_notifyPermitted || !('Notification' in window)) return;
  try {
    var n = new Notification(title, { body: body, tag: tag, icon: '/static/favicon.ico' });
    setTimeout(function() { n.close(); }, 8000);
  } catch(e) { /* silent */ }
}

function showToast(message, type) {
  var container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  var toast = document.createElement('div');
  toast.className = 'toast ' + (type || 'flip-info');
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(function() { toast.remove(); }, 5000);
}

document.addEventListener('DOMContentLoaded', function() {
  var savedInterval = localStorage.getItem('lean_signals_interval');
  if (savedInterval) document.getElementById('settings-interval').value = savedInterval;
  var savedNotify = localStorage.getItem('lean_signals_desktop_notify') === 'true';
  document.getElementById('settings-desktop-notify').checked = savedNotify;

  loadStatus();
  _pollTimer = setInterval(loadStatus, 5000);
  // Live price ticker — polls lightweight /api/prices (reads cache, 0 IBKR slots)
  loadPrices();
  _priceTimer = setInterval(loadPrices, 2000);
  // Order flow ticker — cumulative delta from tick engine
  loadFlowData();
  _flowTimer = setInterval(loadFlowData, 5000);
  // Check notification permission
  checkNotifyPermission();
  // Data quality indicator
  loadDataQuality();
  setInterval(loadDataQuality, 30000);
  // Load slot lifetime settings from server
  loadSettings();
});

async function loadSettings() {
  try {
    var resp = await fetch('/api/settings');
    if (!resp.ok) return;
    var s = await resp.json();
    var     el = document.getElementById('settings-conviction-decay');
    if (el && s.conviction_decay !== undefined) el.value = s.conviction_decay;
    el = document.getElementById('settings-min-conviction');
    if (el && s.min_conviction !== undefined) el.value = s.min_conviction;
    el = document.getElementById('settings-thesis-horizon');
    if (el && s.thesis_horizon !== undefined) el.value = s.thesis_horizon;
    el = document.getElementById('settings-regime-invalidation-factor');
    if (el && s.regime_invalidation_factor !== undefined) el.value = s.regime_invalidation_factor;
    el = document.getElementById('settings-velocity-spike-atr');
    if (el && s.velocity_spike_atr !== undefined) el.value = s.velocity_spike_atr;
    el = document.getElementById('settings-velocity-penalty-atr');
    if (el && s.velocity_penalty_atr !== undefined) el.value = s.velocity_penalty_atr;
    el = document.getElementById('settings-velocity-building-max');
    if (el && s.velocity_building_max !== undefined) el.value = s.velocity_building_max;
    el = document.getElementById('settings-pnl-profit-floor-atr');
    if (el && s.pnl_profit_floor_atr !== undefined) el.value = s.pnl_profit_floor_atr;
    el = document.getElementById('settings-pnl-profit-confirm-atr');
    if (el && s.pnl_profit_confirm_atr !== undefined) el.value = s.pnl_profit_confirm_atr;
    el = document.getElementById('settings-pnl-force-exit-atr');
    if (el && s.pnl_force_exit_atr !== undefined) el.value = s.pnl_force_exit_atr;
    el = document.getElementById('settings-odte-mode');
    if (el && s.odte_mode !== undefined) el.checked = !!s.odte_mode;
  } catch(e) { /* silent */ }
}

async function loadDataQuality() {
  try {
    var resp = await fetch('/api/data-quality');
    if (!resp.ok) return;
    var dq = await resp.json();
    var el = document.getElementById('data-quality-indicator');
    if (!el) return;
    var issues = 0;
    if (!dq.ibkr_connected) issues++;
    if (dq.live_tickers < 3) issues++;
    if (dq.v2_strategies_loaded < 10) issues++;
    if (dq.last_cycle_errors > 0) issues++;
    var label = issues === 0 ? 'DQ: OK' : 'DQ: ' + issues + ' issue' + (issues > 1 ? 's' : '');
    var cls = issues === 0 ? 'dq-ok' : issues <= 2 ? 'dq-warn' : 'dq-err';
    el.textContent = label;
    el.className = 'dq-indicator ' + cls;
    el.title = 'IBKR: ' + (dq.ibkr_connected ? 'connected' : 'OFF') +
      ' | Live: ' + dq.live_tickers + ' tickers' +
      ' | V2: ' + dq.v2_strategies_loaded + ' V3: ' + dq.v3_strategies_loaded +
      ' | Signals: ' + dq.signal_states_tracked +
      ' | Health warnings: ' + dq.health_warnings;
  } catch(e) { /* silent */ }
}

function switchTab(name) {
  _currentTab = name;
  document.querySelectorAll('.tab').forEach(function(t) {
    t.classList.toggle('active', t.dataset.tab === name);
  });
  document.querySelectorAll('.tab-content').forEach(function(tc) {
    tc.classList.toggle('active', tc.id === 'tab-' + name);
  });
  if (name === 'watchlist') loadWatchlist();
  if (name === 'cheatsheet') renderCheatSheet();
  if (name === 'signals' && _status.last_cycle) renderSignals(_status.last_cycle);
  if (name === 'history') renderHistory();
}

function switchSubTab(tab, sub) {
  _currentSubTab = sub;
  var container = document.querySelector('#' + 'tab-' + tab);
  if (!container) return;
  container.querySelectorAll('.sub-tab').forEach(function(st) {
    st.classList.toggle('active', st.dataset.subtab === sub);
  });
  container.querySelectorAll('.signals-group').forEach(function(sg) {
    sg.classList.toggle('active', sg.id === 'signals-' + sub);
  });
  if (tab === 'history') renderHistory();
}

async function loadStatus() {
  try {
    var controller = new AbortController();
    var timeout = setTimeout(function() { controller.abort(); }, 8000);
    var resp = await fetch('/api/status', { signal: controller.signal });
    clearTimeout(timeout);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    _status = await resp.json();
    updateUI();
  } catch(e) {
    if (e.name === 'AbortError') {
      console.warn('Status fetch timed out (server busy)');
    } else {
      console.error('Status fetch failed:', e);
    }
  }
}

function _updateOdteBadge() {
  var odteEl = document.getElementById('odte-mode-badge');
  var odteActive = document.getElementById('settings-odte-mode')?.checked || false;
  if (!odteEl) {
    // Create badge element once
    odteEl = document.createElement('span');
    odteEl.id = 'odte-mode-badge';
    odteEl.className = 'odte-badge';
    document.getElementById('summary-bar-extra')?.appendChild(odteEl);
  }
  odteEl.textContent = odteActive ? '🧪 0DTE Mode' : '';
  odteEl.style.display = odteActive ? 'inline' : 'none';
}

function updateUI() {
  // ── Connection state ──
  var conn = _status.connection || {};
  var ibkrEl = document.getElementById('ibkr-status');
  if (conn.connected) {
    ibkrEl.textContent = 'IBKR Connected';
    ibkrEl.className = 'status-badge connected';
  } else {
    ibkrEl.textContent = 'IBKR Disconnected';
    ibkrEl.className = 'status-badge disconnected';
  }

  // ── Market hours ──
  var mhEl = document.getElementById('market-hours-badge');
  if (!mhEl) {
    mhEl = document.createElement('span');
    mhEl.id = 'market-hours-badge';
    mhEl.className = 'status-badge';
    document.getElementById('summary-bar-extra')?.prepend(mhEl);
  }
  var mh = _status.market_hours || {};
  var label = mh.label || (mh.open ? 'Market Open' : 'Market Closed');
  var isOpen = mh.open || mh.futures_open;
  mhEl.textContent = label;
  mhEl.className = 'status-badge ' + (isOpen ? 'connected' : 'disconnected');

  // ── Cycle state ──
  var cycleEl = document.getElementById('cycle-status');
  if (_status.cycle_in_progress) {
    cycleEl.textContent = 'Running...';
    cycleEl.className = 'status-badge running';
  } else if (_status.auto_run) {
    cycleEl.textContent = 'Auto-Run';
    cycleEl.className = 'status-badge connected';
  } else {
    cycleEl.textContent = 'Idle';
    cycleEl.className = 'status-badge idle';
  }

  // ── Auto-run button ──
  document.getElementById('auto-run-btn').textContent = _status.auto_run ? 'Stop' : 'Auto-Run';

  // ── Last cycle summary ──
  var last = _status.last_cycle;
  if (last) {
    document.getElementById('total-scanned').textContent = 'Scanned: ' + (last.tickers_scanned || 0);
    var sigCount = last.signals_count || 0;
    var flipCount = last.flip_count || 0;
    var flipPotCount = last.flip_count_potential || 0;
    var sigLabel = 'Signals: ' + sigCount;

    // Show significant flips
    if (flipCount > 0) {
      var flips = last.flips || {};
      var flipNames = Object.keys(flips);
      sigLabel += '  🔴 <span class="flip-badge major" title="' + flipNames.join(', ') + '">' + flipCount + ' major flip' + (flipCount > 1 ? 's' : '') + '</span>';
    }
    // Show potential flips (watching)
    if (flipPotCount > 0) {
      sigLabel += '  🔵 <span class="flip-badge potential">' + flipPotCount + ' watching</span>';
    }

    // ── Desktop notifications for new flips ──
    if (!_firstLoad && flipCount > _lastFlipCount && document.getElementById('settings-desktop-notify').checked) {
      sendDesktopNotification('⚠️ Major Signal Flip', flipCount + ' significant flip' + (flipCount > 1 ? 's' : '') + ' detected', 'flip-alert');
      showToast('🔴 ' + flipCount + ' major flip' + (flipCount > 1 ? 's' : '') + ' detected!', 'flip-major');
    }
    _lastFlipCount = flipCount;

    // ── Check for take-profit events ──
    var tpEvents = last.take_profit_events || [];
    if (!_firstLoad && tpEvents.length > _lastTpCount && document.getElementById('settings-desktop-notify').checked) {
      sendDesktopNotification('💰 Take Profit Opportunity', tpEvents.length + ' signal' + (tpEvents.length > 1 ? 's' : '') + ' weakening', 'tp-alert');
      showToast('💰 Take profit: ' + tpEvents.length + ' signal' + (tpEvents.length > 1 ? 's' : '') + ' weakening', 'take-profit');
    }
    _lastTpCount = tpEvents.length;
    _firstLoad = false;
    document.getElementById('total-signals').innerHTML = sigLabel;

    var elapsed = last.elapsed_seconds || 0;
    document.getElementById('cycle-elapsed').textContent = 'Last cycle: ' + elapsed.toFixed(1) + 's';

    // Data freshness
    if (last.timestamp) {
      var fresh = dataFreshness(last.timestamp);
      var freshEl = document.getElementById('data-freshness');
      if (!freshEl) {
        freshEl = document.createElement('span');
        freshEl.id = 'data-freshness';
        freshEl.className = 'freshness-badge';
        document.getElementById('summary-bar-extra')?.appendChild(freshEl);
      }
      freshEl.textContent = fresh.label;
      freshEl.className = 'freshness-badge ' + fresh.cls;
    }
  }

  // Update ODTE mode badge
  _updateOdteBadge();

  // Render current tab
  if (_currentTab === 'signals' && last && last.status === 'completed') {
    renderSignals(last);
  }
  if (_currentTab === 'history') {
    renderHistory();
  }
}

/* ═══════════════════════════════════════════════════════════════
   Live Price Ticker (polls /api/prices — reads cache, 0 IBKR slots)
   ═══════════════════════════════════════════════════════════════ */

async function loadPrices() {
  try {
    var controller = new AbortController();
    var timeout = setTimeout(function() { controller.abort(); }, 3000);
    var resp = await fetch('/api/prices', { signal: controller.signal });
    clearTimeout(timeout);
    if (resp.ok) _livePrices = await resp.json();
    renderPriceTicker();
  } catch(e) { /* silent — ticker is non-critical */ }
}

function renderPriceTicker() {
  var bar = document.getElementById('price-ticker-bar');
  if (!bar) return;
  // Show all tickers with live prices, stocks first then futures
  var entries = [];
  for (var t in _livePrices) {
    var p = _livePrices[t];
    if (p && p.price > 0) entries.push({ticker: t, price: p.price, change: p.change || 0});
  }
  entries.sort(function(a, b) {
    var aFut = a.ticker.indexOf('=F') >= 0, bFut = b.ticker.indexOf('=F') >= 0;
    if (aFut !== bFut) return aFut - bFut;
    return a.ticker.localeCompare(b.ticker);
  });
  var html = '';
  entries.forEach(function(e) {
    var cls = e.change > 0 ? 'up' : e.change < 0 ? 'down' : '';
    var arrow = e.change > 0 ? '▲' : e.change < 0 ? '▼' : '';
    html += '<span class="ticker-quote"><strong>' + e.ticker.replace('=F','') + '</strong> <span class="' + cls + '">' + e.price.toFixed(0) + ' ' + arrow + '</span></span>';
  });
  // ── Order flow deltas (from tick engine) ──
  if (_flowData && Object.keys(_flowData).length > 0) {
    html += '<span class="flow-section">Flow:</span>';
    var flows = ['ES=F','NQ=F','RTY=F','YM=F'];
    flows.forEach(function(t) {
      var f = _flowData[t];
      if (f && f.cumulative_delta) {
        var sign = f.cumulative_delta > 0 ? '+' : '';
        var bullBear = f.cumulative_delta > 0 ? 'bullish' : 'bearish';
        html += '<span class="ticker-quote"><strong>' + t.replace('=F','') + ' Δ</strong> <span class="flow-delta ' + bullBear + '">' + sign + formatBigNum(f.cumulative_delta) + '</span></span>';
      }
    });
  }
  bar.innerHTML = html || '<span style="color:var(--text-muted)">Waiting for price data...</span>';
}

/* ═══════════════════════════════════════════════════════════════
   Signal Rendering with 4-State Management
   ═══════════════════════════════════════════════════════════════ */

function _updateStickyEvents(signals, flips, flipPotentials) {
  // Decrement existing sticky TP counters; remove expired
  for (var t in _stickyTps) {
    _stickyTps[t].cyclesLeft--;
    if (_stickyTps[t].cyclesLeft <= 0) delete _stickyTps[t];
  }
  // Decrement existing sticky flip counters; remove expired
  for (var t in _stickyFlips) {
    _stickyFlips[t].cyclesLeft--;
    if (_stickyFlips[t].cyclesLeft <= 0) delete _stickyFlips[t];
  }

  // Register new TP events (weakening signals)
  (signals || []).forEach(function(s) {
    if (s.weakening && !_stickyTps[s.ticker]) {
      _stickyTps[s.ticker] = {
        cyclesLeft: _STICKY_TTL,
        direction: s.direction,
        verdict: s.verdict || '',
        timestamp: Date.now()
      };
    }
  });

  // Register new flip events; refresh existing ones while still live
  var flipsSeen = {};
  for (var ft in (flips || {})) { flipsSeen[ft] = true; }
  for (var ft in (flipPotentials || {})) { flipsSeen[ft] = true; }
  for (var ft in flipsSeen) {
    if (!_stickyFlips[ft]) {
      var fInfo = (flips || {})[ft] || (flipPotentials || {})[ft] || {};
      _stickyFlips[ft] = {
        cyclesLeft: _STICKY_TTL,
        from: fInfo.from || '',
        to: fInfo.to || '',
        score: fInfo.score || 0,
        potential: !!(flipPotentials || {})[ft],
        timestamp: Date.now()
      };
    } else {
      // Refresh: reset counter if flip is still active
      _stickyFlips[ft].cyclesLeft = _STICKY_TTL;
    }
  }
}

/* ═══════════════════════════════════════════════════════════════
   Card Grid Dashboard — Signal Rendering
   ═══════════════════════════════════════════════════════════════ */

// Tab-based groupings for card layout
var _SIGNAL_TABS = {
  indices: { label: 'Indices', tickers: ['ES=F', 'NQ=F', 'RTY=F', 'YM=F'] },
  commodities: { label: 'Commodities', tickers: ['GC=F', 'CL=F', 'VX=F'] },
  equities: { label: 'Equities', tickers: ['SPY', 'QQQ', 'SPY_OPT', 'QQQ_OPT'] }
};
var _currentSignalSubTab = 'indices';

var _GRID_TICKERS = [];
Object.values(_SIGNAL_TABS).forEach(function(g) { g.tickers.forEach(function(t) { _GRID_TICKERS.push(t); }); });

var _gridInitialized = false;
var _cellCache = {};

// ── Cleanup stale frontend state for removed tickers (P2.14) ──
function _cleanupRemovedTickers(activeTickers) {
  // activeTickers: array of ticker strings currently in the grid
  var keepSet = {};
  activeTickers.forEach(function(t) { keepSet[t] = true; });
  // Clean _stickyTps
  for (var t in _stickyTps) {
    if (!keepSet[t]) delete _stickyTps[t];
  }
  // Clean _stickyFlips
  for (var t in _stickyFlips) {
    if (!keepSet[t]) delete _stickyFlips[t];
  }
  // Clean _cellCache (keep grid tickers)
  for (var t in _cellCache) {
    if (!keepSet[t]) delete _cellCache[t];
  }
}

function _getDirectionArrow(dir) {
  return dir === 'long' ? '\u25b2' : dir === 'short' ? '\u25bc' : '\u2013';
}

function _getDirClass(dir) {
  return dir === 'long' ? 'long' : dir === 'short' ? 'short' : 'neutral';
}

// ── Format suggested entry level type for display ──
function _formatLevelType(type) {
  if (!type || type === 'market' || type === 'none') return '';
  var labels = {
    'fib_236': 'Fib 23.6%', 'fib_382': 'Fib 38.2%', 'fib_50': 'Fib 50%',
    'fib_618': 'Fib 61.8%', 'fib_786': 'Fib 78.6%',
    'fib_1272': 'Fib 127.2%', 'fib_1618': 'Fib 161.8%',
    'prior_day_high': 'Prior Day High', 'prior_day_low': 'Prior Day Low',
    'prior_week_high': 'Prior Week High', 'prior_week_low': 'Prior Week Low',
    'gamma_flip': 'Gamma Flip', 'gamma_wall': 'Gamma Wall',
    'gex_magnet': 'GEX Magnet', 'poc': 'Volume POC',
    'value_area_high': 'VA High', 'value_area_low': 'VA Low',
    'sma_20': 'SMA 20', 'sma_50': 'SMA 50',
    'pivot_r1': 'Pivot R1', 'pivot_s1': 'Pivot S1',
    'confluence_zone': 'Confluence Zone'
  };
  return labels[type] || type.replace(/_/g, ' ');
}

// ── Build synthetic signal from gate evaluation (no signal generated but gate data exists) ──
function _buildSyntheticSignal(gate) {
  var meta = gate.consensus_meta || {};
  var dir = gate.direction || 'neutral';
  var conf = gate.consensus_confidence || 0;
  var tier = (meta && meta.consensus_conviction_tier) ? meta.consensus_conviction_tier : 'bronze';
  var verdict = 'NO ACTION';
  if (dir !== 'neutral' && conf > 0) {
    if (tier === 'platinum' || tier === 'gold') {
      verdict = 'STRONG ' + (dir === 'long' ? 'BUY' : 'SELL');
    } else if (tier === 'silver') {
      verdict = dir === 'long' ? 'BUY' : 'SELL';
    } else {
      verdict = conf > 0.5 ? (dir === 'long' ? 'BUY' : 'SELL') : 'HOLD';
    }
  }
  var strategies = (gate.strategy_votes || []).filter(function(v) { return v.confidence > 0; }).map(function(v) {
    return { name: v.name || v.strategy || '?', direction: v.direction || 'neutral', confidence: v.confidence || 0, reasoning: '' };
  });
  return {
    ticker: gate.ticker, instrument_type: gate.instrument_type || 'stock', direction: dir,
    verdict: verdict, confidence: conf, regime: gate.regime || 'unknown',
    current_price: gate.current_price || 0, gate_passed: gate.gate_passed || false,
    gate_reason: gate.gate_reason || '', consensus_meta: meta, strategies: strategies,
    time_window: gate.time_window || '', vwap_position: gate.vwap_position || '',
    entry_price: 0, stop_loss: 0, take_profit: 0, risk_reward: 0,
  };
}

// ── Create a single ticker card (runs once per ticker) ──
function _createTickerCell(ticker) {
  var cell = document.createElement('div');
  cell.id = 'cell-' + ticker.replace(/=/g, '-').replace(/_/g, '-');
  cell.className = 'ticker-cell state-none';
  cell.setAttribute('data-ticker', ticker);

  var isOption = ticker.indexOf('_OPT') >= 0;
  var isFuture = ticker.indexOf('=F') >= 0;
  var typeLabel = isOption ? 'opt' : isFuture ? 'fut' : 'stk';

  cell.innerHTML =
    // Grid area: verdict
    '<div class="grid-verdict">' +
      '<div style="display:flex;align-items:center;gap:6px;width:100%">' +
        '<span class="verdict-badge no-action">NO ACTION</span>' +
        '<span class="flip-tp-badge"></span>' +
        '<span class="actionability low">Act: --</span>' +
      '</div>' +
      '<div class="reversal-banner" style="display:none">' +
        '<span class="reversal-arrow">\u21BB</span>' +
        '<span class="reversal-label">REVERSAL</span>' +
        '<span class="reversal-dir"></span>' +
        '<span class="reversal-level"></span>' +
      '</div>' +
    '</div>' +
    // Grid area: header (ticker + badges | direction)
    '<div class="grid-header">' +
      '<div>' +
        '<span class="ticker-name">' + ticker.replace('=F','').replace('_OPT','') + '</span>' +
        '<span class="instrument-badge">' + typeLabel + '</span>' +
        '<span class="state-badge none">NONE</span>' +
        '<span class="thesis-age-badge" style="display:none"></span>' +
        '<span class="regime-badge">--</span>' +
      '</div>' +
      '<span class="direction-badge neutral">\u2013 NEUTRAL</span>' +
    '</div>' +
    // Grid area: conviction meter
    '<div class="grid-conv">' +
      '<div class="conviction-meter">' +
        '<span class="tier-badge bronze">BRONZE</span>' +
        '<span class="meter-pct bronze">0.0%</span>' +
        '<div class="meter-bar bronze" style="width:5%"></div>' +
        '<span class="trend-indicator">\u2013</span>' +
      '</div>' +
    '</div>' +
    // Grid area: PnL (always reserved, shows -- when no thesis)
    '<div class="grid-pnl">' +
      '<div class="row-pnl" style="visibility:hidden">' +
        '<span class="pnl-label">Entry</span><span class="pnl-val">$--</span>' +
        '<span class="pnl-label">Now</span><span class="pnl-val">$--</span>' +
        '<span class="pnl-pct pnl-flat">0.00%</span>' +
        '<span class="pnl-dollar pnl-flat">$0.00</span>' +
      '</div>' +
    '</div>' +
    // Grid area: stats (score, conf, strats)
    '<div class="grid-stats">' +
      '<span>Ns: <strong>--</strong></span>' +
      '<span>Conf: <strong>--</strong></span>' +
      '<span>Strats: <strong>0/0</strong></span>' +
    '</div>' +
    // Grid area: live price (dedicated row above levels)
    '<div class="grid-price">' +
      '<span class="level-pair"><span class="level-label">Price</span><span class="level-val price">$--</span></span>' +
    '</div>' +
    // Grid area: levels (entry, SL, TP, R:R + ATR) — each pair in a fixed container
    '<div class="grid-levels">' +
      '<span class="level-pair"><span class="level-label">Entry</span><span class="level-val">$--</span></span>' +
      '<span class="level-pair"><span class="level-label">SL</span><span class="level-val sl">$--</span></span>' +
      '<span class="level-pair"><span class="level-label">TP</span><span class="level-val tp">$--</span></span>' +
      '<span class="level-pair"><span class="level-label">R:R</span><span class="level-val rr">--</span></span>' +
      '<span class="level-pair"><span class="level-label">ATR</span><span class="level-val">$--</span></span>' +
    '</div>' +
    // Grid area: limit entry (always reserved)
    '<div class="grid-limit">' +
      '<span class="level-label limit-label">\u{1F3AF} Limit</span>' +
      '<span class="level-val limit">$--</span>' +
      '<span class="limit-type"></span>' +
    '</div>' +
    // Grid area: support/resistance
    '<div class="grid-sr">' +
      '<div class="row-sr">' +
        '<span class="sr-col support-col">' +
          '<span class="sr-label">\u25B2 S:</span>' +
          '<span class="sr-val">$--</span>' +
          '<span class="sr-dist"></span>' +
        '</span>' +
        '<span class="sr-col resistance-col">' +
          '<span class="sr-label">\u25BC R:</span>' +
          '<span class="sr-val">$--</span>' +
          '<span class="sr-dist"></span>' +
        '</span>' +
      '</div>' +
    '</div>' +
    // Grid area: bottom bar (gate, ns, prox — styled like level-pairs)
    '<div class="grid-bottom">' +
      '<span class="level-pair"><span class="level-label">Gate</span><span class="level-val">--</span></span>' +
      '<span class="level-pair"><span class="level-label">Ns</span><span class="level-val">--</span></span>' +
      '<span class="proximity-warn"></span>' +
    '</div>';

  cell.onclick = function() {
    var last = _status.last_cycle;
    if (!last) return;
    var allSigs = [];
    if (last.signals) {
      ['stock','future','option'].forEach(function(type) {
        (last.signals[type] || []).forEach(function(s) { allSigs.push(s); });
      });
    }
    var sig = allSigs.find(function(s) { return s.ticker === ticker; });
    if (!sig) {
      var gateEvals = last.gate_evaluations || [];
      var gate = gateEvals.find(function(g) { return g.ticker === ticker; });
      if (gate) sig = _buildSyntheticSignal(gate);
    }
    if (sig) showSignalPopup(sig, last);
  };

  return cell;
}

// ── Initialize all 3 signal grids (first load only) ──
function _initTickerGrid() {
  Object.keys(_SIGNAL_TABS).forEach(function(tabKey) {
    var grid = document.getElementById('signals-grid-' + tabKey);
    if (!grid) return;
    grid.innerHTML = '';
    var group = _SIGNAL_TABS[tabKey];
    var row = document.createElement('div');
    row.className = 'ticker-row';
    group.tickers.forEach(function(t) {
      var cell = _createTickerCell(t);
      row.appendChild(cell);
      if (!_cellCache[t]) {
        _cellCache[t] = {
          element: cell,
          state: 'none',
          direction: 'neutral',
          confidence: 0,
          price: 0,
          verdict: '',
          gate: null,
          sig: null
        };
      } else {
        _cellCache[t].element = cell;
      }
    });
    grid.appendChild(row);
  });
  _gridInitialized = true;
}

// ── Switch signals sub-tab (indices / commodities / equities) ──
function switchSignalsSubTab(sub) {
  _currentSignalSubTab = sub;
  document.querySelectorAll('#signals-sub-tabs .sub-tab').forEach(function(st) {
    st.classList.toggle('active', st.dataset.subtab === sub);
  });
  document.querySelectorAll('.signals-grid').forEach(function(sg) {
    sg.classList.toggle('active', sg.id === 'signals-grid-' + sub);
  });
  // Re-render if we have cycle data
  if (_status.last_cycle && _status.last_cycle.status === 'completed') {
    renderSignals(_status.last_cycle);
  }
}

// ── Mapping verbatim verdict text to CSS class suffixes ──
function _verdictClass(verdict) {
  if (!verdict) return 'no-action';
  var v = verdict.toUpperCase();
  if (v.indexOf('STRONG BUY') >= 0) return 'strong-buy';
  if (v.indexOf('STRONG SELL') >= 0) return 'strong-sell';
  if (v === 'BUY') return 'buy';
  if (v === 'SELL') return 'sell';
  if (v === 'HOLD') return 'hold';
  if (v === 'REDUCE') return 'reduce';
  if (v === 'EXIT') return 'exit';
  if (v === 'WAIT') return 'wait';
  if (v === 'AVOID') return 'avoid';
  return 'no-action';
}

// ── Compute a unified actionability score (0-100) ──
function _actionability(confidence, tier, aligned, state) {
  var tierW = {bronze:1, silver:2, gold:3, platinum:4}[tier] || 1;
  var stateW = {none:0, watching:1, pending:2, active:3, confirmed:4, weakening:2}[state] || 1;
  var alignW = aligned ? 1.0 : 0.6;
  var base = (confidence * 100) * (tierW / 4) * (stateW / 4) * alignW;
  return Math.min(Math.round(base), 100);
}

// ── Update a single card in-place ──
function _updateCell(ticker, state, direction, confidence, price, sig, gate, st) {
  var cache = _cellCache[ticker];
  if (!cache) return;
  var cell = cache.element;
  if (!cell) return;

  // ── Meta (computed first, then overridden by cached data) ──
  var meta = gate && gate.consensus_meta ? gate.consensus_meta : (sig && sig.consensus_meta ? sig.consensus_meta : null);

  // ── Cached data persistence: fall back to last known good cycle ──
  var cachedSig = cache.sig;
  var cachedGate = cache.gate;
  var effectiveSig = sig || cachedSig;
  var effectiveGate = gate || cachedGate;
  var effectiveMeta = effectiveSig ? effectiveSig.consensus_meta : effectiveGate ? effectiveGate.consensus_meta : meta;
  var tier = (effectiveMeta && effectiveMeta.consensus_conviction_tier) ? effectiveMeta.consensus_conviction_tier : 'bronze';
  var ns = effectiveMeta ? effectiveMeta.consensus_net_score : 0;

  // Gate-rejected persistent slot: show distinct visual alongside state (P2.25)
  var hasGateReject = (gate && !gate.gate_passed && state !== 'none' && state !== 'watching');
  var stateCls = 'state-' + state + (hasGateReject ? ' state-gate-rejected' : '');
  cell.className = 'ticker-cell ' + stateCls;

  // ── Visual heat: glow intensity based on conviction ──
  var heatLevel = Math.min(Math.floor(confidence / 0.25), 4);
  cell.style.boxShadow = confidence > 0.5 ? '0 0 ' + (6 + heatLevel * 3) + 'px rgba(0,200,100,' + (0.1 + heatLevel * 0.05) + ')' : '';

  // ── Staleness indicator (if cycle data is old) ──
  var fresh = _status.last_cycle && _status.last_cycle.timestamp ? dataFreshness(_status.last_cycle.timestamp) : null;
  if (fresh && fresh.cls === 'stale') {
    cell.style.opacity = '0.7';
  } else {
    cell.style.opacity = '1';
  }

  // ── Verdict badge (use cached verdict when no current signal) ──
  var ve = cell.querySelector('.verdict-badge');
  var verdict = sig ? (sig.verdict || 'NO ACTION') : (cachedSig ? (cachedSig.verdict || 'HOLD') : 'NO ACTION');
  if (ve) {
    ve.textContent = verdict;
    ve.className = 'verdict-badge ' + _verdictClass(verdict);
  }

  // ── Flip/TP badge area ──
  var fe = cell.querySelector('.flip-tp-badge');
  if (fe) {
    var tp = _stickyTps[ticker];
    var flip = _stickyFlips[ticker];
    if (tp) {
      fe.textContent = '\u26A0 TP';
      fe.className = 'flip-tp-badge tp-event';
    } else if (flip) {
      var label = flip.potential ? '\u21BB WATCH' : '\u21BB FLIP';
      fe.textContent = label + (flip.from ? ' ' + flip.from.toUpperCase().slice(0,3) + '\u2192' + flip.to.toUpperCase().slice(0,3) : '');
      fe.className = 'flip-tp-badge ' + (flip.potential ? 'flip-potential' : 'flip-major');
    } else {
      fe.textContent = '';
      fe.className = 'flip-tp-badge';
    }
  }

  // ── Direction badge ──
  var de = cell.querySelector('.direction-badge');
  var dirLabel = direction || 'neutral';
  if (de) {
    de.textContent = _getDirectionArrow(dirLabel) + ' ' + dirLabel.toUpperCase();
    de.className = 'direction-badge ' + _getDirClass(dirLabel);
  }

  // ── Regime badge with alignment color (meta now available) ──
  var re = cell.querySelector('.regime-badge');
  if (re) {
    var regime = (sig && sig.regime) || (gate && gate.regime) || (meta && meta.consensus_regime) || (st && st.entry_regime) || '';
    if (regime && regime !== 'unknown') {
      var isUptrend = regime.indexOf('uptrend') >= 0;
      var isDowntrend = regime.indexOf('downtrend') >= 0;
      var isRanging = regime === 'ranging' || regime === 'high_volatility';
      var isLong = dirLabel === 'long';
      var aligned = (isLong && isUptrend) || (!isLong && dirLabel === 'short' && isDowntrend);
      var label = isUptrend ? '\u2191UPTR' : isDowntrend ? '\u2193DNTR' : isRanging ? '\u2192RNG' : regime.slice(0, 4).toUpperCase();
      re.textContent = label;
      re.className = 'regime-badge ' + (aligned ? 'aligned' : (isRanging ? 'neutral' : 'counter'));
    } else {
      re.textContent = '--';
      re.className = 'regime-badge';
    }
  }

  // ── State badge (no cycle count — thesis-age-badge handles that) ──
  var se = cell.querySelector('.state-badge');
  var stateLabel = state || 'none';
  if (se) {
    se.textContent = stateLabel.toUpperCase();
    se.className = 'state-badge ' + stateLabel;
  }

  // ── Thesis age badge (shown when thesis is active, uses cycle_id from status) ──
  var ab = cell.querySelector('.thesis-age-badge');
  if (ab) {
    var thesisAge = 0;
    if (st && st.has_thesis && st.entry_cycle > 0) {
      var currentCycleId = _status.last_cycle ? _status.last_cycle.cycle_id : 0;
      thesisAge = Math.max(0, currentCycleId - st.entry_cycle);
    }
    if (thesisAge > 0) {
      var horizon = _status.last_cycle ? (_status.last_cycle.thesis_horizon || 30) : 30;
      var staleAt = Math.max(horizon * 0.66, 10);
      var agingAt = Math.max(horizon * 0.33, 5);
      ab.style.display = 'inline';
      ab.textContent = '⏱ ' + thesisAge + 'c';
      ab.className = 'thesis-age-badge' + (thesisAge >= staleAt ? ' stale' : thesisAge >= agingAt ? ' aging' : ' fresh');
    } else {
      ab.style.display = 'none';
    }
  }

  // ── Actionability indicator ──
  var ae = cell.querySelector('.actionability');
  if (ae) {
    var aligned = false;
    var regime = (sig && sig.regime) || (gate && gate.regime) || (meta && meta.consensus_regime) || '';
    if (regime && regime !== 'unknown') {
      var isUptrend = regime.indexOf('uptrend') >= 0;
      var isDowntrend = regime.indexOf('downtrend') >= 0;
      var isLong = dirLabel === 'long';
      aligned = (isLong && isUptrend) || (!isLong && isDowntrend);
    }
    var act = _actionability(confidence, tier, aligned, stateLabel);
    ae.textContent = 'Act: ' + act;
    ae.className = 'actionability ' + (act >= 70 ? 'high' : act >= 40 ? 'medium' : 'low');
  }

  // ── Conviction meter ──
  var convPct = confidence * 100;

  var te = cell.querySelector('.tier-badge');
  if (te) { te.textContent = tier.toUpperCase(); te.className = 'tier-badge ' + tier; }

  var pe = cell.querySelector('.meter-pct');
  if (pe) { pe.textContent = convPct.toFixed(1) + '%'; pe.className = 'meter-pct ' + tier; }

  var be = cell.querySelector('.meter-bar');
  if (be) { be.style.width = Math.max(convPct, 5) + '%'; be.className = 'meter-bar ' + tier; }

  // ── Trend indicator (improving / flat / fading) ──
  var ti = cell.querySelector('.trend-indicator');
  if (ti) {
    var trend = st ? st.trend : null;
    if (trend === 'improving') {
      ti.textContent = '\u2191';
      ti.className = 'trend-indicator improving';
    } else if (trend === 'deteriorating') {
      ti.textContent = '\u2193';
      ti.className = 'trend-indicator deteriorating';
    } else if (trend === 'flat') {
      ti.textContent = '\u2192';
      ti.className = 'trend-indicator flat';
    } else {
      ti.textContent = '\u2013';
      ti.className = 'trend-indicator';
    }
  }

  // ── Row 2p: PnL (merged from active positions) — shows when thesis is active ──
  var r2p = cell.querySelector('.row-pnl');
  if (r2p) {
    var entryPx = st ? st.state_entry_price : 0;
    var hasThesis = st && st.has_thesis && entryPx > 0 && price > 0;
    if (hasThesis) {
      var dirSign = (st.active_direction || 'long') === 'short' ? -1 : 1;
      var pnlPct = ((price - entryPx) / entryPx * 100 * dirSign);
      var pnlDollar = (price - entryPx) * dirSign;
      var pnlCls = pnlPct > 2 ? 'pnl-gain' : pnlPct > 0 ? 'pnl-flat' : pnlPct < -2 ? 'pnl-loss' : 'pnl-flat';
      r2p.style.visibility = 'visible';
      r2p.innerHTML =
        '<span class="pnl-label">Entry</span><span class="pnl-val">$' + entryPx.toFixed(2) + '</span>' +
        '<span class="pnl-label">Now</span><span class="pnl-val">$' + price.toFixed(2) + '</span>' +
        '<span class="pnl-pct ' + pnlCls + '">' + (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%</span>' +
        '<span class="pnl-dollar ' + pnlCls + '">$' + (pnlDollar >= 0 ? '+' : '') + pnlDollar.toFixed(2) + '</span>';
    } else {
      r2p.style.visibility = 'hidden';
    }
  }

  // ── Row 2: Score, Conf, Strats (use cached votes when no current gate/sig) ──
  var r2 = cell.querySelector('.grid-stats');
  if (r2) {
    var votes = (gate && gate.strategy_votes) ? gate.strategy_votes : (sig ? (sig.strategy_votes || []) : (cachedGate ? (cachedGate.strategy_votes || []) : (cachedSig ? (cachedSig.strategies || []) : [])));
    var total = votes ? votes.length : 0;
    var active = votes ? votes.filter(function(v) { return v.confidence > 0.3; }).length : 0;
    var scoreStr = sig ? (Math.abs(ns) * 100).toFixed(1) + '%' : (meta ? (Math.abs(ns) * 100).toFixed(1) + '%' : (cachedSig ? (Math.abs((cachedSig.consensus_meta||{}).consensus_net_score||0) * 100).toFixed(1) + '%' : '--'));
    var confStr = (confidence * 100).toFixed(1) + '%';
    r2.innerHTML =
      '<span>Ns: <strong>' + scoreStr + '</strong></span>' +
      '<span>Conf: <strong>' + confStr + '</strong></span>' +
      '<span>Strats: <strong>' + active + '/' + total + '</strong></span>';
  }

  // ── Price row (dedicated row above levels) ──
  var priceRow = cell.querySelector('.grid-price');
  if (priceRow) {
    var pxStr = price > 0 ? '$' + price.toFixed(2) : (effectiveSig && effectiveSig.current_price > 0 ? '$' + effectiveSig.current_price.toFixed(2) : (effectiveGate && effectiveGate.current_price > 0 ? '$' + effectiveGate.current_price.toFixed(2) : '$--'));
    priceRow.innerHTML = '<span class="level-pair"><span class="level-label">Price</span><span class="level-val price">' + pxStr + '</span></span>';
  }

  // ── Row 3: Entry, SL, TP, R:R, ATR — render each field independently (handles 0/null gracefully) ──
  var r3 = cell.querySelector('.grid-levels');
  if (r3) {
    var ratr = effectiveGate ? (effectiveGate.atr || 0) : 0;
    var ep = effectiveSig ? (effectiveSig.entry_price || 0) : 0;
    var sl = effectiveSig ? (effectiveSig.stop_loss || 0) : 0;
    var tp = effectiveSig ? (effectiveSig.take_profit || 0) : 0;
    var rr = effectiveSig ? (effectiveSig.risk_reward || 0) : 0;
    // Fallback R:R computation if we have ep/sl/tp
    if (rr <= 0 && ep > 0 && sl > 0 && tp > 0 && Math.abs(sl - ep) > 0.001) {
      rr = Math.abs(tp - ep) / Math.abs(sl - ep);
      if (!isFinite(rr)) rr = 0;
    }
    var epStr = ep > 0 ? '$' + ep.toFixed(2) : '$--';
    var slStr = sl > 0 ? '$' + sl.toFixed(2) : '$--';
    var slTitle = (ep > 0 && sl > 0 && ratr > 0) ? Math.abs(ep - sl) / ratr : '';
    var tpStr = tp > 0 ? '$' + tp.toFixed(2) : '$--';
    var rrStr = rr > 0 ? rr.toFixed(1) : '--';
    var atrStr = ratr > 0 ? '$' + ratr.toFixed(2) : '$--';
    r3.innerHTML =
      '<span class="level-pair"><span class="level-label">Entry</span><span class="level-val">' + epStr + '</span></span>' +
      '<span class="level-pair"><span class="level-label">SL</span><span class="level-val sl"' + (slTitle ? ' title="' + slTitle.toFixed(1) + '\u00D7 ATR"' : '') + '>' + slStr + '</span></span>' +
      '<span class="level-pair"><span class="level-label">TP</span><span class="level-val tp">' + tpStr + '</span></span>' +
      '<span class="level-pair"><span class="level-label">R:R</span><span class="level-val rr">' + rrStr + '</span></span>' +
      '<span class="level-pair"><span class="level-label">ATR</span><span class="level-val">' + atrStr + '</span></span>';
  }

  // ── Row 4: Suggested Limit Entry (use cached sig) ──
  var r4 = cell.querySelector('.grid-limit');
  if (r4) {
    var suggested = effectiveSig ? effectiveSig.suggested_entry : 0;
    var entryPx4 = effectiveSig ? effectiveSig.entry_price : 0;
    var suggType = effectiveSig ? (effectiveSig.suggested_entry_type || '') : '';
    if (suggested && entryPx4 && Math.abs(suggested - entryPx4) > 0.005) {
      r4.innerHTML = '<span class="level-label limit-label">\u{1F3AF} Limit</span>' +
        '<span class="level-val limit">$' + suggested.toFixed(2) + '</span>' +
        '<span class="limit-type">' + _formatLevelType(suggType) + '</span>';
    } else {
      r4.innerHTML = '<span class="level-label limit-label">\u{1F3AF} Limit</span>' +
        '<span class="level-val limit">$--</span>' +
        '<span class="limit-type"></span>';
    }
  }

  // ── SR Row: nearest support/resistance (use cached sig) ──
  var srRow = cell.querySelector('.row-sr');
  if (srRow) {
    var nsVal = effectiveSig ? effectiveSig.nearest_support : null;
    var nrVal = effectiveSig ? effectiveSig.nearest_resistance : null;
    var dir = direction || 'neutral';

    // Support column
    var suppVal = srRow.querySelector('.support-col .sr-val');
    var suppDist = srRow.querySelector('.support-col .sr-dist');
    if (suppVal && nsVal && price > 0) {
      var suppDistPct = ((price - nsVal) / price * 100);
      suppVal.textContent = '$' + nsVal.toFixed(2);
      suppDist.textContent = suppDistPct > 0 ? '+' + suppDistPct.toFixed(2) + '%' : suppDistPct.toFixed(2) + '%';
      var suppClose = suppDistPct < 0.5;
      suppVal.className = 'sr-val ' + (dir === 'long' ? (suppClose ? 'sr-good' : '') : (suppClose ? 'sr-bad' : ''));
      suppDist.className = 'sr-dist ' + (suppDistPct < 0.5 ? (dir === 'long' ? 'sr-good' : 'sr-bad') : '');
    } else if (suppVal) {
      suppVal.textContent = '$--';
      suppVal.className = 'sr-val';
      if (suppDist) suppDist.textContent = '';
    }

    // Resistance column
    var resVal = srRow.querySelector('.resistance-col .sr-val');
    var resDist = srRow.querySelector('.resistance-col .sr-dist');
    if (resVal && nrVal && price > 0) {
      var resDistPct = ((nrVal - price) / price * 100);
      resVal.textContent = '$' + nrVal.toFixed(2);
      resDist.textContent = resDistPct > 0 ? '+' + resDistPct.toFixed(2) + '%' : resDistPct.toFixed(2) + '%';
      var resClose = resDistPct < 0.5;
      resVal.className = 'sr-val ' + (dir === 'short' ? (resClose ? 'sr-good' : '') : (resClose ? 'sr-bad' : ''));
      resDist.className = 'sr-dist ' + (resClose ? (dir === 'short' ? 'sr-good' : 'sr-bad') : '');
    } else if (resVal) {
      resVal.textContent = '$--';
      resVal.className = 'sr-val';
      if (resDist) resDist.textContent = '';
    }
  }    // ── Reversal Signal Banner (Fix 1: shows during V-reversals at key levels) ──
    var revBanner = cell.querySelector('.reversal-banner');
    if (revBanner) {
        var revSig = null;
        // Check current signal snapshot from persistence engine (by_ticker)
        if (st && st.current_signal && st.current_signal.reversal_signal) {
            revSig = st.current_signal.reversal_signal;
        }
        if (revSig && revSig.entry_level && revSig.direction) {
            revBanner.style.display = 'flex';
            var revDir = revBanner.querySelector('.reversal-dir');
            if (revDir) {
                revDir.textContent = revSig.direction.toUpperCase();
                revDir.className = 'reversal-dir ' + (revSig.direction === 'long' ? 'long' : 'short');
            }
            var revLevel = revBanner.querySelector('.reversal-level');
            if (revLevel) {
                var entryType = _formatLevelType(revSig.entry_type || '');
                revLevel.textContent = '\u{1F3AF} $' + (revSig.entry_level || 0).toFixed(2) + (entryType ? ' - ' + entryType : '');
            }
        } else {
            revBanner.style.display = 'none';
        }
    }

    // ── Bottom: gate, ns, proximity ──
    var bot = cell.querySelector('.grid-bottom');
    if (bot) {
    var gateCls = '';
    var eGate = effectiveGate;
    if (eGate) {
      gateCls = eGate.gate_passed ? 'tc-gate-pass' : 'tc-gate-fail';
    }
    // ── Proximity warning (use cached sig) ──
    var proxWarn = effectiveSig ? (effectiveSig.proximity_warning || '') : '';
    var proxHtml = '';
    if (proxWarn) {
      var warnCls = proxWarn.indexOf('AT_') >= 0 ? 'danger' : proxWarn.indexOf('NEAR_') >= 0 ? 'warning' : 'info';
      var warnLabel = proxWarn.replace(/_/g, ' ').replace('AT RESISTANCE', 'AT RESISTANCE').replace('AT SUPPORT', 'AT SUPPORT').slice(0, 40);
      proxHtml = '<span class="proximity-warn ' + warnCls + '" title="' + proxWarn + '">\u26A0 ' + warnLabel + '</span>';
    }

    bot.innerHTML =
      '<span class="level-pair"><span class="level-label">Gate</span><span class="level-val' + (gateCls ? ' ' + gateCls : '') + '" title="' + (!eGate.gate_passed ? (eGate.gate_reason || 'fail').replace(/_/g, ' ') : '') + '">' + (eGate ? (eGate.gate_passed ? 'Passed' : 'Blocked') : '--') + '</span></span>' +
      '<span class="level-pair"><span class="level-label">Ns</span><span class="level-val ' + (ns > 0.1 ? 'tc-ns-pos' : ns < -0.1 ? 'tc-ns-neg' : '') + '">' + (ns >= 0 ? '+' : '') + ns.toFixed(2) + '</span></span>' +
      proxHtml;
  }

  cache.state = state;
  cache.direction = direction;
  cache.confidence = confidence;
  cache.price = price;
  cache.verdict = verdict;
  cache.gate = gate;
  cache.sig = sig;
}

// ── Main render: called on each status update ──
function renderSignals(cycle) {
  // Initialize grid on first call
  if (!_gridInitialized) _initTickerGrid();

  // Get signal states (all tickers, not just those with signals)
  var states = cycle.signal_states || {};
  var byTicker = states.by_ticker || {};

  // Get signal data from this cycle (for those that passed gate)
  var allSigs = [];
  var groups = cycle.signals || {};
  ['stock','future','option'].forEach(function(type) {
    (groups[type] || []).forEach(function(s) { allSigs.push(s); });
  });

  // Get gate evaluations for per-ticker strategy data
  var gateEvals = cycle.gate_evaluations || [];
  var gateByTicker = {};
  gateEvals.forEach(function(g) { gateByTicker[g.ticker] = g; });

  // Get flip data for sticky flip/TP badges
  var flips = cycle.flips || {};
  var flipsPotential = cycle.flips_potential || {};

  // Update sticky events (flip/TP badges linger for N cycles)
  _updateStickyEvents(allSigs, flips, flipsPotential);

  // Cleanup stale frontend state for tickers no longer in grid (P2.14)
  _cleanupRemovedTickers(_GRID_TICKERS);

  // Get live prices
  var prices = _livePrices || {};

  // Update each grid cell
  _GRID_TICKERS.forEach(function(ticker) {
    var st = byTicker[ticker] || { state: 'none', direction: 'neutral', conviction: 0 };
    var sig = allSigs.find(function(s) { return s.ticker === ticker; });
    var gate = gateByTicker[ticker];
    var priceData = prices[ticker];
    var price = (priceData && priceData.price) || (gate && gate.current_price) || (sig && sig.current_price) || 0;

    // Determine state and direction — always trust persistence engine, never fabricate
    var state = st.state || 'none';
    var direction = st.active_direction || (sig ? sig.direction : 'neutral');
    var confidence = st.conviction || (sig ? sig.confidence : 0);

    _updateCell(ticker, state, direction, confidence, price, sig, gate, st);
  });

  // Update summary counts
  var totalSignals = allSigs.length;
  document.getElementById('total-signals').textContent = 'Signals: ' + totalSignals;
  document.getElementById('total-scanned').textContent = 'Scanned: ' + (cycle.tickers_scanned || _GRID_TICKERS.length);

  var elapsed = cycle.elapsed_seconds || 0;
  document.getElementById('cycle-elapsed').textContent = 'Last cycle: ' + elapsed.toFixed(1) + 's';
}

/* ═══════════════════════════════════════════════════════════════
   History Rendering
   ═══════════════════════════════════════════════════════════════ */

function renderHistory() {
  var container = document.getElementById('history-list');
  if (!container) return;

  // Merge base history (from /api/status) with paginated pages (from /api/history)
  var baseHistory = _status.history || [];
  var fullHistory = baseHistory.concat(_historyPages);

  // Deduplicate by cycle_id (status poll may return newer cycles than pages)
  var seen = {};
  var history = [];
  fullHistory.forEach(function(c) {
    var cid = String(c.cycle_id || '');
    if (!seen[cid]) {
      seen[cid] = true;
      history.push(c);
    }
  });

  if (!history || history.length === 0) {
    container.innerHTML = '<div class="empty-state">📜 No cycle history yet. Run a cycle first.</div>';
    return;
  }

  var sub = _currentSubTab || 'stock';
  var filterText = (window._historyFilter || '').toLowerCase().trim();
  container.innerHTML = '';

  // Filter input + cycle count
  var filterDiv = document.createElement('div');
  filterDiv.style.cssText = 'margin-bottom:10px;display:flex;gap:8px;align-items:center';
  filterDiv.innerHTML =
    '<input id="history-filter-input" type="text" placeholder="Filter by ticker..." ' +
    'value="' + (window._historyFilter || '') + '" ' +
    'oninput="window._historyFilter=this.value;renderHistory()" ' +
    'style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg-card);color:var(--text);font-size:13px">' +
    '<span style="font-size:12px;color:var(--text-muted)">' + history.length + ' cycles</span>';
  container.appendChild(filterDiv);

  history.forEach(function(cycle) {
    var cycleDiv = document.createElement('div');
    cycleDiv.className = 'history-cycle';

    var ts = cycle.timestamp || '';
    var timeStr = ts ? new Date(ts).toLocaleTimeString() : '--';
    var signals = cycle.signals || {};
    var typeSignals = signals[sub] || [];

    // Apply ticker filter
    if (filterText) {
      typeSignals = typeSignals.filter(function(s) {
        return (s.ticker || '').toLowerCase().indexOf(filterText) !== -1;
      });
    }

    var sigCount = typeSignals.length;
    var flipCount = cycle.flip_count || 0;
    var wasError = cycle.status === 'error';

    cycleDiv.innerHTML =
      '<div class="history-header" onclick="this.nextElementSibling.classList.toggle(\'expanded\')">' +
        '<span><strong>#' + (cycle.cycle_id || '?') + '</strong> ' + timeStr + ' — ' + sigCount + ' ' + sub + ' signals' +
        (flipCount > 0 ? ' | 🔴 ' + flipCount + ' flip' + (flipCount > 1 ? 's' : '') : '') +
        (wasError ? ' | ❌ ERROR' : '') +
        '</span>' +
        '<span>' + (cycle.elapsed_seconds || 0).toFixed(1) + 's</span>' +
      '</div>' +
      '<div class="history-body">';

    if (wasError) {
      cycleDiv.querySelector('.history-body').innerHTML = '<div class="error-state">Error: ' + (cycle.reason || 'unknown') + '</div>';
    } else if (typeSignals.length === 0) {
      if (filterText) {
        cycleDiv.querySelector('.history-body').innerHTML = '<em style="color:var(--text-muted)">No matching signals this cycle</em>';
      } else {
        cycleDiv.querySelector('.history-body').innerHTML = '<em style="color:var(--text-muted)">No ' + sub + ' signals this cycle</em>';
      }
    } else {
      var bodyHtml = '';
      typeSignals.forEach(function(s) {
        var dirArrow = s.direction === 'long' ? '▲' : s.direction === 'short' ? '▼' : '–';
        var gateBadge = s.gate_passed === false
          ? '<span class="gate-badge rejected" style="font-size:9px;margin-left:4px">BLOCKED</span>'
          : '';
        bodyHtml +=
          '<div class="history-signal" data-ticker="' + (s.ticker || '') + '" data-cycle="' + (cycle.cycle_id || '') + '">' +
            '<span class="direction-badge ' + s.direction + '" style="padding:1px 6px;font-size:10px">' + dirArrow + '</span>' +
            '<strong>' + s.ticker + '</strong>' +
            '<span>' + (s.confidence * 100).toFixed(1) + '%</span>' +
            '<span class="verdict-badge" style="font-size:9px;padding:0 4px">' + (s.verdict || '') + '</span>' +
            (gateBadge) +
          '</div>';
      });
      cycleDiv.querySelector('.history-body').innerHTML = bodyHtml;
    }

    container.appendChild(cycleDiv);
  });

  // Load More button (fetch next page from /api/history)
  var hasMore = history.length >= 40 || _historyPages.length > 0;
  if (hasMore && history.length >= 10) {
    var loadMoreBtn = document.createElement('button');
    loadMoreBtn.className = 'btn btn-secondary';
    loadMoreBtn.style.cssText = 'display:block;margin:16px auto;padding:8px 24px';
    loadMoreBtn.textContent = 'Load More';
    loadMoreBtn.onclick = function() {
      var offset = _historyPageLoaded || 0;
      if (offset <= 0 && baseHistory.length > 0) offset = baseHistory.length;
      this.disabled = true;
      this.textContent = 'Loading...';
      var self = this;
      fetch('/api/history?offset=' + offset + '&limit=20')
        .then(function(r) { return r.json(); })
        .then(function(data) {
          var newCycles = data.cycles || [];
          if (newCycles.length > 0) {
            newCycles.forEach(function(c) { _historyPages.push(c); });
            _historyPageLoaded = offset + newCycles.length;
          }
          renderHistory();
        })
        .catch(function() {
          self.textContent = 'Error loading — try again';
          self.disabled = false;
        });
    };
    container.appendChild(loadMoreBtn);
  }

  // Delegated click for history signals → popup
  container.onclick = function(e) {
    var el = e.target.closest('.history-signal');
    if (!el) return;
    var ticker = el.getAttribute('data-ticker');
    var cycleId = el.getAttribute('data-cycle');
    if (!ticker || !cycleId) return;
    var cycle = null;
    for (var i = 0; i < history.length; i++) {
      if (String(history[i].cycle_id) === cycleId) {
        cycle = history[i];
        break;
      }
    }
    if (!cycle) return;
    var signals = cycle.signals || {};
    var typeSignals = signals[sub] || [];
    for (var j = 0; j < typeSignals.length; j++) {
      if (typeSignals[j].ticker === ticker) {
        showSignalPopup(typeSignals[j], cycle);
        return;
      }
    }
  };
}

/* ═══════════════════════════════════════════════════════════════
   Watchlist (Slot Usage)
   ═══════════════════════════════════════════════════════════════ */

function loadWatchlist() {
  var slotEl = document.getElementById('slot-usage');
  var tickerEl = document.getElementById('subscribed-tickers');
  if (!slotEl || !tickerEl) return;

  var slots = _status.slot_usage || {};
  var limits = slots.limits || {};
  var byType = slots.by_type || {};

  // STATE: Loading / Empty
  if (!slots.total_limit) {
    slotEl.innerHTML = '<div class="empty-state">No slot data yet</div>';
    tickerEl.innerHTML = '';
    return;
  }

  // STATE: Data
  var slotHtml = '';
  ['stock', 'future', 'option'].forEach(function(t) {
    var used = byType[t] || 0;
    var limit = limits[t] || 10;
    var pct = Math.min((used / limit) * 100, 100);
    var barClass = pct > 80 ? 'red' : pct > 60 ? 'yellow' : 'green';
    slotHtml +=
      '<div class="slot-card">' +
        '<div class="slot-label">' + t.charAt(0).toUpperCase() + t.slice(1) + '</div>' +
        '<div class="slot-value">' + used + '/' + limit + '</div>' +
        '<div class="slot-bar"><div class="slot-bar-fill ' + barClass + '" style="width:' + pct + '%"></div></div>' +
      '</div>';
  });
  slotEl.innerHTML = slotHtml;

  var fixedStocks = slots.fixed_stocks || 0;
  var fixedFutures = slots.fixed_futures || 0;
  var pinned = slots.pinned || 0;
  slotHtml +=
    '<div class="slot-card"><div class="slot-label">Fixed Stocks</div><div class="slot-value">' + fixedStocks + '</div></div>' +
    '<div class="slot-card"><div class="slot-label">Fixed Futures</div><div class="slot-value">' + fixedFutures + '</div></div>' +
    '<div class="slot-card"><div class="slot-label">Pinned</div><div class="slot-value">' + pinned + '</div></div>';
  slotEl.innerHTML = slotHtml;

  var subTickers = _status.connection ? (_status.connection.subscribed || []) : [];
  if (subTickers.length === 0) {
    tickerEl.innerHTML = '<div class="empty-state" style="padding:12px">No tickers subscribed yet</div>';
  } else {
    tickerEl.innerHTML = subTickers.map(function(t) { return '<span class="ticker-item">' + t + '</span>'; }).join('');
  }
}

/* ═══════════════════════════════════════════════════════════════
   Signal Popup
   ═══════════════════════════════════════════════════════════════ */

function showSignalPopup(signal, cycle) {
  var overlay = document.getElementById('signal-popup-overlay');
  if (!overlay) return;
  overlay.style.display = 'flex';

  // Populate header
  document.getElementById('popup-title').textContent = signal.ticker + ' — ' + signal.instrument_type.toUpperCase();
  var dirEl = document.getElementById('popup-direction');
  dirEl.textContent = signal.direction.toUpperCase();
  dirEl.className = 'direction-badge ' + signal.direction;

  // ── Verdict in popup ──
  var popupVerdictEl = document.getElementById('popup-verdict');
  if (popupVerdictEl) {
    var v = signal.verdict || 'NO ACTION';
    popupVerdictEl.textContent = v;
    popupVerdictEl.className = 'verdict-badge ' + v.toLowerCase().replace(/\s+/g, '-');
  }

  document.getElementById('popup-confidence').textContent = (signal.confidence * 100).toFixed(1) + '%';
  document.getElementById('popup-regime').textContent = (signal.regime || '—').replace(/_/g, ' ');
  document.getElementById('popup-price').textContent = '$' + (signal.current_price || 0).toFixed(2);

  // Session meta
  var sessionMeta = document.getElementById('popup-session-meta');
  var tw = signal.time_window || '';
  var vw = signal.vwap_position || '';
  if (tw || vw) {
    sessionMeta.style.display = 'flex';
    document.getElementById('popup-time-window').textContent = tw.replace(/_/g, ' ') || '--';
    document.getElementById('popup-vwap-pos').textContent = vw || '--';
  } else {
    sessionMeta.style.display = 'none';
  }

  // State duration — O(1) lookup via by_ticker
  var popupState = document.getElementById('popup-state-duration');
  if (popupState) { popupState.style.display = 'none'; popupState.textContent = ''; }
  var popupGenTime = document.getElementById('popup-generated-time');
  var popupEntryPrice = document.getElementById('popup-entry-price');
  var foundState = false;
  var signalStates = cycle.signal_states || {};
  if (popupState && signalStates && signalStates.by_ticker) {
    var ts = signalStates.by_ticker[signal.ticker];
    if (ts && ts.state && ts.state !== 'none' && ts.state !== 'watching') {
      var dur = stateAge(ts.state_since);
      popupState.textContent = ts.state.toUpperCase() + ' x' + (ts.consecutive_same || 1) + (dur ? ' for ' + dur : '');
      popupState.className = 'state-badge ' + ts.state;
      popupState.style.display = 'inline-block';
      foundState = true;

      // Populate generation time & entry price
      if (popupGenTime) {
        popupGenTime.textContent = formatSignalTime(ts.state_since);
        popupGenTime.style.display = 'inline';
      }
      if (popupEntryPrice) {
        var ep = ts.state_entry_price || signal.entry_price || 0;
        popupEntryPrice.textContent = ep > 0 ? '$' + ep.toFixed(2) : '--';
        popupEntryPrice.style.display = 'inline';
      }
    }
  }

  // Gate reason (rejected signals)
  var gateReason = document.getElementById('popup-gate-reason');
  if (!signal.gate_passed) {
    gateReason.style.display = 'block';
    document.getElementById('popup-gate-reason-text').textContent = signal.gate_reason || 'unknown';
  } else {
    gateReason.style.display = 'none';
  }

  // Levels
  document.getElementById('popup-entry').textContent = (signal.entry_price || signal.current_price || 0) > 0 ? '$' + (signal.entry_price || signal.current_price || 0).toFixed(2) : '--';
  document.getElementById('popup-sl').textContent = signal.stop_loss ? '$' + signal.stop_loss.toFixed(2) : '--';
  document.getElementById('popup-tp').textContent = signal.take_profit ? '$' + signal.take_profit.toFixed(2) : '--';
  document.getElementById('popup-rr').textContent = signal.risk_reward ? signal.risk_reward.toFixed(1) : '--';

  // ── Suggested entry (limit order level) ──
  var popupLimitRow = document.getElementById('popup-limit-entry-row');
  var suggested = signal.suggested_entry || 0;
  var suggType = signal.suggested_entry_type || '';
  // Show when suggested_entry differs meaningfully from current price, OR when
  // the type is explicitly set (handles options where entry_price ≈ suggested_entry)
  var curPx = signal.current_price || signal.entry_price || 0;
  var showLimit = suggested && curPx && suggType && suggType !== 'market' && Math.abs(suggested - curPx) / Math.max(curPx, 0.01) > 0.001;
  if (popupLimitRow && showLimit) {
    popupLimitRow.style.display = 'flex';
    document.getElementById('popup-limit-entry').textContent = '$' + suggested.toFixed(2);
    document.getElementById('popup-limit-type').textContent = _formatLevelType(suggType);
  } else if (popupLimitRow) {
    popupLimitRow.style.display = 'none';
  }

  // ── Nearest support / resistance ──
  var popupSR = document.getElementById('popup-sr-levels');
  if (popupSR) {
    var ns = signal.nearest_support;
    var nr = signal.nearest_resistance;
    if (ns || nr) {
      popupSR.style.display = 'flex';
      document.getElementById('popup-nearest-support').textContent = ns ? '$' + ns.toFixed(2) : '--';
      document.getElementById('popup-nearest-resistance').textContent = nr ? '$' + nr.toFixed(2) : '--';
    } else {
      popupSR.style.display = 'none';
    }
  }

  // ── Proximity warning ──
  var popupProx = document.getElementById('popup-proximity-warning');
  if (popupProx) {
    var proxWarn = signal.proximity_warning || '';
    if (proxWarn) {
      popupProx.style.display = 'block';
      var warnCls = proxWarn.indexOf('AT_') >= 0 ? 'prox-danger' : proxWarn.indexOf('NEAR_') >= 0 ? 'prox-warning' : 'prox-info';
      popupProx.innerHTML = '<span class="proximity-banner ' + warnCls + '">\u26A0 ' + proxWarn.replace(/_/g, ' ').slice(0, 80) + '</span>';
    } else {
      popupProx.style.display = 'none';
    }
  }

  // Option levels
  var optLevels = document.getElementById('popup-option-levels');
  var optPrem = signal.option_entry_premium || 0;
  if (signal.instrument_type === 'option' && optPrem > 0) {
    optLevels.style.display = 'block';
    document.getElementById('popup-opt-entry').textContent = '$' + optPrem.toFixed(2);
    document.getElementById('popup-opt-sl').textContent = signal.option_sl_premium ? '$' + signal.option_sl_premium.toFixed(2) : '--';
    document.getElementById('popup-opt-tp').textContent = signal.option_tp_premium ? '$' + signal.option_tp_premium.toFixed(2) : '--';
    document.getElementById('popup-premium-rr').textContent = signal.premium_risk_reward ? signal.premium_risk_reward.toFixed(1) : '--';
  } else {
    optLevels.style.display = 'none';
  }

  // Strike recommendation
  var strikeSec = document.getElementById('popup-strike-recommendation');
  var recStrike = signal.recommended_strike || 0;
  if (signal.instrument_type === 'option' && recStrike > 0) {
    strikeSec.style.display = 'block';
    document.getElementById('popup-strike').textContent = '$' + recStrike.toFixed(signal.ticker === 'SPX' ? 0 : 2);
    document.getElementById('popup-opt-type').textContent = (signal.recommended_otm || '') + ' ' + (signal.recommended_option_type || '');
    document.getElementById('popup-est-win').textContent = signal.estimated_win_rate ? (signal.estimated_win_rate * 100).toFixed(0) + '%' : '--';
    document.getElementById('popup-est-payoff').textContent = signal.estimated_payoff ? signal.estimated_payoff.toFixed(1) + 'x' : '--';
    document.getElementById('popup-strike-rationale').textContent = signal.strike_rationale || '';
  } else {
    strikeSec.style.display = 'none';
  }

  // Position
  var posSec = document.getElementById('popup-position-sizing');
  if (signal.instrument_type === 'option' && signal.direction !== 'neutral') {
    posSec.style.display = 'block';
    document.getElementById('popup-contracts').textContent = signal.position_contracts || 1;
    document.getElementById('popup-total-prem').textContent = optPrem > 0 ? '$' + (optPrem * (signal.position_contracts || 1)).toFixed(2) : '--';
  } else {
    posSec.style.display = 'none';
  }

  // Market dashboard
  var dashSec = document.getElementById('popup-market-dashboard');
  var miniDash = signal.market_dashboard || {};
  if (Object.keys(miniDash).length > 0 && signal.instrument_type === 'option') {
    dashSec.style.display = 'block';
    var grid = document.getElementById('dashboard-grid');
    grid.innerHTML = '';
    var metrics = [
      {k:'underlying',l:'Underlying',v:miniDash.underlying||'--'},
      {k:'underlying_price',l:'Price',v:'$'+(miniDash.underlying_price||0).toFixed(2)},
      {k:'iv',l:'ATM IV',v:(miniDash.iv||0).toFixed(1)+'%'},
      {k:'hv_10',l:'HV(10)',v:(miniDash.hv_10||0).toFixed(1)+'%'},
      {k:'dte',l:'DTE',v:miniDash.dte||'--'},
      {k:'pc_ratio',l:'P/C Vol',v:(miniDash.pc_ratio||0).toFixed(2)},
      {k:'pc_ratio_5d',l:'P/C 5d Avg',v:(miniDash.pc_ratio_5d||0).toFixed(2)},
      {k:'gamma_flip',l:'Gamma Flip',v:'$'+(miniDash.gamma_flip||0).toFixed(1)},
      {k:'delta_positioning',l:'Delta Pos',v:miniDash.delta_positioning||0},
      {k:'atm_straddle',l:'ATM Straddle',v:'$'+(miniDash.atm_straddle||0).toFixed(2)},
      {k:'skew_1m',l:'Skew 1M',v:(miniDash.skew_1m||0).toFixed(1)},
      {k:'charm_direction',l:'Charm Dir',v:miniDash.charm_direction||'--'},
      {k:'vix_spot',l:'VIX',v:(miniDash.vix_spot||0).toFixed(1)},
      {k:'breadth_state',l:'Breadth',v:(miniDash.breadth_state||'--')+(miniDash.breadth_thrust?'⚡':'')},
      {k:'futures_alignment',l:'Futures Align',v:(miniDash.futures_alignment||0)+'%'},
      {k:'tech_divergence',l:'Tech Diverg',v:(miniDash.tech_divergence||0).toFixed(4)},
    ];
    metrics.forEach(function(m) {
      var div = document.createElement('div');
      div.className = 'dashboard-metric';
      div.innerHTML = '<label>' + m.l + '</label><span>' + m.v + '</span>';
      grid.appendChild(div);
    });
    var summEl = document.getElementById('dashboard-summary');
    var dashSummary = 'IV ' + (miniDash.iv||0).toFixed(0) + '% | Breadth ' + (miniDash.breadth_state||'--') + ' | VIX ' + (miniDash.vix_spot||0).toFixed(1);
    summEl.textContent = dashSummary;
  } else {
    dashSec.style.display = 'none';
  }

  // Strategies (handle both strategies and strategy_votes field names)
  var strategies = signal.strategies || signal.strategy_votes || [];
  var stratContainer = document.getElementById('popup-strategies');
  stratContainer.innerHTML = '';
  if (strategies.length > 0) {
    strategies.forEach(function(s) {
      var item = document.createElement('div');
      item.className = 'strategy-item';
      var dirArrow = s.direction === 'long' ? '▲' : s.direction === 'short' ? '▼' : '–';
      var dirClass = s.direction === 'long' ? 'accent' : s.direction === 'short' ? 'danger' : '';
      var reason = s.reasoning || '';
      item.innerHTML =
        '<span class="strat-name">' + (s.name || '?') + '</span>' +
        '<span style="color:var(--' + dirClass + ');font-weight:600">' + dirArrow + '</span>' +
        '<span>' + (s.confidence * 100).toFixed(1) + '%</span>' +
        '<span style="color:var(--text-muted);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + reason.substring(0,100) + '</span>';
      stratContainer.appendChild(item);
    });
  } else {
    stratContainer.innerHTML = '<em style="color:var(--text-muted);font-size:11px">No active strategies</em>';
  }

  // Consensus meta
  var cm = signal.consensus_meta || {};
  var consSec = document.getElementById('popup-consensus-meta');
  var hasConsensus = Object.keys(cm).length > 0 && cm.consensus_net_score !== undefined;
  if (hasConsensus) {
    consSec.style.display = 'block';
    var consGrid = document.getElementById('consensus-grid');
    consGrid.innerHTML = '';
    var consMetrics = [
      {k:'consensus_net_score',l:'Net Score',v:cm.consensus_net_score!==undefined?(cm.consensus_net_score).toFixed(4):'--'},
      {k:'consensus_active_votes',l:'Active Votes',v:cm.consensus_active_votes||0},
      {k:'consensus_weighted_long',l:'Weighted Long',v:cm.consensus_weighted_long!==undefined?cm.consensus_weighted_long.toFixed(4):'--'},
      {k:'consensus_weighted_short',l:'Weighted Short',v:cm.consensus_weighted_short!==undefined?cm.consensus_weighted_short.toFixed(4):'--'},
      {k:'consensus_total_weight',l:'Total Weight',v:cm.consensus_total_weight!==undefined?cm.consensus_total_weight.toFixed(4):'--'},
      {k:'consensus_counter_trend',l:'Counter Trend',v:cm.consensus_counter_trend||'--'},
      {k:'consensus_conviction_tier',l:'Conviction',v:cm.consensus_conviction_tier||'--'},
      {k:'consensus_regime',l:'Regime',v:cm.consensus_regime||'--'},
      {k:'consensus_family_count',l:'Families',v:cm.consensus_family_count||0},
      {k:'consensus_authority_weighted',l:'Authority Wtd',v:cm.consensus_authority_weighted?'Yes':'No'},
    ];
    consMetrics.forEach(function(m) {
      var div = document.createElement('div');
      div.className = 'dashboard-metric';
      div.innerHTML = '<label>' + m.l + '</label><span>' + m.v + '</span>';
      consGrid.appendChild(div);
    });
    var summEl2 = document.getElementById('consensus-summary');
    summEl2.textContent = 'Net ' + (cm.consensus_net_score||0).toFixed(3) + ' | Tier ' + (cm.consensus_conviction_tier||'--') + ' | ' + (cm.consensus_active_votes||0) + ' votes';

    // Vote breakdown
    var votes = cm.consensus_votes || [];
    var voteBreakdown = document.getElementById('vote-breakdown');
    if (votes.length > 0) {
      var voteHtml = '<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">Vote contributions:</div>';
      votes.forEach(function(v) {
        var dirArrow2 = v.direction === 'long' ? '▲' : v.direction === 'short' ? '▼' : '–';
        voteHtml +=
          '<div class="vote-item">' +
            '<span class="vote-name">' + v.name + '</span>' +
            '<span>' + dirArrow2 + '</span>' +
            '<span>' + (v.confidence * 100).toFixed(0) + '%</span>' +
            '<span class="vote-contribution">weight=' + v.weight.toFixed(4) + ' contrib=' + v.contribution.toFixed(4) + '</span>' +
            (v.authority ? '<span class="vote-contribution">auth=' + v.authority.toFixed(1) + 'x</span>' : '') +
          '</div>';
      });
      voteBreakdown.innerHTML = voteHtml;
    } else {
      voteBreakdown.innerHTML = '';
    }
  } else {
    consSec.style.display = 'none';
  }

  // News
  var news = signal.news || [];
  var newsContainer = document.getElementById('popup-news');
  if (news.length > 0) {
    newsContainer.innerHTML = news.map(function(n) {
      var h = n.headline || '';
      var s = n.sentiment !== undefined ? ' (' + (n.sentiment > 0 ? '+' : '') + n.sentiment.toFixed(2) + ')' : '';
      return '<div class="news-item">' + h.substring(0, 120) + s + '</div>';
    }).join('');
  } else {
    newsContainer.innerHTML = '<em style="color:var(--text-muted)">No recent news</em>';
  }
}

function closePopup(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById('signal-popup-overlay').style.display = 'none';
  document.getElementById('validate-popup-overlay').style.display = 'none';
}

function closeValidatePopup() {
  document.getElementById('validate-popup-overlay').style.display = 'none';
}

/* ═══════════════════════════════════════════════════════════════
   Cycle / Auto-Run Controls
   ═══════════════════════════════════════════════════════════════ */

async function runCycle() {
  document.getElementById('cycle-status').textContent = 'Running...';
  document.getElementById('cycle-status').className = 'status-badge running';
  try {
    var controller = new AbortController();
    var timeout = setTimeout(function() { controller.abort(); }, 30000);
    var resp = await fetch('/api/run-cycle', { method: 'POST', signal: controller.signal });
    clearTimeout(timeout);
    var data = await resp.json();
    _status.last_cycle = data;
    updateUI();
  } catch(e) {
    console.error('Cycle run failed:', e);
    document.getElementById('cycle-status').textContent = 'Failed';
    document.getElementById('cycle-status').className = 'status-badge disconnected';
  }
}

async function toggleAutoRun() {
  var active = !_status.auto_run;
  try {
    await fetch('/api/auto-run/' + (active ? 'start' : 'stop'), { method: 'POST' });
    _status.auto_run = active;
    updateUI();
  } catch(e) {
    console.error('Auto-run toggle failed:', e);
  }
}

async function saveSettings() {
  var interval = document.getElementById('settings-interval').value;
  var desktopNotify = document.getElementById('settings-desktop-notify').checked;
  localStorage.setItem('lean_signals_interval', interval);
  localStorage.setItem('lean_signals_desktop_notify', desktopNotify);

  // Save thesis validation engine settings (applies immediately, no restart)
  var convictionDecay = document.getElementById('settings-conviction-decay')?.value;
  var minConviction = document.getElementById('settings-min-conviction')?.value;
  var thesisHorizon = document.getElementById('settings-thesis-horizon')?.value;
  var regimeInvalidationFactor = document.getElementById('settings-regime-invalidation-factor')?.value;
  var pnlProfitFloorAtr = document.getElementById('settings-pnl-profit-floor-atr')?.value;
  var pnlProfitConfirmAtr = document.getElementById('settings-pnl-profit-confirm-atr')?.value;
  var pnlForceExitAtr = document.getElementById('settings-pnl-force-exit-atr')?.value;
  var velocitySpikeAtr = document.getElementById('settings-velocity-spike-atr')?.value;
  var velocityPenaltyAtr = document.getElementById('settings-velocity-penalty-atr')?.value;
  var velocityBuildingMax = document.getElementById('settings-velocity-building-max')?.value;
  // Verdict thresholds
  var verdictExit = document.getElementById('settings-verdict-exit')?.value;
  var verdictReduce = document.getElementById('settings-verdict-reduce')?.value;
  var verdictHold = document.getElementById('settings-verdict-hold')?.value;
  var verdictBuy = document.getElementById('settings-verdict-buy')?.value;
  var verdictStrong = document.getElementById('settings-verdict-strong')?.value;

  try {
    await fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        conviction_decay: parseFloat(convictionDecay) ?? 0.97,
        min_conviction: parseFloat(minConviction) ?? 0.25,
        thesis_horizon: parseInt(thesisHorizon) || 30,
        regime_invalidation_factor: parseFloat(regimeInvalidationFactor) ?? 0.60,
        pnl_profit_floor_atr: parseFloat(pnlProfitFloorAtr) ?? 2.0,
        pnl_profit_confirm_atr: parseFloat(pnlProfitConfirmAtr) ?? 3.0,
        pnl_force_exit_atr: parseFloat(pnlForceExitAtr) ?? 2.5,
        velocity_spike_atr: parseFloat(velocitySpikeAtr) ?? 0.8,
        velocity_penalty_atr: parseFloat(velocityPenaltyAtr) ?? 0.4,
        velocity_building_max: parseFloat(velocityBuildingMax) ?? 0.70,
        odte_mode: document.getElementById('settings-odte-mode')?.checked || false,
        verdict_exit_threshold: parseInt(verdictExit) ?? -1,
        verdict_reduce_threshold: parseInt(verdictReduce) ?? 0,
        verdict_hold_threshold: parseInt(verdictHold) ?? 1,
        verdict_buy_threshold: parseInt(verdictBuy) ?? 2,
        verdict_strong_threshold: parseInt(verdictStrong) ?? 3,
      }),
    });
    _updateOdteBadge();
    alert('Settings saved (all thresholds apply immediately, no restart needed)');
  } catch(e) {
    alert('Settings saved locally, but server save failed: ' + e.message);
  }
}

/* ═══════════════════════════════════════════════════════════════
   Validation
   ═══════════════════════════════════════════════════════════════ */

async function runValidate() {
  var btn = document.getElementById('validate-btn');
  var status = document.getElementById('validate-status');
  btn.disabled = true;
  btn.textContent = 'Running...';
  status.textContent = 'Validating all strategies...';

  try {
    var resp = await fetch('/api/validate', { method: 'POST' });
    var data = await resp.json();
    btn.disabled = false;
    btn.textContent = 'Validate Strategies';

    if (data.status === 'error') {
      status.textContent = 'Error: ' + (data.message || 'unknown').substring(0, 200);
      return;
    }

    showValidatePopup(data);
    status.textContent = 'Validation complete: ' + (data.passed || 0) + ' passed, ' + (data.failed || 0) + ' failed';
  } catch(e) {
    btn.disabled = false;
    btn.textContent = 'Validate Strategies';
    status.textContent = 'Error: ' + e.message;
  }
}

function showValidatePopup(data) {
  var overlay = document.getElementById('validate-popup-overlay');
  if (!overlay) return;
  overlay.style.display = 'flex';

  document.getElementById('validate-total').textContent = data.total || data.tests_run || 0;
  document.getElementById('validate-passed').textContent = data.passed || 0;
  document.getElementById('validate-failed').textContent = data.failed || 0;
  document.getElementById('validate-skipped').textContent = data.skipped || 0;
  document.getElementById('validate-cycle').textContent = data.created_at_cycle || '-';

  // Badges
  var passBadge = document.getElementById('validate-badge-pass');
  var failBadge = document.getElementById('validate-badge-fail');
  passBadge.style.display = 'none';
  failBadge.style.display = 'none';
  if ((data.failed || 0) === 0 && (data.total || data.tests_run || 0) > 0) {
    passBadge.style.display = 'inline';
    passBadge.textContent = 'All Passed ✓';
  } else if ((data.failed || 0) > 0) {
    failBadge.style.display = 'inline';
    failBadge.textContent = (data.failed || 0) + ' Failed ✗';
  }

  // Populate signal groups
  var allContainer = document.getElementById('validate-signals-all');
  var stockContainer = document.getElementById('validate-signals-stock');
  var futureContainer = document.getElementById('validate-signals-future');
  var optionContainer = document.getElementById('validate-signals-option');

    var renderValidateSignals = function(signals, container) {
    if (!container) return;
    if (!signals || signals.length === 0) {
      container.innerHTML = '<div class="empty-state">No signals</div>';
      return;
    }
    container.innerHTML = '';
    signals.forEach(function(s) {
      var card = document.createElement('div');
      card.className = 'ticker-cell state-' + (s.state || 'none');
      card.onclick = function() {
        if (s) showSignalPopup(s, _status.last_cycle);
      };
      var dirCls = s.direction === 'long' ? 'long' : s.direction === 'short' ? 'short' : 'neutral';
      var vCls = _verdictClass(s.verdict || '');
      var dirArrow = s.direction === 'long' ? '\u25b2' : s.direction === 'short' ? '\u25bc' : '\u2013';
      card.innerHTML =
        '<div class="tc-verdict-row"><span class="verdict-badge ' + vCls + '">' + (s.verdict || 'NO ACTION') + '</span></div>' +
        '<div class="row1">' +
          '<div>' +
            '<span class="ticker-name">' + s.ticker + '</span>' +
            '<span class="state-badge ' + (s.state || 'none') + '">' + ((s.state || 'none').toUpperCase()) + '</span>' +
          '</div>' +
          '<span class="direction-badge ' + dirCls + '">' + dirArrow + ' ' + s.direction.toUpperCase() + '</span>' +
        '</div>' +
        '<div class="row2">' +
          '<span>Conf: <strong>' + (s.confidence * 100).toFixed(1) + '%</strong></span>' +
          '<span>Ns: <strong>' + (s.composite_score ? (s.composite_score * 100).toFixed(1) : '--') + '%</strong></span>' +
          '<span>Strats: <strong>' + (s.agreeing_count || 0) + '/' + (s.strategy_count || 0) + '</strong></span>' +
        '</div>' +
        '<div class="tc-bottom">' +
          '<span>' + (s.gate_passed ? '\u2705 passed' : '\u274C ' + (s.gate_reason || 'fail').replace(/_/g, ' ').slice(0, 25)) + '</span>' +
          '<span style="margin-left:auto">$' + (s.current_price || 0).toFixed(2) + '</span>' +
        '</div>';
      container.appendChild(card);
    });
  };

  var sigs = data.signals || {};
  renderValidateSignals(sigs.all || sigs.stock || [], allContainer);
  renderValidateSignals(sigs.stock || [], stockContainer);
  renderValidateSignals(sigs.future || [], futureContainer);
  renderValidateSignals(sigs.option || [], optionContainer);
}

/* ═══════════════════════════════════════════════════════════════
   Cheat Sheet — Signal & Parameter Reference
   ═══════════════════════════════════════════════════════════════

   CHEAT SHEET RULE: Every time you add or modify a signal state,
   conviction tier, health score factor, regime type, warning chip,
   card field, market dashboard metric, or any user-facing parameter,
   you MUST update the cheatSheetData object below with the new/changed
   information. This keeps the dashboard self-documenting for traders.
   ═══════════════════════════════════════════════════════════════ */

function renderCheatSheet() {
  var container = document.getElementById('cheatsheet-content');
  if (!container) return;

  /* ── Structured Cheat Sheet Data ──
     Add/update sections here when features change.
     Each section: { id, title, icon, items: [{ term, badge, badgeClass, desc, interpret }] }
     - term: the name of the concept
     - badge: optional visual preview (shows a mini badge)
     - badgeClass: CSS class for the badge preview
     - desc: what it means (1-2 sentences)
     - interpret: how to use it for trading decisions
  */
  var sections = [
    {
      id: 'signal-states', title: 'Signal States', icon: '📊',
      desc: 'The state machine that tracks conviction over multiple cycles. States build from NONE → CONFIRMED. WEAKENING is a warning before signals disappear.',
      items: [
        { term: 'NONE', badge: 'NONE', badgeClass: 'watching', desc: 'No signal has been detected for this ticker yet.', interpret: 'Ignore — nothing actionable.' },
        { term: 'WATCHING', badge: 'WATCHING', badgeClass: 'watching', desc: 'A potential flip or new direction is forming. Needs more cycles to confirm.', interpret: 'Monitor but do NOT enter. Wait for PENDING or higher.' },
        { term: 'WEAKENING', badge: 'WEAKENING', badgeClass: 'weakening', desc: 'A previously ACTIVE/CONFIRMED signal is losing conviction. Fires TAKE PROFIT notification.', interpret: '⚠️ Close or reduce position. Signal is dying.' },
        { term: 'PENDING', badge: 'PENDING x2', badgeClass: 'pending', desc: '2+ consecutive cycles in the same direction. Building conviction.', interpret: 'Small pilot position OK. Wait for ACTIVE for full size.' },
        { term: 'ACTIVE', badge: 'ACTIVE x3', badgeClass: 'active', desc: '3+ consecutive cycles in the same direction. Strong conviction.', interpret: '✅ Safe to enter with standard position size.' },
        { term: 'CONFIRMED', badge: 'CONFIRMED x5', badgeClass: 'confirmed', desc: '5+ consecutive cycles in the same direction. Maximum conviction.', interpret: '✅✅ Highest confidence. Can scale up position.' }
      ]
    },
    {
      id: 'conviction-tiers', title: 'Conviction Tiers', icon: '🏆',
      desc: 'Confidence bands that determine position sizing and signal quality. Tier is based on confidence % and regime alignment.',
      items: [
        { term: 'Bronze', badge: '<80%', badgeClass: 'watching', desc: 'Confidence below 80%. Weak signal — insufficient agreement or low net score.', interpret: 'Skip or use minimum position size.' },
        { term: 'Silver', badge: '80-90%', badgeClass: 'pending', desc: 'Confidence 80-90%. Decent agreement among strategies.', interpret: 'Standard position, proceed with caution.' },
        { term: 'Gold', badge: '90-95%', badgeClass: 'active', desc: 'Confidence 90-95%. Strong agreement AND regime-aligned (not counter-trend).', interpret: 'Full position size, high conviction.' },
        { term: 'Platinum', badge: '>95%', badgeClass: 'confirmed', desc: 'Confidence 95%+. Near-unanimous strategy agreement + regime-aligned + high family diversity.', interpret: 'Maximum conviction. Best possible signal.' }
      ]
    },
    {
      id: 'health-score', title: 'Signal Health Score', icon: '💚',
      desc: 'A parallel quality metric that runs ALONGSIDE the state machine with zero lag. A CONFIRMED signal with low Health is dangerous — the state machine is slow to react, but Health detects decay immediately.',
      items: [
        { term: 'Robust (70+)', badge: 'ROBUST', badgeClass: 'confirmed', desc: 'Health score >= 70. All 5 vital signs are strong.', interpret: '✅ Signal is healthy. Confidently enter/hold.' },
        { term: 'Caution (40-69)', badge: 'CAUTION', badgeClass: 'pending', desc: 'Health score 40-69. Some weakening detected in one or more factors.', interpret: '⚠️ Monitor closely. Reduce size or wait.' },
        { term: 'Fragile (<40)', badge: 'FRAGILE', badgeClass: 'weakening', desc: 'Health score < 40 AND signal is ACTIVE/CONFIRMED. Signal is deteriorating but state hasn\'t changed yet.', interpret: '🔴 AVOID ENTRY. If holding, tighten stops.' },
        { term: 'Terminal (0)', badge: 'TERMINAL', badgeClass: 'watching', desc: 'Health score = 0. All factors have collapsed. Signal is dead.', interpret: '❌ Do not trade. Signal about to flip.' }
      ]
    },
    {
      id: 'health-factors', title: 'Health Score Factors', icon: '🔬',
      desc: 'The 5 component factors that make up the Signal Health Score. Each contributes a weighted percentage to the total.',
      items: [
        { term: 'Net Score Momentum', badge: '30%', badgeClass: 'watching', desc: 'Is the net score improving or deteriorating over the last 3 cycles?', interpret: 'Fading net score = early warning of a flip.' },
        { term: 'Strategy Retention', badge: '25%', badgeClass: 'watching', desc: 'What % of strategies that voted last cycle are still voting this cycle?', interpret: 'Dropping strategies = conviction is eroding.' },
        { term: 'Confidence Tightness', badge: '20%', badgeClass: 'watching', desc: 'How tightly clustered are strategy confidence values? (CV = coefficient of variation)', interpret: 'Wide spread = strategies are fighting each other.' },
        { term: 'Family Diversity', badge: '15%', badgeClass: 'watching', desc: 'How many strategy families are contributing? Single-family dominance = fragile.', interpret: '1-family signals are vulnerable to regime shifts.' },
        { term: 'Margin Above Threshold', badge: '10%', badgeClass: 'watching', desc: 'How far is the net score above the minimum threshold (0.20)?', interpret: 'Close to threshold = one weak cycle could kill the signal.' }
      ]
    },
    {
      id: 'regime-types', title: 'Regime Types', icon: '🌤️',
      desc: 'The market\'s current personality. Regime determines which strategies get weighted and whether counter-trend signals are allowed.',
      items: [
        { term: 'strong_uptrend', badge: 'STRONG UP', badgeClass: 'confirmed', desc: 'ADX > 25, EMA sloping up, RSI > 65, price above SMA50.', interpret: 'Trend strategies boosted (1.2x). Mean reversion crushed (0.15x). SHORT signals penalized 0.25x.' },
        { term: 'strong_downtrend', badge: 'STRONG DOWN', badgeClass: 'weakening', desc: 'ADX > 25, EMA sloping down, RSI < 35, price below SMA50.', interpret: 'Trend strategies boosted. LONG signals penalized 0.25x. Higher threshold to clear (0.50).' },
        { term: 'ranging', badge: 'RANGING', badgeClass: 'pending', desc: 'ADX < 20, Bollinger Bands squeezed, RSI ~50, price near SMA20.', interpret: 'All strategy families balanced. Full position sizing. Both LONG/SHORT allowed.' },
        { term: 'high_volatility', badge: 'HIGH VOL', badgeClass: 'weakening', desc: 'ATR% > 3%, BB bandwidth > 8%, volume spike.', interpret: 'Position sizing reduced to 0.50x. Counter-trend blocked entirely. Wider stops recommended.' }
      ]
    },
    {
      id: 'card-fields', title: 'Signal Card Fields', icon: '🃏',
      desc: 'Every field shown on the signal cards in the Active Signals tab.',
      items: [
        { term: 'Confidence %', badge: '73.2%', badgeClass: 'watching', desc: 'Weighted confidence from all agreeing strategies (0-100%).', interpret: 'Higher = stronger agreement. Compare with Health Score.' },
        { term: 'Net Score', badge: '+0.45', badgeClass: 'watching', desc: 'Composite score from -1.0 (max short) to +1.0 (max long).', interpret: 'Magnitude matters more than sign. ±0.20 = weak, ±0.70 = strong.' },
        { term: 'Strategy Count', badge: '7/12', badgeClass: 'watching', desc: 'Agreeing strategies / total strategies that ran.', interpret: '7/12 voting LONG = decent. 3/12 = weak consensus.' },
        { term: 'Regime', badge: 'strong_uptrend', badgeClass: 'watching', desc: 'Current market regime classification.', interpret: 'Counter-trend signals are risky. Aligned signals are safer.' },
        { term: 'Price', badge: '$584.20', badgeClass: 'watching', desc: 'Current market price of the underlying (midpoint).', interpret: 'Use with entry/SL/TP levels for trade planning.' },
        { term: 'Entry / SL / TP', badge: 'R:R 2.5', badgeClass: 'watching', desc: 'Entry price, Stop Loss, Take Profit, and Risk:Reward ratio.', interpret: 'R:R > 2.0 is good. R:R < 1.0 is risky.' },
        { term: 'State xN', badge: 'CONFIRMED x8', badgeClass: 'confirmed', desc: 'Current signal state + number of consecutive same-direction cycles.', interpret: 'Streak length shows persistence. Long streaks are reliable.' },
        { term: 'Age Decay', badge: 'decaying (0.72)', badgeClass: 'watching', desc: 'Confidence multiplier from signal age. Starts decaying after 15 min.', interpret: 'Old signals (decay < 0.70) are less actionable.' },
        { term: 'Gate', badge: '✅ Passed', badgeClass: 'confirmed', desc: 'Pre-filter check. ✅ = passed all checks. ❌ = blocked (see reason).', interpret: 'Gate-rejected signals are unreliable. Only trade gate-passed signals.' },
        { term: 'Verdict', badge: 'STRONG LONG', badgeClass: 'confirmed', desc: 'Final synthesis of ALL indicators into one trade decision using a Base+Modifier scoring matrix.', interpret: 'The single most important field on the card. Overrides all other signals.' }
      ]
    },
    {
      id: 'verdict-system', title: 'Final Verdict System', icon: '⚖️',
      desc: 'Synthesizes Signal State + Conviction Tier + Health Score + Regime + Counter-Trend + Age Decay + Gate into one actionable trade decision. Uses a Base+Modifier scoring matrix with hard safety caps.',
      items: [
        { term: 'STRONG LONG / SHORT', badge: 'STRONG LONG', badgeClass: 'confirmed', desc: 'Score ≥ 3. CONFIRMED + Platinum/Gold + Robust + regime-aligned. Maximum conviction.', interpret: '✅✅ Highest quality signal. Full position size. Safe to enter aggressively.' },
        { term: 'LONG / SHORT', badge: 'LONG', badgeClass: 'active', desc: 'Score = 2. ACTIVE/CONFIRMED with decent tier, health, and alignment.', interpret: '✅ Standard entry. Normal position size.' },
        { term: 'HOLD', badge: 'HOLD', badgeClass: 'pending', desc: 'Score = 1. Signal valid but showing some weakness (caution health, silver tier, or gate failure).', interpret: '⚠️ Hold existing position. Do NOT add. Monitor for upgrade or degradation.' },
        { term: 'REDUCE', badge: 'REDUCE', badgeClass: 'weakening', desc: 'Score = 0. Signal deteriorating. Fragile health OR multiple risk penalties.', interpret: '🔴 Reduce position size significantly. Tighten stops. Prepare to exit.' },
        { term: 'EXIT', badge: 'EXIT', badgeClass: 'weakening', desc: 'Score ≤ -1. Signal collapsing. Terminal health, counter-trend, or multiple failures.', interpret: '❌ Close position immediately. Signal has reversed or died.' },
        { term: 'WAIT', badge: 'WAIT', badgeClass: 'pending', desc: 'PENDING state with decent health. Building conviction but not ready.', interpret: '⏳ Do not enter yet. Monitor for upgrade to ACTIVE.' },
        { term: 'AVOID', badge: 'AVOID', badgeClass: 'watching', desc: 'Score ≤ -2 OR PENDING + fragile health. Signal is too risky.', interpret: '🚫 Stay away. Do not trade this ticker in this direction.' },
        { term: 'NO ACTION', badge: 'NO ACTION', badgeClass: 'watching', desc: 'NONE/WATCHING state or neutral direction. No directional signal exists.', interpret: 'Nothing actionable. Wait for a signal to form.' },
        { term: 'Scoring Matrix', badge: 'CONFIRMED=+2...', badgeClass: 'watching', desc: 'Base: CONFIRMED=+2, ACTIVE=+1. Tier: Plat=+1, Gold=0, Silver=-1, Bronze=-2. Health: Robust=0, Caution=-1, Fragile=-2. Regime Aligned=+1. Counter-Trend=-1. Gate Failed=-2. Age Decay<0.7=-1.', interpret: 'Use this to understand why a verdict was assigned. Higher total = higher conviction required for STRONG.' },
        { term: 'Safety Caps', badge: 'CAPS', badgeClass: 'watching', desc: 'Fragile health caps max verdict at REDUCE. Gate-failed caps at HOLD. Terminal health forces EXIT/AVOID regardless of score.', interpret: 'Safety caps prevent overconfident trades on weak signals.' }
      ]
    },
    {
      id: 'warning-chips', title: 'Warning Chips', icon: '⚠️',
      desc: 'Small colored badges that appear on signal cards when the Health Score detects specific problems.',
      items: [
        { term: 'Fading', badge: 'Fading', badgeClass: 'weakening', desc: 'Net score is trending downward over the last 3 cycles.', interpret: 'Signal is losing steam. Watch for flip.' },
        { term: 'Scattered', badge: 'Scattered', badgeClass: 'weakening', desc: 'Strategy confidence values have high variance (CV > 0.6).', interpret: 'Strategies disagree on strength even if they agree on direction.' },
        { term: '1-family', badge: '1-family', badgeClass: 'weakening', desc: 'Only one strategy family is contributing to the signal.', interpret: 'Vulnerable — if that family\'s regime changes, signal dies.' },
        { term: 'Near neutral', badge: 'Near neutral', badgeClass: 'weakening', desc: 'Net score is within 0.05 of the minimum threshold.', interpret: 'One weak cycle could drop the signal below threshold.' },
        { term: 'Key exit', badge: 'Key exit', badgeClass: 'weakening', desc: 'One or more top-contributing strategies from the previous cycle have dropped out.', interpret: 'The strongest supporters are abandoning the signal.' },
        { term: 'Stale', badge: 'Stale 8cyc', badgeClass: 'weakening', desc: 'No strong cycle (net score > 0.4) in the last N cycles.', interpret: 'Signal is CONFIRMED but coasting on past glory.' },
        { term: 'X dropped', badge: '5 dropped', badgeClass: 'weakening', desc: 'N strategies that voted last cycle have dropped out this cycle.', interpret: 'Mass strategy exodus — signal may collapse soon.' }
      ]
    },
    {
      id: 'direction-badges', title: 'Direction & Badges', icon: '🎯',
      desc: 'Visual indicators that appear on signal cards for direction, flips, and special conditions.',
      items: [
        { term: 'LONG', badge: '▲ LONG', badgeClass: 'long', desc: 'Strategies predict price will go UP.', interpret: 'Buy calls, go long futures, or buy stock.' },
        { term: 'SHORT', badge: '▼ SHORT', badgeClass: 'short', desc: 'Strategies predict price will go DOWN.', interpret: 'Buy puts, go short futures, or short stock.' },
        { term: 'Major Flip', badge: '↻ FLIP', badgeClass: 'weakening', desc: 'Significant direction change detected (score >= 0.6). Requires 2+ counter-cycles.', interpret: 'Close existing position. Consider reversing.' },
        { term: 'Potential Flip', badge: '↻ WATCHING', badgeClass: 'pending', desc: 'Early flip signal detected but needs more cycles to confirm.', interpret: 'Tighten stops. Don\'t add to position.' },
        { term: 'TAKE PROFIT', badge: '⚠️ TAKE PROFIT', badgeClass: 'weakening', desc: 'Signal has entered WEAKENING state. Time to close.', interpret: 'Close position and take profit. Do NOT re-enter.' },
        { term: 'FRAGILE', badge: '⚠️ FRAGILE', badgeClass: 'weakening', desc: 'Signal is CONFIRMED but Health Score < 40. Dangerous to enter.', interpret: 'Do NOT enter new positions. If holding, exit or tighten stops.' }
      ]
    },
    {
      id: 'market-dashboard', title: 'Market Dashboard Metrics', icon: '📈',
      desc: 'Metrics shown in the mini-dashboard row on option signal cards and in the full dashboard popup.',
      items: [
        { term: 'ATM IV', badge: 'IV 18.5%', badgeClass: 'watching', desc: 'At-the-money implied volatility. Higher = options are more expensive.', interpret: 'IV > 30% = expensive options (sell premium). IV < 15% = cheap (buy options).' },
        { term: 'P/C Vol Ratio', badge: 'P/C 0.85', badgeClass: 'watching', desc: 'Put/Call volume ratio. > 1.0 = more puts traded (bearish). < 0.7 = more calls (bullish).', interpret: 'Extreme readings (>1.5 or <0.4) signal sentiment extremes.' },
        { term: 'VIX Spot', badge: 'VIX 16.2', badgeClass: 'watching', desc: 'CBOE Volatility Index. Measures expected S&P 500 volatility.', interpret: 'VIX > 25 = fear (buy). VIX < 12 = complacency (caution).' },
        { term: 'Breadth State', badge: 'bullish ⚡', badgeClass: 'confirmed', desc: 'Market breadth across ES/NQ/YM/RTY futures + VIX alignment.', interpret: 'Bullish breadth + thrust = strong uptrend. Bearish breadth = downtrend.' },
        { term: 'Gamma Flip', badge: '$582.50', badgeClass: 'watching', desc: 'Price level where dealer gamma flips from long to short (or vice versa).', interpret: 'Above gamma flip = dealers are short gamma (amplifies moves). Below = stabilizing.' },
        { term: 'DTE', badge: 'DTE 3', badgeClass: 'watching', desc: 'Days to expiration for the recommended option.', interpret: '0DTE = very risky. 7+ DTE = more time for thesis to play out.' },
        { term: 'Delta Positioning', badge: '0.65', badgeClass: 'watching', desc: 'Net delta exposure of options market. Positive = dealers long, negative = dealers short.', interpret: 'Extreme positioning can cause squeezes when unwinding.' },
        { term: 'Skew 1M', badge: 'Skew +3.2', badgeClass: 'watching', desc: '1-month put vs call IV skew. Positive = puts more expensive (fear).', interpret: 'High skew = fear premium in puts. Low/negative skew = complacency.' }
      ]
    },
    {
      id: 'consensus-meta', title: 'Consensus Meta', icon: '📐',
      desc: "Metrics shown in the 'Consensus Detail' section of signal popups. These explain how the final signal was constructed from individual strategy votes.",
      items: [
        { term: 'Net Score', badge: '+0.45', badgeClass: 'watching', desc: 'Weighted sum of all strategy votes (-1.0 = max short, +1.0 = max long).', interpret: 'Magnitude matters: |score| > 0.50 = strong, |score| < 0.20 = weak.' },
        { term: 'Active Votes', badge: '12', badgeClass: 'watching', desc: 'Number of strategies that produced a non-neutral vote this cycle.', interpret: 'More active votes = broader signal foundation.' },
        { term: 'Weighted Long/Short', badge: '+0.65 / -0.12', badgeClass: 'watching', desc: 'Total weight on each side. The larger side determines direction.', interpret: 'Wide gap between long/short = strong consensus. Close gap = uncertain.' },
        { term: 'Counter-Trend', badge: 'Yes', badgeClass: 'weakening', desc: 'Signal direction opposes the current market regime (e.g., LONG in strong_downtrend).', interpret: 'Counter-trend signals need higher confidence (0.50 threshold) and are penalized 0.25x.' },
        { term: 'Family Count', badge: '4 families', badgeClass: 'confirmed', desc: 'Number of distinct strategy families that contributed votes.', interpret: '3+ families = diverse signal. 1 family = fragile.' },
        { term: 'Authority Weighted', badge: 'Yes', badgeClass: 'confirmed', desc: 'Whether strategy authority multipliers (based on historical accuracy) were applied.', interpret: 'Authority weighting gives more weight to strategies with better track records.' },
        { term: 'Regime Boost', badge: '+0.15', badgeClass: 'confirmed', desc: 'Bonus added when signal direction matches the current market regime.', interpret: 'Regime-aligned signals get a small confidence boost.' },
        { term: 'Total Weight', badge: '14.20', badgeClass: 'watching', desc: 'Sum of all strategy weights (before normalization).', interpret: 'Higher total weight = more strategies contributing meaningfully.' }
      ]
    },
        {
      id: 'gate-system', title: 'Gate System', icon: '🚦',
      desc: "The gate is a pre-filter that runs before signals are emitted. It checks market conditions and blocks signals likely to be false positives. Each signal card shows 'Gate: ✅' (passed) or 'Gate: ❌ <reason>' (rejected).",
      items: [
        { term: 'Macro Event', badge: '❌ macro', badgeClass: 'weakening', desc: 'Major economic event window (FOMC, CPI, NFP, etc.). Gate blocks signals during these.', interpret: 'Never trade during macro events — volatility is unpredictable.' },
        { term: 'Volume Gate', badge: '❌ volume', badgeClass: 'weakening', desc: 'Current volume is below the minimum threshold for reliable signals.', interpret: 'Low volume = unreliable price action. Wait for volume to pick up.' },
        { term: 'Spread Gate', badge: '❌ spread', badgeClass: 'weakening', desc: 'Bid-ask spread is too wide, making entries/exits too expensive.', interpret: 'Wide spreads kill profitability. Avoid unless spread < 0.1% of price.' },
        { term: 'Correlation Conflict', badge: '❌ conflict', badgeClass: 'weakening', desc: 'Correlated instruments disagree. E.g., ES is LONG but NQ is SHORT.', interpret: 'When ES and NQ conflict, both signals are less reliable.' },
        { term: 'Globex Regime', badge: '⚠️ globex', badgeClass: 'pending', desc: 'After-hours session. Thresholds are relaxed but more confirmation is needed.', interpret: 'Globex signals need 3 cycles instead of 2. Lower confidence is expected.' },
        { term: 'Min Signal Threshold', badge: '0.20', badgeClass: 'watching', desc: 'Net score must exceed 0.20 (or 0.50 if counter-trend) to pass.', interpret: 'Signals below threshold are noise, not actionable.' }
      ]
    },
    {
      id: 'time-windows', title: 'Time Windows', icon: '🕐',
      desc: 'Session time windows that affect strategy weights and signal behavior.',
      items: [
        { term: 'power_hour', badge: 'power_hour', badgeClass: 'confirmed', desc: 'Last hour of regular trading (3:00-4:00 PM ET). Highest volume and liquidity.', interpret: 'Best time for entries. Volume confirms moves.' },
        { term: 'midday_lull', badge: 'midday_lull', badgeClass: 'watching', desc: 'Middle of the session (11:30-2:00 PM ET). Lower volume, more noise.', interpret: 'Reduce position size. Avoid breakout strategies.' },
        { term: 'market_open', badge: 'market_open', badgeClass: 'pending', desc: 'First 30 min of regular trading (9:30-10:00 AM ET). High volatility, gap fills.', interpret: 'Wait for initial range to form. Don\'t chase opening spike.' },
        { term: 'market_close', badge: 'market_close', badgeClass: 'pending', desc: 'Last 15 min of regular trading (3:45-4:00 PM ET). Positioning for overnight.', interpret: 'Be aware of end-of-day positioning flows.' },
        { term: 'after_hours', badge: 'after_hours', badgeClass: 'watching', desc: 'Globex / pre-market / post-market. Low volume, wider spreads.', interpret: 'Use relaxed thresholds. More confirmation needed. Lower confidence.' }
      ]
    }
  ];

  // ── Build HTML ──
  var html = '';
  html += '<div class="cheatsheet-header">';
  html += '<h2>📖 Signal & Parameter Cheat Sheet</h2>';
  html += '<p class="cheatsheet-subtitle">Quick reference for every signal state, metric, badge, and parameter in Lean Signals. Click any section to expand.</p>';
  html += '</div>';

  // Table of contents
  html += '<div class="cheatsheet-toc">';
  sections.forEach(function(sec) {
    html += '<a href="#cs-' + sec.id + '" class="toc-link">' + sec.icon + ' ' + sec.title + '</a>';
  });
  html += '</div>';

  // Sections
  sections.forEach(function(sec) {
    html += '<div id="cs-' + sec.id + '" class="cheatsheet-section">';
    html += '<div class="cheatsheet-section-header" onclick="toggleCollapse(\'cs-body-' + sec.id + '\', this)">';
    html += '<span>' + sec.icon + ' <strong>' + sec.title + '</strong></span>';
    html += '<span class="collapse-icon">▼</span>';
    html += '</div>';
    html += '<div id="cs-body-' + sec.id + '" class="cheatsheet-section-body" style="display:none">';
    html += '<p class="cheatsheet-section-desc">' + sec.desc + '</p>';
    html += '<table class="cheatsheet-table">';
    html += '<thead><tr><th>Term</th><th>Preview</th><th>Description</th><th>How to Interpret</th></tr></thead>';
    html += '<tbody>';
    sec.items.forEach(function(item) {
      html += '<tr>';
      html += '<td class="cs-term">' + item.term + '</td>';
      html += '<td class="cs-badge"><span class="state-badge ' + (item.badgeClass || 'watching') + '">' + (item.badge || '') + '</span></td>';
      html += '<td class="cs-desc">' + item.desc + '</td>';
      html += '<td class="cs-interpret">' + item.interpret + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    html += '</div>';
    html += '</div>';
  });

  html += '<div class="cheatsheet-footer">' + '<p>💡 <strong>Tip:</strong> This cheat sheet is updated whenever new features are added. If something is missing, check the <code>renderCheatSheet()</code> function in <code>static/app.js</code>.</p>' + '</div>';

  container.innerHTML = html;
}

function switchValidateSubTab(sub) {
  var container = document.getElementById('validate-popup-body');
  container.querySelectorAll('.sub-tab').forEach(function(st) {
    st.classList.toggle('active', st.dataset.subtab === sub);
  });
  ['all', 'stock', 'future', 'option'].forEach(function(t) {
    var el = document.getElementById('validate-signals-' + t);
    if (el) el.classList.toggle('active', t === sub);
  });
}
