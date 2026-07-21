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
});

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

function renderSignals(cycle) {
  // STATE: Error
  if (cycle.status === 'error') {
    ['stock','future','option'].forEach(function(type) {
      var container = document.getElementById('signals-' + type);
      if (container) container.innerHTML = '<div class="error-state">❌ Cycle failed: ' + (cycle.reason || 'unknown') + '</div>';
    });
    return;
  }

  var groups = cycle.signals || {};
  var flips = cycle.flips || {};
  var flipPotentials = cycle.flips_potential || {};
  var signalStates = cycle.signal_states || {};

  // ── Update sticky event persistence ──
  var allSignals = [];
  ['stock', 'future', 'option'].forEach(function(type) {
    var sigs = groups[type] || [];
    sigs.forEach(function(s) {
      // Derive weakening state for sticky TP tracking
      var ss = (signalStates && signalStates.by_ticker) ? signalStates.by_ticker[s.ticker] : null;
      s.weakening = (ss && ss.state === 'weakening');
      allSignals.push(s);
    });
  });
  _updateStickyEvents(allSignals, flips, flipPotentials);

  ['stock', 'future', 'option'].forEach(function(type) {
    var container = document.getElementById('signals-' + type);
    if (!container) return;

    var signals = groups[type] || [];

    // STATE: Empty
    if (signals.length === 0) {
      var emptyMsg = (type === 'option') ? '📭 No option signals — check chain health or wait for confluence'
        : (type === 'future') ? '📭 No futures signals — market conditions below threshold'
        : '📭 No stock signals — no tickers meeting criteria';
      container.innerHTML = '<div class="empty-state">' + emptyMsg + '</div>';
      return;
    }

    // STATE: Data
    container.innerHTML = '';
    signals.forEach(function(s) {
      var card = createSignalCard(s, cycle, flips, flipPotentials, signalStates);
      container.appendChild(card);
    });
  });
}

function createSignalCard(signal, cycle, flips, flipPotentials, signalStates) {
  var card = document.createElement('div');
  card.className = 'signal-card';
  var dirClass = signal.direction === 'long' ? 'long' : signal.direction === 'short' ? 'short' : 'neutral';
  var dirArrow = signal.direction === 'long' ? '▲' : signal.direction === 'short' ? '▼' : '–';    // ── Signal state badge (persistence) — O(1) lookup via by_ticker ──
    var signalStateHtml = '';
    var isWeakening = false;
    var thisState = (signalStates && signalStates.by_ticker) ? signalStates.by_ticker[signal.ticker] : null;
    if (thisState && thisState.state && thisState.state !== 'none') {
        var dur = stateAge(thisState.state_since);
        var durLabel = dur ? ' for ' + dur : '';
        var stateClass = thisState.state;
        signalStateHtml = '<span class="state-badge ' + stateClass + '">' + thisState.state.toUpperCase() + ' x' + (thisState.consecutive_same || 1) + durLabel + '</span>';
        if (thisState.state === 'weakening') isWeakening = true;
        // Age decay display
        if (thisState.age_decay && thisState.age_decay < 1.0) {
          signalStateHtml += ' <span class="age-decay-badge">decaying (' + thisState.age_decay.toFixed(2) + ')</span>';
        }
      }

  // ── Entry/Exit levels ──
  var entryPrice = signal.entry_price || signal.current_price || 0;
  var sl = signal.stop_loss || 0;
  var tp = signal.take_profit || 0;
  var rr = signal.risk_reward || 0;
  var levelsHtml = '';
  if (sl > 0 && tp > 0) {
    levelsHtml =
      '<div class="row3">' +
        '<span class="level-label">Entry</span><span class="level-val">$' + entryPrice.toFixed(2) + '</span>' +
        '<span class="level-label">SL</span><span class="level-val sl">$' + sl.toFixed(2) + '</span>' +
        '<span class="level-label">TP</span><span class="level-val tp">$' + tp.toFixed(2) + '</span>' +
        '<span class="level-label">R:R</span><span class="level-val rr">' + rr.toFixed(1) + '</span>' +
      '</div>';
  }

  // ── Option premium levels ──
  var optPrem = signal.option_entry_premium || 0;
  if (optPrem > 0 && signal.instrument_type === 'option') {
    var recStrike = signal.recommended_strike || 0;
    var recOtm = signal.recommended_otm || '';
    var recType = signal.recommended_option_type || '';
    var estWin = signal.estimated_win_rate ? (signal.estimated_win_rate * 100).toFixed(0) + '%' : '--';
    var strikeLine = '';
    if (recStrike > 0) {
      strikeLine =
        '<span class="level-label">' + recOtm + ' ' + recType + '</span>' +
        '<span class="level-val tp">$' + recStrike.toFixed(0) + '</span>' +
        '<span class="level-label">Est Win</span><span class="level-val rr">' + estWin + '</span>';
    }
    var posLine = (signal.instrument_type === 'option' && signal.direction !== 'neutral')
      ? '<span class="level-label">Size</span><span class="level-val">1 contract</span>' : '';
    var todLabel = signal.time_window || '';
    var vwapLabel = signal.vwap_position || '';
    var metaLine = '';
    if (todLabel || vwapLabel) {
      metaLine =
        (todLabel ? '<span class="level-label">' + todLabel.replace(/_/g, ' ') + '</span>' : '') +
        (vwapLabel ? '<span class="level-val">VWAP: ' + vwapLabel + '</span>' : '');
    }
    levelsHtml +=
      (strikeLine ? '<div class="row3 option-strike">' + strikeLine + '</div>' : '') +
      '<div class="row3 option-premium">' +
        '<span class="level-label">Prem Entry</span><span class="level-val">$' + optPrem.toFixed(2) + '</span>' +
        '<span class="level-label">Prem SL</span><span class="level-val sl">$' + (signal.option_sl_premium || 0).toFixed(2) + '</span>' +
        '<span class="level-label">Prem TP</span><span class="level-val tp">$' + (signal.option_tp_premium || 0).toFixed(2) + '</span>' +
      '</div>' +
      (posLine ? '<div class="row3 option-size">' + posLine + '</div>' : '') +
      (metaLine ? '<div class="row3 option-meta">' + metaLine + '</div>' : '');
  }

  // ── Mini dashboard for options ──
  var miniDash = signal.market_dashboard || {};
  var miniDashHtml = '';
  if (Object.keys(miniDash).length > 0 && signal.instrument_type === 'option') {
    var breadthLabel = miniDash.breadth_state || '—';
    var breadthClass = breadthLabel === 'bullish' || breadthLabel === 'slightly_bullish' ? 'accent'
      : breadthLabel === 'bearish' || breadthLabel === 'slightly_bearish' ? 'danger' : '';
    var thrustIcon = miniDash.breadth_thrust ? ' ⚡' : '';
    miniDashHtml =
      '<div class="row-mini">' +
        '<span>IV <strong>' + (miniDash.iv || 0).toFixed(1) + '%</strong></span>' +
        '<span>P/C <strong>' + (miniDash.pc_ratio != null ? miniDash.pc_ratio : '—') + '</strong></span>' +
        '<span>VIX <strong>' + (miniDash.vix_spot != null ? miniDash.vix_spot : '—') + '</strong></span>' +
        '<span>Breadth <strong class="' + breadthClass + '">' + breadthLabel + thrustIcon + '</strong></span>' +
        '<span>DTE <strong>' + (miniDash.dte != null ? miniDash.dte : '—') + '</strong></span>' +
      '</div>';
  }

  // ── Flip badge (from persistence engine + sticky persistence) ──
  var flipInfo = flips[signal.ticker] || null;
  var flipPotInfo = (!flipInfo) ? (flipPotentials[signal.ticker] || null) : null;
  var stickyFlip = _stickyFlips[signal.ticker] || null;
  var flipRow = '';
  if (flipInfo) {
    var score = flipInfo.score || 0;
    var flipClass = score >= 0.8 ? 'flip-badge major' : 'flip-badge';
    flipRow = '<div class="row-flip"><span class="' + flipClass + '">↻ FLIP ' + (flipInfo.from || '').toUpperCase() + ' → ' + (flipInfo.to || '').toUpperCase() + ' score=' + score.toFixed(2) + '</span></div>';
  } else if (flipPotInfo) {
    var needed = flipPotInfo.needs_cycles || 1;
    flipRow = '<div class="row-flip"><span class="flip-badge potential">↻ WATCHING: needs ' + needed + ' more cycle' + (needed > 1 ? 's' : '') + ' to confirm</span></div>';
  } else if (stickyFlip) {
    var stickyClass = stickyFlip.potential ? 'flip-badge potential' : (stickyFlip.score >= 0.8 ? 'flip-badge major' : 'flip-badge');
    var stickyLabel = stickyFlip.from ? (stickyFlip.from.toUpperCase() + ' → ' + stickyFlip.to.toUpperCase()) : 'recent flip';
    flipRow = '<div class="row-flip"><span class="' + stickyClass + ' sticky">↻ FLIP ' + stickyLabel + ' (' + stickyFlip.cyclesLeft + ' cycles ago)</span></div>';
  }

  // TAKE PROFIT banner (current + sticky persistence)
  var tpBannerHtml = '';
  var stickyTp = _stickyTps[signal.ticker] || null;
  if (isWeakening) {
    tpBannerHtml = '<div class="take-profit-banner">⚠️ TAKE PROFIT — Signal Weakening</div>';
  } else if (stickyTp && stickyTp.cyclesLeft > 0) {
    tpBannerHtml = '<div class="take-profit-banner sticky">⚠️ TAKE PROFIT — Signal Weakened (' + stickyTp.cyclesLeft + ' cycles ago)</div>';
  }

  // ── Build card ──
    // Conviction meter + tier badge
    var convTier = (signal.consensus_meta && signal.consensus_meta.consensus_conviction_tier) || 'bronze';
    var convPct = signal.confidence * 100;
    var meterWidth = Math.max(convPct, 5);
    var tierBadge = '<span class="tier-badge ' + convTier + '">' + convTier.toUpperCase() + '</span>';
    var convictionHtml =
      '<div class="conviction-meter">' +
        tierBadge +
        '<span class="meter-pct ' + convTier + '">' + convPct.toFixed(1) + '%</span>' +
        '<div class="meter-bar ' + convTier + '" style="width:' + meterWidth + '%"></div>' +
      '</div>';

    // ── Signal Health Score bar (parallel quality metric) ──
    var healthScores = cycle.health_scores || {};
    var health = healthScores[signal.ticker];
    var healthHtml = '';
    var fragileHtml = '';
    var warningHtml = '';
    if (health && health.health !== undefined) {
      var healthPct = health.health;
      var healthLabel = health.label || 'caution';
      var healthLabelDisplay = healthLabel.toUpperCase();
      var healthColors = { robust: 'var(--accent)', caution: 'var(--warning)', fragile: 'var(--danger)', terminal: '#666' };
      var hc = healthColors[healthLabel] || 'var(--text-muted)';
      healthHtml =
        '<div class="health-bar-row">' +
          '<span class="health-label-badge ' + healthLabel + '">' + healthLabelDisplay + '</span>' +
          '<div class="health-bar-track"><div class="health-bar-fill ' + healthLabel + '" style="width:' + healthPct + '%;background:' + hc + '"></div></div>' +
          '<span class="health-pct ' + healthLabel + '">' + healthPct + '</span>' +
        '</div>';

      // Fragile signal badge: CONFIRMED but health < 40 — O(1) lookup
      var signalState = (signalStates && signalStates.by_ticker && signalStates.by_ticker[signal.ticker])
        ? signalStates.by_ticker[signal.ticker].state : '';
      if (healthLabel === 'fragile' || healthLabel === 'terminal') {
        if (signalState === 'confirmed' || signalState === 'active') {
          fragileHtml = '<span class="fragile-badge">⚠️ FRAGILE — Avoid Entry</span>';
        }
      }

      // Warnings — categorized by Health Score factor
      var warnings = health.warnings || [];
      if (warnings.length > 0) {
        var warnItems = [];
        warnings.forEach(function(w) {
          if (w === 'net_score_fading') warnItems.push({cat: 'Net Score', label: 'Fading', cls: 'warn-momentum'});
          else if (w.indexOf('strategies_dropped') >= 0) warnItems.push({cat: 'Strategies', label: w.split('_')[0] + ' dropped', cls: 'warn-retention'});
          else if (w === 'confidence_scattered') warnItems.push({cat: 'Tightness', label: 'Scattered', cls: 'warn-tightness'});
          else if (w === 'single_family_dominant') warnItems.push({cat: 'Concentration', label: '1-family', cls: 'warn-diversity'});
          else if (w === 'near_threshold') warnItems.push({cat: 'Margin', label: 'Near neutral', cls: 'warn-margin'});
          else if (w === 'key_families_exiting') warnItems.push({cat: 'Concentration', label: 'Key exit', cls: 'warn-diversity'});
          else if (w.indexOf('stale_confirmation') >= 0) warnItems.push({cat: 'Age', label: 'Stale ' + w.split('_').pop() + 'cyc', cls: 'warn-staleness'});
          else warnItems.push({cat: 'Health', label: w.replace(/_/g, ' '), cls: ''});
        });
        warningHtml = '<div class="health-warnings">' + warnItems.map(function(item) {
          return '<span class="warn-chip ' + item.cls + '"><span class="warn-chip-cat">' + item.cat + ':</span>' + item.label + '</span>';
        }).join('') + '</div>';
      }
    }

  // ── Final Verdict (synthesizes all indicators) ──
  var verdict = signal.verdict || 'NO ACTION';
  var verdictClass = 'verdict-badge ' + verdict.toLowerCase().replace(/\s+/g, '-');
  var verdictHtml = '<div class="verdict-row"><span class="' + verdictClass + '">' + verdict + '</span></div>';

  // ── Assemble card HTML ──
  card.innerHTML =
    verdictHtml +
    '<div class="row1">' +
      '<div><span class="ticker-name">' + signal.ticker + '</span>' +
        '<span class="instrument-badge">' + (signal.instrument_type || '') + '</span>' +
        signalStateHtml + '</div>' +
      '<span class="direction-badge ' + dirClass + '">' + dirArrow + ' ' + signal.direction.toUpperCase() + '</span>' +
    '</div>' +
    convictionHtml +
    healthHtml +
    fragileHtml +
    warningHtml +
    '<div class="row2">' +
      '<span>Confidence: <strong>' + (signal.confidence * 100).toFixed(1) + '%</strong></span>' +
      '<span>Score: <strong>' + ((signal.composite_score || 0) * 100).toFixed(1) + '%</strong></span>' +
      '<span>Strategies: <strong>' + (signal.agreeing_count || 0) + '/' + (signal.strategy_count || 0) + '</strong></span>' +
      '<span>Regime: <span class="regime-badge ' + (signal.regime || 'unknown') + '">' + (signal.regime || '?').replace(/_/g, ' ') + '</span></span>' +
    '</div>' +
    levelsHtml + miniDashHtml + tpBannerHtml + flipRow;

  // ── Click to popup ──
  card.onclick = function() { showSignalPopup(signal, cycle); };
  return card;
}

/* ═══════════════════════════════════════════════════════════════
   History Rendering
   ═══════════════════════════════════════════════════════════════ */

function renderHistory() {
  var container = document.getElementById('history-list');
  if (!container) return;

  // STATE: Loading
  var history = _status.history || [];
  if (!history || history.length === 0) {
    container.innerHTML = '<div class="empty-state">📜 No cycle history yet. Run a cycle first.</div>';
    return;
  }

  // STATE: Data
  var sub = _currentSubTab || 'stock';
  container.innerHTML = '';
  history.forEach(function(cycle) {
    var cycleDiv = document.createElement('div');
    cycleDiv.className = 'history-cycle';

    var ts = cycle.timestamp || '';
    var timeStr = ts ? new Date(ts).toLocaleTimeString() : '--';
    var signals = cycle.signals || {};
    var typeSignals = signals[sub] || [];
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
      cycleDiv.querySelector('.history-body').innerHTML = '<em style="color:var(--text-muted)">No ' + sub + ' signals this cycle</em>';
    } else {
      var bodyHtml = '';
      typeSignals.forEach(function(s) {
        var dirArrow = s.direction === 'long' ? '▲' : s.direction === 'short' ? '▼' : '–';
        bodyHtml +=
          '<div class="history-signal">' +
            '<span class="direction-badge ' + s.direction + '" style="padding:1px 6px;font-size:10px">' + dirArrow + '</span>' +
            '<strong>' + s.ticker + '</strong>' +
            '<span>' + (s.confidence * 100).toFixed(1) + '%</span>' +
            '<span style="color:var(--text-muted)">' + (s.regime || '') + '</span>' +
          '</div>';
      });
      cycleDiv.querySelector('.history-body').innerHTML = bodyHtml;
    }

    cycleDiv.querySelector('.history-body').innerHTML += '</div>';
    container.appendChild(cycleDiv);
  });
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

  // Strategies
  var strategies = signal.strategies || [];
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
  alert('Settings saved (refresh may be required for interval change)');
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
      card.className = 'signal-card';
      var dirClass = s.direction === 'long' ? 'long' : s.direction === 'short' ? 'short' : 'neutral';
      card.innerHTML =
        '<div class="row1">' +
          '<span class="ticker-name">' + s.ticker + '</span>' +
          '<span class="direction-badge ' + dirClass + '">' + s.direction.toUpperCase() + '</span>' +
        '</div>' +
        '<div class="row2">' +
          '<span>Confidence: <strong>' + (s.confidence * 100).toFixed(1) + '%</strong></span>' +
          '<span>Score: <strong>' + (s.composite_score ? (s.composite_score * 100).toFixed(1) : '--') + '%</strong></span>' +
          '<span>Strategies: <strong>' + (s.agreeing_count || 0) + '/' + (s.strategy_count || 0) + '</strong></span>' +
          '<span>Regime: <strong>' + (s.regime || '?') + '</strong></span>' +
        '</div>' +
        '<div class="row3" style="color:var(--text-muted)">' +
          '<span>Gate: ' + (s.gate_passed ? '✅' : '❌ ' + (s.gate_reason || '')) + '</span>' +
          '<span>Price: $' + (s.current_price || 0).toFixed(2) + '</span>' +
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

  html += '<div class="cheatsheet-footer">';
  html += '<p>💡 <strong>Tip:</strong> This cheat sheet is updated whenever new features are added. If something is missing, check the <code>renderCheatSheet()</code> function in <code>static/app.js</code>.</p>';
  html += '</div>';

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
