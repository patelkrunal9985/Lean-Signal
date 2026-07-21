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
});

function switchTab(name) {
  _currentTab = name;
  document.querySelectorAll('.tab').forEach(function(t) {
    t.classList.toggle('active', t.dataset.tab === name);
  });
  document.querySelectorAll('.tab-content').forEach(function(tc) {
    tc.classList.toggle('active', tc.id === 'tab-' + name);
  });
  if (name === 'watchlist') loadWatchlist();
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
  var dirArrow = signal.direction === 'long' ? '▲' : signal.direction === 'short' ? '▼' : '–';    // ── Signal state badge (persistence) ──
    var signalStateHtml = '';
    var isWeakening = false;
    if (signalStates && signalStates.by_state) {
      var tickerStates = signalStates.by_state;
      var thisState = null;
      for (var st in tickerStates) {
        var tickersInState = tickerStates[st] || [];
        for (var i = 0; i < tickersInState.length; i++) {
          if (tickersInState[i].ticker === signal.ticker) {
            thisState = tickersInState[i];
            break;
          }
        }
        if (thisState) break;
      }
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

  // ── Flip badge (from persistence engine) ──
  var flipInfo = flips[signal.ticker] || null;
  var flipPotInfo = (!flipInfo) ? (flipPotentials[signal.ticker] || null) : null;
  var flipRow = '';
  if (flipInfo) {
    var score = flipInfo.score || 0;
    var flipClass = score >= 0.8 ? 'flip-badge major' : 'flip-badge';
    flipRow = '<div class="row-flip"><span class="' + flipClass + '">↻ FLIP ' + (flipInfo.from || '').toUpperCase() + ' → ' + (flipInfo.to || '').toUpperCase() + ' score=' + score.toFixed(2) + '</span></div>';
  } else if (flipPotInfo) {
    var needed = flipPotInfo.needs_cycles || 1;
    flipRow = '<div class="row-flip"><span class="flip-badge potential">↻ WATCHING: needs ' + needed + ' more cycle' + (needed > 1 ? 's' : '') + ' to confirm</span></div>';
  }    // ── Build card ──
    // Conviction meter
    var convTier = (signal.consensus_meta && signal.consensus_meta.consensus_conviction_tier) || 'bronze';
    var convPct = signal.confidence * 100;
    var meterWidth = Math.max(convPct, 5);
    var convictionHtml =
      '<div class="conviction-meter">' +
        '<span class="meter-pct ' + convTier + '">' + convPct.toFixed(1) + '%</span>' +
        '<div class="meter-bar ' + convTier + '" style="width:' + meterWidth + '%"></div>' +
      '</div>';

    // TAKE PROFIT banner for weakening state
    var tpBannerHtml = '';
    if (isWeakening) {
      tpBannerHtml = '<div class="take-profit-banner">⚠️ TAKE PROFIT — Signal Weakening</div>';
    }

    card.innerHTML =
      '<div class="row1">' +
        '<div><span class="ticker-name">' + signal.ticker + '</span>' +
        '<span class="instrument-badge">' + signal.instrument_type + '</span>' +
        signalStateHtml + '</div>' +
        '<span class="direction-badge ' + dirClass + '">' + dirArrow + ' ' + signal.direction.toUpperCase() + '</span>' +
      '</div>' +
      '<div class="row2">' +
        convictionHtml +
        '<span>Score: <strong>' + (signal.composite_score * 100).toFixed(1) + '%</strong></span>' +
        '<span>Strategies: <strong>' + (signal.agreeing_count || 0) + '/' + (signal.strategy_count || 0) + '</strong></span>' +
        '<span>Regime: <strong>' + (signal.regime || '?') + '</strong></span>' +
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

  // State duration
  var popupState = document.getElementById('popup-state-duration');
  if (popupState) { popupState.style.display = 'none'; popupState.textContent = ''; }
  var foundState = false;
  if (popupState && signalStates && signalStates.by_state) {
    var tickerStates = signalStates.by_state;
    for (var st in tickerStates) {
      var tickersInState = tickerStates[st] || [];
      for (var i = 0; i < tickersInState.length; i++) {
        if (tickersInState[i].ticker === signal.ticker && tickersInState[i].state && tickersInState[i].state !== 'none' && tickersInState[i].state !== 'watching') {
          var dur = stateAge(tickersInState[i].state_since);
          popupState.textContent = tickersInState[i].state.toUpperCase() + ' x' + (tickersInState[i].consecutive_same || 1) + (dur ? ' for ' + dur : '');
          popupState.className = 'state-badge ' + tickersInState[i].state;
          popupState.style.display = 'inline-block';
          foundState = true;
          break;
        }
      }
      if (foundState) break;
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
