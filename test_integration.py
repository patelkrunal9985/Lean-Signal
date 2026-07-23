"""Multi-cycle integration test for signal persistence pipeline.
Simulates the full lifecycle: building -> thesis -> maturation -> exit.
Verifies signals are generated at correct net_score thresholds."""

import sys, logging, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "engine"))
sys.path.insert(0, os.path.dirname(__file__))

logging.disable(logging.CRITICAL)

from engine.signal_persistence import (
    update, reset, get_ticker_state, get_all_states,
    get_signal_timeline, remove_ticker,
)

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        detail_str = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {name}{detail_str}")

cm = {
    "consensus_families": {"momentum": 0.4, "flow": 0.3, "gamma": 0.3},
    "consensus_family_count": 3,
    "consensus_agreement_cv": 0.25,
    "consensus_threshold": 0.2,
    "consensus_dominant_share": 0.4,
    "consensus_active_votes": 5,
    "consensus_weighted_long": 0.65,
    "consensus_weighted_short": 0.15,
    "consensus_net_score": 0.5,
    "consensus_counter_trend": "no",
    "consensus_conviction_tier": "gold",
}

cm_weak = dict(cm)
cm_weak["consensus_family_count"] = 1
cm_weak["consensus_families"] = {"momentum": 0.5}
cm_weak["consensus_agreement_cv"] = 0.6
cm_weak["consensus_active_votes"] = 2
cm_weak["consensus_conviction_tier"] = "bronze"

def test_signal_generation_thresholds():
    """Verify signals generate at correct net_score thresholds."""
    print("\n=== SIGNAL GENERATION THRESHOLDS ===")
    scenarios = [
        ("low_0.10", 0.10, "none"),    # too low
        ("low_0.15", 0.15, "watching"), # watching only
        ("low_0.20", 0.20, "watching"), # stuck watching
        ("mid_0.25", 0.25, "pending"),  # reaches pending
        ("mid_0.30", 0.30, "pending"),  # reaches pending
        ("high_0.35", 0.35, "active"),  # reaches active
        ("high_0.50", 0.50, "active"), # reaches active
    ]
    for ticker, ns, min_state in scenarios:
        reset()
        for i in range(10):
            update(ticker, "long", min(ns * 1.6, 0.9), ns, cm,
                   cycle_id=i + 1, current_price=5500, instrument_type="future")
        ts = get_ticker_state(ticker)
        states = ["none", "watching", "pending", "active", "confirmed"]
        got_idx = states.index(ts["state"])
        want_idx = states.index(min_state)
        check(f"{ticker}: net_score={ns}, final state={ts['state']} (conv={ts['conviction']:.3f})",
              got_idx >= want_idx,
              f"expected at least {min_state}, got {ts['state']}")

def test_thesis_lifecycle():
    """Full lifecycle: build → thesis → mature → exit."""
    print("\n=== THESIS LIFECYCLE ===")
    reset()
    # Phase 1: Build to thesis
    for i in range(3):
        update("LIFE", "long", 0.7, 0.5, cm, cycle_id=i + 1,
               current_price=5500, instrument_type="future")
    ts = get_ticker_state("LIFE")
    check("thesis entry pending", ts["state"] in ("pending", "active", "confirmed"))
    check("entry_price set", ts["state_entry_price"] == 5500)
    check("has_thesis", ts["has_thesis"] is True)

    # Phase 2: Mature to confirmed
    for i in range(3, 8):
        update("LIFE", "long", 0.7, 0.5, cm, cycle_id=i + 1,
               current_price=5500, instrument_type="future")
    ts = get_ticker_state("LIFE")
    check("thesis reaches active/confirmed", ts["state"] in ("active", "confirmed"))

    # Phase 3: Weakening via direction flip
    update("LIFE", "short", 0.5, -0.3, cm, cycle_id=9,
           current_price=5490, instrument_type="future")
    ts = get_ticker_state("LIFE")
    check("opposition -> weakening or watching", ts["state"] in ("weakening", "watching"),
          f"got {ts['state']} conv={ts['conviction']:.3f}")
    check("conviction < 0.45", ts["conviction"] < 0.45)

    # Phase 4: Force exit via sustained opposition
    for i in range(5):
        update("LIFE", "short", 0.2, -0.2, cm, cycle_id=10 + i,
               current_price=5480, instrument_type="future")
    ts = get_ticker_state("LIFE")
    check("thesis exits eventually", ts["state"] in ("none", "watching", "weakening"),
          f"got {ts['state']}")

def test_force_exit_pnl():
    """Force exit via PnL Guardian drawdown."""
    print("\n=== PNL GUARDIAN FORCE EXIT ===")
    reset()
    for i in range(3):
        update("PNL_EXIT", "long", 0.7, 0.5, cm, cycle_id=i + 1,
               current_price=5500, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_EXIT")
    check("thesis built", ts["state"] in ("active", "confirmed"))
    pre = ts["conviction"]

    # Large drawdown: -40/15 = -2.67 ATR
    update("PNL_EXIT", "long", 0.2, -0.2, cm, cycle_id=4,
           current_price=5460, instrument_type="future", atr=15.0)
    ts = get_ticker_state("PNL_EXIT")
    check("force exit conviction <= 0.05", ts["conviction"] <= 0.05,
          f"got {ts['conviction']:.4f}")
    check("force exit state = none", ts["state"] == "none",
          f"got {ts['state']}")

def test_rebuild_after_exit():
    """Verify clean rebuild after force exit."""
    print("\n=== REBUILD AFTER EXIT ===")
    reset()
    # Build
    for i in range(3):
        update("REBUILD", "long", 0.7, 0.5, cm, cycle_id=i + 1,
               current_price=5500, instrument_type="future", atr=15.0)
    # Force exit
    update("REBUILD", "long", 0.0, -0.3, cm, cycle_id=4,
           current_price=5460, instrument_type="future", atr=15.0)
    ts = get_ticker_state("REBUILD")
    check("exited to none", ts["state"] == "none")

    # Rebuild - should not jump to active immediately
    update("REBUILD", "long", 0.6, 0.4, cm, cycle_id=5,
           current_price=5495, instrument_type="future")
    ts = get_ticker_state("REBUILD")
    check("re-entry conviction modest (<0.35)", ts["conviction"] < 0.35,
          f"got {ts['conviction']:.4f}")
    check("re-entry state is watching/pending", ts["state"] in ("watching", "pending"),
          f"got {ts['state']}")

    # Should reach active by cycle 3
    for i in range(2):
        update("REBUILD", "long", 0.7, 0.5, cm, cycle_id=6 + i,
               current_price=5500, instrument_type="future")
    ts = get_ticker_state("REBUILD")
    check("rebuilt to active/confirmed by cycle 3",
          ts["state"] in ("active", "confirmed"),
          f"got {ts['state']} conv={ts['conviction']:.3f}")

def test_weak_signal_stays_weak():
    """Low net_score should not generate signals even after many cycles."""
    print("\n=== WEAK SIGNAL REJECTION ===")
    reset()
    for i in range(20):
        update("WEAK", "long", 0.3, 0.12, cm_weak, cycle_id=i + 1,
               current_price=5500, instrument_type="future")
    ts = get_ticker_state("WEAK")
    check("net_score=0.12 stays watching or lower after 20 cycles",
          ts["state"] in ("none", "watching"),
          f"got {ts['state']} conv={ts['conviction']:.3f}")

def test_oscillation_dampening():
    """Peak erosion when conviction declines."""
    print("\n=== OSCILLATION DAMPENING ===")
    reset()
    for i in range(3):
        update("OSC", "long", 0.7, 0.5, cm, cycle_id=i + 1,
               current_price=5500, instrument_type="future")
    ts = get_ticker_state("OSC")
    initial_peak = ts["conviction_peak"]
    check("initial peak set", initial_peak > 0)

    # Decline for 5 cycles
    for i in range(5):
        update("OSC", "long", 0.4, 0.15, cm, cycle_id=4 + i,
               current_price=5495, instrument_type="future")
    ts = get_ticker_state("OSC")
    check("peak eroded below initial", ts["conviction_peak"] < initial_peak,
          f"initial={initial_peak:.4f} eroded={ts['conviction_peak']:.4f}")
    check("peak still above current conviction", ts["conviction_peak"] > ts["conviction"],
          f"peak={ts['conviction_peak']:.4f} conv={ts['conviction']:.4f}")

def test_multi_ticker():
    """Multiple tickers maintain independent states."""
    print("\n=== MULTI TICKER ===")
    reset()
    tickers_status = {}
    for i in range(5):
        for tkr, ns in [("AAPL", 0.5), ("MSFT", 0.3), ("GOOG", 0.15), ("SPY", 0.6)]:
            update(tkr, "long", min(ns * 1.6, 0.9), ns, cm,
                   cycle_id=i + 1, current_price=5500, instrument_type="stock")
            ts = get_ticker_state(tkr)
            tickers_status[tkr] = ts["state"]
    check("high conviction ticker confirmed", tickers_status["SPY"] == "confirmed",
          f"got {tickers_status['SPY']}")
    check("medium conviction ticker at least pending",
          tickers_status["AAPL"] in ("pending", "active", "confirmed"),
          f"got {tickers_status['AAPL']}")
    check("low conviction ticker watching or lower",
          tickers_status["GOOG"] in ("none", "watching"),
          f"got {tickers_status['GOOG']}")
    all_states = get_all_states()
    check("get_all_states has by_ticker", "by_ticker" in all_states)
    check("get_all_states has summary", "summary" in all_states)

def test_state_entry_price():
    """Verify entry_price stays after thesis entry, even during weakening rebound."""
    print("\n=== STATE ENTRY PRICE PRESERVATION ===")
    reset()
    for i in range(3):
        update("PRICE", "long", 0.7, 0.5, cm, cycle_id=i + 1,
               current_price=5500, instrument_type="future")
    ts = get_ticker_state("PRICE")
    check("entry_price = 5500", ts["state_entry_price"] == 5500)

    # Weakening via direction flip
    update("PRICE", "short", 0.5, -0.3, cm, cycle_id=4,
           current_price=5490, instrument_type="future")
    ts = get_ticker_state("PRICE")
    check("entry_price preserved in weakening", ts["state_entry_price"] == 5500)

    # Rebound back to long
    update("PRICE", "long", 0.7, 0.5, cm, cycle_id=5,
           current_price=5510, instrument_type="future")
    ts = get_ticker_state("PRICE")
    check("rebound preserves entry_price", ts["state_entry_price"] == 5500,
          f"got {ts['state_entry_price']}")

def test_remove_ticker():
    """Remove a ticker and verify it's gone."""
    print("\n=== REMOVE TICKER ===")
    reset()
    update("REMOVE_ME", "long", 0.7, 0.5, cm, cycle_id=1,
           current_price=5500, instrument_type="future")
    check("ticker exists before remove", get_ticker_state("REMOVE_ME")["state"] != "none")
    remove_ticker("REMOVE_ME")
    ts = get_ticker_state("REMOVE_ME")
    check("ticker reset after remove", ts["state"] == "none")
    check("conviction reset after remove", ts["conviction"] == 0.0)


if __name__ == "__main__":
    print("=" * 60)
    print("SIGNAL PERSISTENCE INTEGRATION TEST")
    print("=" * 60)

    test_signal_generation_thresholds()
    test_thesis_lifecycle()
    test_force_exit_pnl()
    test_rebuild_after_exit()
    test_weak_signal_stays_weak()
    test_oscillation_dampening()
    test_multi_ticker()
    test_state_entry_price()
    test_remove_ticker()

    total = PASS + FAIL
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {PASS}/{total} passed, {FAIL}/{total} failed")
    print(f"{'=' * 60}")

    sys.exit(1 if FAIL > 0 else 0)
