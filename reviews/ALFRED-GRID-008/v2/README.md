# ALFRED-GRID-008 shadow review v2

Paper/shadow-only reference implementation. It does not connect to Alpaca and cannot place live orders.

Changes after Grok cross-review:

1. **Proximity cap** — a live BUY must be within `1.5 × step_pct` of current mid. At 70 bps this is 1.05%, so the old CRV bids 3.8–5.2% below spot are not posted.
2. **Dual-confirm capital escape** — release requires the same challenger to win two consecutive reviews, plus both a 1.25× score ratio and an absolute score edge of 0.20.
3. **Stable-origin re-arm** — origin never chases price. A completed rung is re-armed only while the lane is still kept and price is within 3 grid steps of origin; otherwise it is parked. Parked rungs can re-arm only after price returns to the origin window.
4. **Persistent identity/state** — rung cycle, state, BUY/SELL order IDs, fills and realized P&L serialize through `to_state()` / `from_state()`. `min_round_trips_before_protected` now drives productive-history classification.
5. **Injected fees** — `FeeSchedule` is required by `ShadowLane`; the engine contains no hardcoded 15-bps fee assumption.

Additional invariant retained: a BUY and the paired SELL cannot both fill in the same market event.

Validation result: **12/12 tests pass** locally.

CRV fixture at `0.34610` produces **0 live buys**. The capital lease produces `RELEASE / stale_better_challenger_confirmed` only after the second confirming review.

ZIP SHA-256: `fd98bb96e7f2f971956cdd8ff06285cea67546404261338551ad6d2fd5dee460`

This package is for review and shadow validation only. Do not live deploy from this folder.
