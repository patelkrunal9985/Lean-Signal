let _currentTab = 'signals';
let _currentSubTab = 'stock';
let _status = {};
let _autoRunActive = false;
let _pollTimer = null;

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
  // Option premium levels for options
  var optPrem = signal.option_entry_premium || 0;
  if (optPrem > 0 && signal.instrument_type === 'option') {
    // Recommended strike
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
    // Position: always 1 contract for options (signal-only system)
    var posLine = '';
    if (signal.instrument_type === 'option' && signal.direction !== 'neutral') {
      posLine = '<span class="level-label">Size</span><span class="level-val">1 contract</span>';
    }
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
  // Mini-dashboard for option signals: key metrics at a glance
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
    '</div>' +
    miniDashHtml +
    levelsHtml;
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

  // Entry/Exit levels
  var entryPrice = signal.entry_price || signal.current_price || 0;
  var sl = signal.stop_loss || 0;
  var tp = signal.take_profit || 0;
  var rr = signal.risk_reward || 0;
  document.getElementById('popup-entry').textContent = '$' + entryPrice.toFixed(2);
  document.getElementById('popup-sl').textContent = '$' + sl.toFixed(2);
  document.getElementById('popup-tp').textContent = '$' + tp.toFixed(2);
  document.getElementById('popup-rr').textContent = rr.toFixed(1) + ':1';

  // Highlight direction on SL
  var slEl = document.getElementById('popup-sl');
  slEl.className = signal.direction === 'long' ? 'level-sl-long' : 'level-sl-short';

  // Option premium levels
  var optSection = document.getElementById('popup-option-levels');
  var optPrem = signal.option_entry_premium || 0;
  if (optPrem > 0 && signal.instrument_type === 'option') {
    optSection.style.display = 'block';
    document.getElementById('popup-opt-entry').textContent = '$' + optPrem.toFixed(2);
    document.getElementById('popup-opt-sl').textContent = '$' + (signal.option_sl_premium || 0).toFixed(2);
    document.getElementById('popup-opt-tp').textContent = '$' + (signal.option_tp_premium || 0).toFixed(2);
    document.getElementById('popup-premium-rr').textContent = ((signal.premium_risk_reward || 0)).toFixed(1) + ':1';
  } else {
    optSection.style.display = 'none';
  }

  // Strike recommendation
  var strikeSection = document.getElementById('popup-strike-recommendation');
  var recStrike = signal.recommended_strike || 0;
  if (recStrike > 0 && signal.instrument_type === 'option') {
    strikeSection.style.display = 'block';
    document.getElementById('popup-strike').textContent = '$' + recStrike.toFixed(0) + ' ' + (signal.recommended_otm || '');
    document.getElementById('popup-opt-type').textContent = signal.recommended_option_type || '';
    document.getElementById('popup-est-win').textContent = (signal.estimated_win_rate ? (signal.estimated_win_rate * 100).toFixed(0) + '%' : '--');
    document.getElementById('popup-est-payoff').textContent = (signal.estimated_payoff || 0).toFixed(1) + ':1';
    document.getElementById('popup-strike-rationale').textContent = signal.strike_rationale || '';
  } else {
    strikeSection.style.display = 'none';
  }

  // Position: always 1 contract for options
  var posSection = document.getElementById('popup-position-sizing');
  if (signal.instrument_type === 'option' && signal.direction !== 'neutral') {
    posSection.style.display = 'block';
    document.getElementById('popup-contracts').textContent = '1';
    document.getElementById('popup-total-prem').textContent = '$' + ((signal.option_entry_premium || 0)).toFixed(2);
  } else {
    posSection.style.display = 'none';
  }

  // Time window & VWAP
  var metaSection = document.getElementById('popup-session-meta');
  var todLabel = signal.time_window || '';
  var vwapLabel = signal.vwap_position || '';
  if (todLabel || vwapLabel) {
    metaSection.style.display = 'flex';
    document.getElementById('popup-time-window').textContent = todLabel ? todLabel.replace(/_/g, ' ') : '--';
    document.getElementById('popup-vwap-pos').textContent = vwapLabel || '--';
  } else {
    metaSection.style.display = 'none';
  }

  // ── Market Dashboard (option signals only, collapsed by default) ──
  var dashSection = document.getElementById('popup-market-dashboard');
  var dash = signal.market_dashboard || {};
  if (Object.keys(dash).length > 0 && signal.instrument_type === 'option') {
    dashSection.style.display = 'block';
    // Reset to collapsed state
    var dashBody = document.getElementById('dashboard-body');
    dashBody.style.display = 'none';
    var dashIcon = dashSection.querySelector('.collapse-icon');
    if (dashIcon) dashIcon.textContent = '▼';
    var dashGrid = document.getElementById('dashboard-grid');
    var dashItems = [
      ['Underlying', '$' + (dash.underlying_price || 0).toFixed(2)],
      ['ATM IV', (dash.iv || 0).toFixed(1) + '%'],
      ['HV(10d)', (dash.hv_10 || 0).toFixed(1) + '%'],
      ['DTE', dash.dte],
      ['P/C Ratio', dash.pc_ratio],
      ['P/C 5d Avg', dash.pc_ratio_5d],
      ['Gamma Flip', '$' + (dash.gamma_flip || 0).toFixed(0)],
      ['γ Walls', dash.gamma_walls_count],
      ['Delta Pos', formatBigNum(dash.delta_positioning)],
      ['Straddle', '$' + (dash.atm_straddle || 0).toFixed(2)],
      ['Skew(1m)', (dash.skew_1m || 0).toFixed(1) + ' pts'],
      ['Charm', dash.charm_direction + ' ' + formatTinyNum(dash.charm_magnitude)],
      ['Vanna', formatBigNum(dash.total_vanna)],
      ['VIX Spot', dash.vix_spot],
      ['Breadth', dash.breadth_state + ' (' + (dash.breadth_composite > 0 ? '+' : '') + dash.breadth_composite.toFixed(2) + ')'],
      ['Breadth Trend', dash.breadth_trend],
      ['VIX State', dash.vix_state],
      ['Futures Align', dash.futures_alignment + '%'],
      ['Tech Div', (dash.tech_divergence > 0 ? '+' : '') + (dash.tech_divergence * 100).toFixed(2) + '%'],
      ['Small Cap', dash.small_cap_participating ? '✓ Participating' : '✗ Lagging'],
      ['Thrust', dash.breadth_thrust ? '⚡ ACTIVE' : '—'],
    ];
    dashGrid.innerHTML = dashItems.map(function(p) {
      return '<div class="dash-metric"><label>' + p[0] + '</label><span>' + p[1] + '</span></div>';
    }).join('');
    // Inline summary: always visible in the header (even when collapsed)
    var dashSummary = document.getElementById('dashboard-summary');
    if (dashSummary) {
      var breadthLabel = dash.breadth_state || '—';
      dashSummary.textContent =
        'IV ' + (dash.iv || 0).toFixed(1) + '% · ' +
        'P/C ' + (dash.pc_ratio != null ? dash.pc_ratio : '—') + ' · ' +
        'VIX ' + (dash.vix_spot != null ? dash.vix_spot : '—') + ' · ' +
        'Breadth ' + breadthLabel + ' · ' +
        'DTE ' + (dash.dte != null ? dash.dte : '—');
    }
  } else {
    dashSection.style.display = 'none';
  }

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
      var reasoning = s.reasoning ? '<div class="strat-reasoning">' + s.reasoning + '</div>' : '';

      // Build diagnostics section
      var diag = s.diagnostics || {};
      var diagHtml = '';
      if (Object.keys(diag).length > 0) {
        var diagPairs = [];
        for (var k in diag) {
          if (diag.hasOwnProperty(k)) {
            var v = diag[k];
            var displayVal = typeof v === 'number' ? (Math.abs(v) < 0.001 ? formatTinyNum(v) : (Math.abs(v) > 1000 ? formatBigNum(v) : (Number.isInteger(v) ? v : v.toFixed(4)))) : v;
            diagPairs.push('<span class="diag-kv"><em>' + k.replace(/_/g, ' ') + '</em>: ' + displayVal + '</span>');
          }
        }
        diagHtml = '<div class="strat-diag">' + diagPairs.join(' · ') + '</div>';
      }

      item.innerHTML =
        '<div><span class="strat-name">' + s.name + '</span><span class="strat-source">' + (s.source || '') + '</span>' + reasoning + diagHtml + '</div>' +
        '<div class="strat-dir-conf">' +
          '<span class="direction-badge ' + sDirClass + '" style="font-size:10px">' + s.direction.toUpperCase() + '</span>' +
          '<span style="font-weight:600">' + (s.confidence * 100).toFixed(1) + '%</span>' +
        '</div>';
      stratContainer.appendChild(item);
    });
  }

  // ── Consensus Meta (collapsed by default) ──
  var consSection = document.getElementById('popup-consensus-meta');
  var consMeta = signal.consensus_meta || {};
  var consVotes = consMeta.consensus_votes || [];
  if (consVotes.length > 0 || consMeta.consensus_net_score != null) {
    consSection.style.display = 'block';
    // Reset to collapsed state
    var consBody = document.getElementById('consensus-body');
    consBody.style.display = 'none';
    var consIcon = consSection.querySelector('.collapse-icon');
    if (consIcon) consIcon.textContent = '▼';
    // Aggregate grid
    var consGrid = document.getElementById('consensus-grid');
    var consItems = [
      ['Net Score', (consMeta.consensus_net_score || 0).toFixed(2)],
      ['Active Votes', consMeta.consensus_active_votes || 0],
      ['Neutral Votes', consMeta.consensus_neutral_votes || 0],
      ['Weighted Long', (consMeta.consensus_weighted_long || 0).toFixed(2)],
      ['Weighted Short', (consMeta.consensus_weighted_short || 0).toFixed(2)],
      ['Total Weight', (consMeta.consensus_total_weight || 0).toFixed(2)],
      ['Counter Trend', consMeta.consensus_counter_trend || 'no'],
      ['Regime Boost', ((consMeta.consensus_regime_boost || 0) > 0 ? '+' + consMeta.consensus_regime_boost.toFixed(2) : '0')],
      ['TOD Window', consMeta.consensus_tod_window || '—'],
      ['Consensus Action', consMeta.consensus_action || '—'],
    ];
    consGrid.innerHTML = consItems.map(function(p) {
      return '<div class="dash-metric"><label>' + p[0] + '</label><span>' + p[1] + '</span></div>';
    }).join('');
    // Inline summary: always visible in the header
    var consSummary = document.getElementById('consensus-summary');
    if (consSummary) {
      var netScore = (consMeta.consensus_net_score != null ? consMeta.consensus_net_score : 0).toFixed(2);
      var activeVotes = consMeta.consensus_active_votes || 0;
      var counterTrend = consMeta.consensus_counter_trend || 'no';
      var action = consMeta.consensus_action || '—';
      consSummary.textContent =
        'Net ' + (netScore > 0 ? '+' : '') + netScore + ' · ' +
        activeVotes + ' active · ' +
        'Counter: ' + counterTrend + ' · ' +
        'Action: ' + action;
    }
    // Vote breakdown list
    var voteSection = document.getElementById('vote-breakdown');
    if (consVotes.length > 0) {
      consVotes.sort(function(a, b) { return b.contribution - a.contribution; });
      var voteHtml = '<h5>Vote Breakdown</h5><div class="vote-list">';
      consVotes.forEach(function(v) {
        var vDirClass = v.direction === 'long' ? 'long' : v.direction === 'short' ? 'short' : 'neutral';
        voteHtml +=
          '<div class="vote-item">' +
            '<span class="vote-name">' + v.name + ' <em>' + (v.type || '') + '</em></span>' +
            '<span class="direction-badge ' + vDirClass + '" style="font-size:9px">' + v.direction.toUpperCase() + '</span>' +
            '<span class="vote-conf">' + ((v.confidence || 0) * 100).toFixed(0) + '%</span>' +
            '<span class="vote-weight">×' + (v.weight || 0).toFixed(2) + '</span>' +
            '<span class="vote-contrib">=' + (v.contribution || 0).toFixed(3) + '</span>' +
          '</div>';
      });
      voteHtml += '</div>';
      voteSection.innerHTML = voteHtml;
      voteSection.style.display = 'block';
    } else {
      voteSection.style.display = 'none';
    }
  } else {
    consSection.style.display = 'none';
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
