Before making any commit, run all tests and ensure they pass. After a successful commit and passing tests, always push to `origin`.

# Golden Rule: Always Push to Git
After every meaningful set of changes, stage, commit with a descriptive message, and push to origin. Never leave uncommitted work sitting locally.

# Session Summary (July 16, 2026)

## 5 New Option Strategies Added (30 total)
- `prior_hl_magnetism` — Prior day/week high-low + option gamma/OI (calls at resistance, puts at support)
- `oi_change_rate` — Smart vs noise OI building with proximity-to-ATM weighting
- `vol_smile_curvature` — IV smile shape / butterfly spreads / wing premium detection
- `delta_gamma_imbalance` — Crash-up/flash-crash dealer feedback loop detection
- `breadth_confirmation` — Market breadth divergences for index options

## New Infrastructure
- `engine/market_breadth.py` — Breadth computation from ES/NQ/YM/RTY + VIX alignment
- Time-of-day weights for all 5 new strategies in `engine/time_of_day.py`
- Per-strategy diagnostics pipeline: strategy.compute() → v3_results_raw → signal → popup
- Market dashboard dict injected into option signals (21 aggregate metrics)

## UI Features
- Market Dashboard popup (collapsible, 21 metrics)
- Per-strategy diagnostics in strategy breakdown
- Consensus vote breakdown (per-strategy direction, weight, contribution)
- Inline summary rows on collapsed sections
- Mini-metrics row on option signal cards (IV, P/C, VIX, Breadth, DTE)
- Breadth state color-coding + thrust indicator (green/red/⚡)

## Bug Fixes
- prior_hl_magnetism: call/put side filtering for gamma/OI at levels
- oi_change_rate: proximity-to-ATM weighting, avg_proximity fix
- delta_gamma_imbalance: fallback delta scale fix, velocity calc fix
- Falsy value checks (DTE=0, P/C=0) fixed in mini-dashboard
