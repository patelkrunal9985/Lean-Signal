let _currentTab = 'signals';
let _currentSubTab = 'stock';
let _status = {};
let _autoRunActive = false;
let _pollTimer = null;

document.addEventListener('DOMContentLoaded', function() {
  loadStatus();
  _pollTimer = setInterval(loadStatus, 5000);
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
}

async function loadStatus() {
  try {
    var resp = await fetch('/api/status');
    _status = await resp.json();
    updateUI();
  } catch(e) {
    console.error('Status fetch failed:', e);
  }
}

function updateUI() {
  var conn = _status.connection || {};
  var ibkrEl = document.getElementById('ibkr-status');
  if (conn.connected) {
    ibkrEl.textContent = 'IBKR Connected';
    ibkrEl.className = 'status-badge connected';
  } else {
    ibkrEl.textContent = 'IBKR Disconnected';
    ibkrEl.className = 'status-badge disconnected';
  }

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

  if (_status.auto_run) {
    document.getElementById('auto-run-btn').textContent = 'Stop';
  } else {
    document.getElementById('auto-run-btn').textContent = 'Auto-Run';
  }

  var last = _status.last_cycle;
  if (last) {
    document.getElementById('total-scanned').textContent = 'Scanned: ' + (last.tickers_scanned || 0);
    document.getElementById('total-signals').textContent = 'Signals: ' + (last.signals_count || 0);
    var elapsed = last.elapsed_seconds || 0;
    document.getElementById('cycle-elapsed').textContent = 'Last cycle: ' + elapsed.toFixed(1) + 's';
  }

  if (_currentTab === 'signals' && last && last.status === 'completed') {
    renderSignals(last);
  }
}

function renderSignals(cycle) {
  var groups = cycle.signals || {};
  ['stock', 'future', 'option'].forEach(function(type) {
    var container = document.getElementById('signals-' + type);
    if (!container) return;
    var signals = groups[type] || [];
    if (signals.length === 0) {
      container.innerHTML = '<div class="signal-card" style="opacity:0.6"><em>No signals</em></div>';
      return;
    }
    container.innerHTML = '';
    signals.forEach(function(s) { container.appendChild(createSignalCard(s, cycle)); });
  });
}

function createSignalCard(signal, cycle) {
  var card = document.createElement('div');
  card.className = 'signal-card';
  var dirClass = signal.direction === 'long' ? 'long' : signal.direction === 'short' ? 'short' : 'neutral';
  var dirArrow = signal.direction === 'long' ? '▲' : signal.direction === 'short' ? '▼' : '–';
  card.innerHTML =
    '<div class="row1">' +
      '<div><span class="ticker-name">' + signal.ticker + '</span>' +
      '<span class="instrument-badge">' + signal.instrument_type + '</span></div>' +
      '<span class="direction-badge ' + dirClass + '">' + dirArrow + ' ' + signal.direction.toUpperCase() + '</span>' +
    '</div>' +
    '<div class="row2">' +
      '<span>Confidence: <strong>' + (signal.confidence * 100).toFixed(1) + '%</strong></span>' +
      '<span>Score: <strong>' + (signal.composite_score * 100).toFixed(1) + '%</strong></span>' +
      '<span>Strategies: <strong>' + signal.agreeing_count + '/' + signal.strategy_count + '</strong></span>' +
      '<span>Regime: <strong>' + signal.regime + '</strong></span>' +
      '<span>Price: <strong>$' + (signal.current_price || 0).toFixed(2) + '</strong></span>' +
    '</div>';
  card.addEventListener('click', function() { showSignalPopup(signal, cycle); });
  return card;
}

function showSignalPopup(signal, cycle) {
  var overlay = document.getElementById('signal-popup-overlay');
  var dirClass = signal.direction === 'long' ? 'long' : signal.direction === 'short' ? 'short' : 'neutral';
  document.getElementById('popup-title').textContent = signal.ticker + ' — Signal Detail';
  document.getElementById('popup-direction').textContent = signal.direction.toUpperCase();
  document.getElementById('popup-direction').className = 'direction-badge ' + dirClass;
  document.getElementById('popup-confidence').textContent = (signal.confidence * 100).toFixed(1) + '%';
  document.getElementById('popup-score').textContent = (signal.composite_score * 100).toFixed(1) + '%';
  document.getElementById('popup-regime').textContent = signal.regime + ' (' + (signal.regime_confidence * 100).toFixed(0) + '%)';
  document.getElementById('popup-price').textContent = '$' + (signal.current_price || 0).toFixed(2);

  var stratContainer = document.getElementById('popup-strategies');
  var strats = signal.strategies || [];
  if (strats.length === 0) {
    stratContainer.innerHTML = '<div style="color:var(--text-muted)">No strategies fired</div>';
  } else {
    stratContainer.innerHTML = '';
    strats.sort(function(a, b) { return b.confidence - a.confidence; });
    strats.forEach(function(s) {
      var item = document.createElement('div');
      item.className = 'strategy-item';
      var sDirClass = s.direction === 'long' ? 'long' : s.direction === 'short' ? 'short' : 'neutral';
      var reasoning = s.reasoning ? '<div style="font-size:11px;color:var(--text-muted);margin-top:2px">' + s.reasoning + '</div>' : '';
      item.innerHTML =
        '<div><span class="strat-name">' + s.name + '</span><span class="strat-source">' + (s.source || '') + '</span>' + reasoning + '</div>' +
        '<div class="strat-dir-conf">' +
          '<span class="direction-badge ' + sDirClass + '" style="font-size:10px">' + s.direction.toUpperCase() + '</span>' +
          '<span style="font-weight:600">' + (s.confidence * 100).toFixed(1) + '%</span>' +
        '</div>';
      stratContainer.appendChild(item);
    });
  }

  var newsContainer = document.getElementById('popup-news');
  var news = signal.news || [];
  if (news.length === 0) {
    newsContainer.innerHTML = '<div style="color:var(--text-muted)">No recent news</div>';
  } else {
    newsContainer.innerHTML = '';
    news.slice(0, 5).forEach(function(n) {
      var item = document.createElement('div');
      item.className = 'news-item';
      item.innerHTML =
        '<div class="headline" onclick="window.open(\'' + (n.url || '#') + '\',\'_blank\')">' + n.headline + '</div>' +
        '<div class="source">' + (n.source || '') + '</div>';
      newsContainer.appendChild(item);
    });
  }

  overlay.style.display = 'flex';
}

function closePopup(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById('signal-popup-overlay').style.display = 'none';
}

async function runCycle() {
  var btn = document.getElementById('run-cycle-btn');
  btn.disabled = true;
  btn.textContent = 'Running...';
  try {
    var resp = await fetch('/api/run-cycle', { method: 'POST' });
    var result = await resp.json();
    _status.last_cycle = result;
    _status.cycle_in_progress = false;
    updateUI();
  } catch(e) {
    console.error('Run cycle failed:', e);
  }
  btn.disabled = false;
  btn.textContent = 'Run Cycle';
}

async function toggleAutoRun() {
  if (_status.auto_run) {
    await fetch('/api/auto-run/stop');
    _status.auto_run = false;
  } else {
    await fetch('/api/auto-run/start');
    _status.auto_run = true;
  }
  updateUI();
}

function loadWatchlist() {
  var status = _status;
  var slots = status.slot_usage || {};
  var slotContainer = document.getElementById('slot-usage');
  var limits = slots.limits || {};
  var byType = slots.by_type || {};
  var html = '<div class="slot-card"><h3>Total</h3><div class="used">' + (slots.total_fixed || 0) + '/' + (slots.total_limit || 100) + '</div><div class="limit">fixed</div></div>';
  ['stock', 'future', 'option'].forEach(function(t) {
    var used = byType[t] || 0;
    var limit = limits[t] || 0;
    html += '<div class="slot-card"><h3>' + t.charAt(0).toUpperCase() + t.slice(1) + '</h3><div class="used">' + used + '/' + limit + '</div><div class="limit">slots</div></div>';
  });
  slotContainer.innerHTML = html;
}

function saveSettings() {
  alert('Settings saved (local). Auto-run will use new interval on next cycle.');
}
