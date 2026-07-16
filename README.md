# Lean Signals

Signal-Only Trading Intelligence — an ensemble-based trading signal engine for stocks, futures, and index options (SPX, SPY, QQQ, NDX). Connects to Interactive Brokers (IBKR) for real-time data.

---

## Architecture

```
IBKR TWS/Gateway
    │
    ├─ Live prices, OHLCV, market depth ──► ticker_data_map
    ├─ Option chains (full greeks) ──────► option_metrics.py
    │                                         │
    │    ┌────────────────────────────────────┘
    │    ▼
    │  option context dict (40+ fields: IV, HV, PCR, GEX, gamma_flip,
    │     delta_pos, vanna, charm, skew, straddle, VIX, chain snapshots)
    │    │
    │    ├─► 30 V3 option strategies ──► v3_results_raw (with diagnostics)
    │    ├─► V2 strategies (stocks/futures only)
    │    ├─► Market breadth computation (ES/NQ/YM/RTY + VIX alignment)
    │    ├─► Regime detection (trending/ranging/volatile)
    │    │
    │    ▼
    │  Consensus Coordinator (regime-weighted ensemble)
    │    │
    │    ▼
    │  3-Layer Signal Quality Gate
    │    │  L1: Integrity (data freshness, spreads, volume)
    │    │  L2: Alignment (regime, trend, COT)
    │    │  L3: Conviction (strategy agreement, confidence threshold)
    │    │
    │    ▼
    │  Signals → Strike Selection → Entry/Exit Levels
    │    │
    │    ▼
    └─► Dashboard (Flask + vanilla JS)
         ├─ Signal cards with mini-metrics
         └─ Popup: Market Dashboard + per-strategy diagnostics + consensus votes
```

---

## Option Strategies (30 total)

### Volatility (13)
| # | Strategy | Description |
|---|---|---|
| 1 | `gamma_exposure` | Dealer GEX positioning and gamma imbalance |
| 2 | `iv_skew` | Put/call IV skew (slope) |
| 3 | `iv_rv_spread` | Implied vs realized volatility spread |
| 4 | `theta_decay` | Theta harvesting and time decay analysis |
| 5 | `gamma_flip_levels` | Distance to zero-gamma strike |
| 6 | `gamma_flip_acceleration` | Velocity toward gamma flip level |
| 7 | `skew_term_structure` | IV skew across expiration tenors |
| 8 | `earnings_vol_arbitrage` | Pre/post-earnings vol spreads |
| 9 | `zero_dte_gamma` | 0DTE gamma scalping on expiry day |
| 10 | `vix_spx_convexity` | VIX/SPX convexity arbitrage signals |
| 11 | `vanna_charm_flow` | Dealer hedging prediction from Vanna/Charm |
| 12 | `vol_smile_curvature` 🆕 | IV smile shape — butterfly spreads, wing premium |
| 13 | `delta_gamma_imbalance` 🆕 | Crash-up/flash-crash feedback loop detection |

### Flow (11)
| # | Strategy | Description |
|---|---|---|
| 14 | `put_call_divergence` | P/C volume ratio divergence from 5d avg |
| 15 | `large_option_flow` | Large block option trade detection |
| 16 | `option_volume_flow` | Volume delta between chain snapshots |
| 17 | `opening_drive` | Gap fill vs option flow direction after open |
| 18 | `unusual_whale_flow` | Abnormal large premium flow detection |
| 19 | `strike_volume_surge` | Volume concentration at specific strikes |
| 20 | `vwap_option_flow` | VWAP + option flow confluence |
| 21 | `sector_etf_option_rotation` | Sector ETF option flow rotation |
| 22 | `oi_change_rate` 🆕 | Smart vs noise OI building — proximity-weighted |
| 23 | `breadth_confirmation` 🆕 | Market breadth divergences for index options |

### Trend (2)
| # | Strategy | Description |
|---|---|---|
| 24 | `oi_concentration` | OI clustering at key strikes |
| 25 | `call_put_wall_breakout` | Price breaking through gamma/OI walls |

### Reversion (5)
| # | Strategy | Description |
|---|---|---|
| 26 | `expected_vs_actual` | Expected move vs actual price deviation |
| 27 | `max_pain` | Max pain theory — pin-to-strike magnetism |
| 28 | `delta_positioning` | Extreme dealer delta → mean reversion |
| 29 | `delta_hedging_imbalance` | Dealer hedging pressure → reversion |
| 30 | `prior_hl_magnetism` 🆕 | Prior day/week high-low with option gamma/OI confirmation |

🆕 = Added this session

---

## Market Breadth Pipeline

Computes breadth proxies from existing data (zero new IBKR subscriptions):

| Metric | Source | What It Measures |
|---|---|---|
| **Futures Alignment** | ES, NQ, YM, RTY | % of index futures in same direction |
| **Tech Divergence** | QQQ/NQ vs SPY/ES | Tech vs broad market performance spread |
| **Small-Cap Participation** | RTY vs large caps | Whether small caps confirm or lag |
| **VIX Confirmation** | VX=F vs SPX/ES | VIX moving inverse to market (confirmation) or same direction (divergence) |
| **Breadth Thrust** | All futures + VIX | Rare synchronized surge — highest conviction |
| **Composite Score** | All above | -1.0 to +1.0 aggregated breadth score |

File: `engine/market_breadth.py`

---

## Consensus Coordinator

Regime-weighted ensemble voting across all strategies:

| Regime | trend | reversion | flow | volatility |
|---|---|---|---|---|
| strong_uptrend | 1.20 | 0.15 | 0.50 | 0.65 |
| uptrend | 1.10 | 0.25 | 0.60 | 0.60 |
| ranging | 0.65 | 0.80 | 0.80 | 0.70 |
| downtrend | 1.10 | 0.25 | 0.60 | 0.60 |
| strong_downtrend | 1.20 | 0.15 | 0.50 | 0.65 |

Counter-trend penalty: ×0.25. Options require 3+ strategies agreeing with 0.20+ min confidence.

---

## Time-of-Day System

5 intraday windows with per-strategy weight multipliers:

| Window | Time (ET) | Dominant Strategies |
|---|---|---|
| opening_drive | 9:30–10:10 | opening_drive, prior_hl_magnetism, breadth_confirmation |
| morning_session | 10:10–11:30 | gamma_exposure, oi_change_rate, delta_positioning |
| midday_lull | 11:30–2:00 | max_pain, oi_concentration, breadth_confirmation |
| power_hour | 2:00–3:30 | zero_dte_gamma, delta_gamma_imbalance, vanna_charm_flow |
| closing_pin | 3:30–4:00 | vanna_charm_flow, max_pain, delta_gamma_imbalance |

All 5 new strategies have TOD weights in `engine/time_of_day.py`.

---

## Dashboard & UI Features

### Signal Cards
- Direction badge (▲ LONG / ▼ SHORT), confidence %, strategy count, regime
- **Mini-metrics row** (options only): IV%, P/C ratio, VIX, Breadth state (color-coded), DTE
- Entry/exit levels, option premium levels, strike recommendation

### Signal Popup (click any card)
1. **Direction / Confidence / Regime / Price** — summary grid
2. **Session meta** — time window + VWAP position
3. **📊 Market Dashboard** — collapsible, 21 metrics (IV, HV, PCR, gamma_flip, delta_pos, straddle, skew, charm, vanna, VIX, breadth composite, futures alignment, tech divergence, small-cap, thrust)
4. **📍 Entry & Exit Levels** — entry/SL/TP/R:R
5. **💰 Option Premium Levels** — premium entry/SL/target/R:R
6. **🎯 Strike Recommendation** — OTM type, strike price, est. win rate, est. payoff
7. **Strategy Breakdown** — per-strategy diagnostics (GEX, charm, proximity, signal_type, etc.)
8. **📈 Consensus Detail** — collapsible, aggregate grid + per-vote breakdown (name, direction, weight, contribution)
9. **News & Sentiment**

### Collapsible Sections
Both Market Dashboard and Consensus Detail are collapsed by default with inline summary rows showing key metrics at a glance — click to expand for full detail.

---

## Key Files

| File | Purpose |
|---|---|
| `engine/runner.py` | Main cycle runner — data fetching, strategy execution, signal generation |
| `engine/option_metrics.py` | Option chain fetching + aggregate metric computation |
| `engine/market_breadth.py` 🆕 | Breadth computation from existing futures/index data |
| `engine/time_of_day.py` | Intraday window definitions + per-strategy TOD weights |
| `engine/consensus_coordinator.py` | Regime-weighted ensemble voting |
| `engine/v3/gate.py` | 3-layer signal quality gate |
| `engine/v3/registry.py` | Strategy registration (30 option, 15 futures, 7 stock) |
| `engine/v3/options/*.py` | 30 option strategy implementations |
| `server.py` | HTTP server (port 8088) |
| `templates/dashboard.html` | Dashboard UI |
| `static/app.js` | Frontend JavaScript |
| `static/styles.css` | Dashboard styling |

---

## Running

```bash
# Start the server (connects to IBKR TWS/Gateway on 127.0.0.1:4001)
python server.py

# Dashboard at http://localhost:8088

# API endpoints:
#   GET  /api/status        — system status
#   POST /api/run-cycle     — trigger a cycle
#   POST /api/auto-run/start — start auto-run (60s interval)
#   POST /api/auto-run/stop  — stop auto-run
```

Requires IBKR TWS or IB Gateway running locally with API enabled on port 4001.
