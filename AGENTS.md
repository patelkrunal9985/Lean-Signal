Before making any commit, run all tests and ensure they pass:
- `python test_strategies.py` (164 tests)
- `python test_integration.py` (36 tests)
- Or `python validate_pipeline.py` (runs both)

After a successful commit and passing tests, always push to `origin`.

# UI Change Rule: Always Verify on localhost:8088
After ANY change to UI files (static/app.js, templates/dashboard.html, static/styles.css), do the following:
1. Fetch http://localhost:8088/static/app.js — verify paren balance (`js.count('(') - js.count(')')`) and brace balance (`js.count('{') - js.count('}')`) are both 0
2. Fetch http://localhost:8088/ — verify the HTML loads and contains expected elements (ticker-grid, ibkr-status, etc.)
3. Run a cycle via POST to /api/run-cycle and verify it completes
4. Fetch http://localhost:8088/api/status — verify connection.connected is true, market_hours.open is correct
5. Open http://localhost:8088 in a browser (or programmatically verify) to confirm no JS console errors
6. If user reported issues, specifically check what the browser serves (not just the file on disk) — clear browser cache before testing

# Golden Rule: Always Push to Git
After every meaningful set of changes, stage, commit with a descriptive message, and push to origin. Never leave uncommitted work sitting locally.

# Golden Rule: Analyze & Plan Before Implementing
For ANY development work (code changes, new features, refactors, bug fixes, etc.):
1. **Analyze** — Gather context, read relevant files, research as needed
2. **Present a plan** — Describe the proposed approach, affected files, and reasoning
3. **Wait for approval** — Do NOT make any code changes until explicitly asked
4. **After approval** — Implement the changes, verify with tests, then commit and push immediately. Never leave uncommitted work sitting locally.

The default mode is analyze and plan — never implement without permission.

# Session Summary (July 24, 2026)

## Round 3 — Quality & Safety Fixes (11 changes across 10 files)

### Bug Fixes (4)
- **ibkr_data_feed.py**: Bid/ask zero-overwrite guard (only update when >0); deterministic client ID offset via `random.Random(self.client_id).randint(0,31)` — prevents stale zero bid/ask overwriting real data.
- **entry_exit.py**: Removed absolute $0.10 stop floor for futures — uses purely relative `entry * FUTURE_SL_FLOOR_PCT`, avoids phantom $0.10 stops on low-priced instruments.
- **signal_persistence.py**: Autosave preserves all `MAX_MEMORY=10` snapshots (was `[-3:]`), fixing data loss on the 11th cycle.
- **strategy_eval.py**: V3 strategy failures logged at WARNING (was DEBUG), so operational issues are visible in production logs.

### Technical Debt (3)
- **regime/detector.py**: Removed dead `REGIME_MULTIPLIERS` dict and `risk_multiplier` from output — unused since v3 migration.
- **utils/settings_manager.py**: Removed 24 orphaned keys (old cycle-counting state machine: `neutral_cooldown_active`, `neutral_cooldown_confirmed`, `neutral_cooldown_max`, `sticky_counter_cycles`, etc.).
- **templates/dashboard.html**: JS cache buster v14→v16.

### Architecture (2)
- **engine/v3/base.py**: Added `WEIGHT_BY_FAMILY` map — differentiates strategy weight per family (options_micro=0.08, flow/mtf=0.06, volume=0.05, technical=0.04, macro=0.03).
- **engine/v3/registry.py**: Applies `s.default_weight = WEIGHT_BY_FAMILY.get(s.family, 0.05)` during strategy initialization, giving stronger families more influence in consensus.

### Test Fixes (1)
- **test_strategies.py**: Updated 3 SettingsManager tests (defaults, set_many, get_all, reset) to use existing keys after orphaned key removal.

### Infrastructure (1)
- Fixed terminal scroll freezing (PowerShell buffer overflow) — screen cleared, scrollback flushed, profile created.

## Cumulative Summary (Rounds 1-3)
- **37 changes across 16 files**: 26 Round 1+2 fixes + 11 Round 3 fixes
- **224 tests all passing** (188 main + 36 integration)
- **All 34 unaddressed deep-audit items resolved**: 4 signal-quality fixes, 30 code-hygiene items
- Key patterns: shared event loop (19,200→~200 loops/day), dead code removal, orphaned key cleanup, relative pricing, data-feed guards, weight differentiation

## Session Summary (July 22, 2026)

## Building Phase Fix — Signal Generation Unblocked
- **Root cause**: `base = net_mag * 0.8 * building_factor` created a hard ceiling at `net_score * 0.8`. Net_scores ≤0.30 maxed out at 0.24 → stuck in "watching" forever. Thesis entry required `≥0.3125` to ever reach "pending". Tickers with common market net_scores (0.15–0.30) never generated signals.
- **Fix**: Removed the `0.8` multiplier → ceiling now = `net_mag`, so `net_score=0.25` reaches pending (was stuck watching). Halved the minimum threshold.
- **Faster ramp**: `building_factor = (cycles_seen + 2) / 4.0` (was `/5.0`). Reaches max factor at 2 cycles (was 3). Empirically tested: net_score=0.25→pending at cycle 3 (was never), net_score=0.35→active at cycle 3 (was cycle 5).
- **Memory cleared on thesis exit**: `_signal_memory[ticker] = []` (was `[-2:]`). Building factor resets properly on re-entry (max conviction ~0.20 vs instant 0.40).

## Critical Bug Fix — `entry_dir` Mutability
- **Root cause**: `entry_dir = _active_direction.get(ticker, "long")` read from mutable `_active_direction` which is updated during weakening/neutral cycles. When short opposition continued, `_active_direction` flipped to "short" → `entry_dir=="short"` matched current direction → opposition penalty (`×0.4`) disappeared → conviction jumped back to 0.608 instead of decaying toward exit.
- **Fix**: Added `_entry_direction` — immutable snapshot captured at thesis entry (stored alongside `_entry_regime`, `_entry_cycle`). Used throughout conviction formula: `_entry_direction.get(ticker, _active_direction.get(ticker, "long"))`.
- **Impact**: Sustained opposition now correctly decays conviction through `×0.4` penalty (0.288 → 0.188 → 0.186 → ...). PnL Guardian negation, edge calculation, and velocity penalty all use the original thesis direction.

## Test Suite
- **164 existing tests**: All pass (9 shifted by new building ramp, 24 PnL/velocity/building tests adjusted)
- **36 new integration tests**: `test_integration.py` — signal generation thresholds by net_score (0.10/0.15/0.20/0.25/0.35/0.50), full thesis lifecycle (build→mature→weaken→exit), PnL Guardian force exit, clean rebuild after exit, weak signal rejection, oscillation dampening, multi-ticker independence, entry_price preservation during weakening rebound, remove_ticker
- **200 total tests all passing**

## Pre-Commit Validation
- `validate_pipeline.py` — runs both test suites, exit code 1 on any failure
- Run before every commit: `python validate_pipeline.py`

## Relevant Files
- `engine/signal_persistence.py`: building factor (0.8→removed, /5→/4), memory clear (`[]` vs `[-2:]`), `_entry_direction` added (immutable entry dir), version bump 2→3, save/load/clear for new field
- `test_strategies.py`: 9 test expectations adjusted for faster building ramp (cycles shifted by 1)
- `test_integration.py`: 36 new tests across 9 scenarios
- `validate_pipeline.py`: pre-commit validation runner
