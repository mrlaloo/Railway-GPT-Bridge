# MP13.2.2 duplicate-close accounting fix

Base: MoneyPenny-FXbot @ d7431df0 (MP13.2.2)

Scope: finalize / reconcile accounting only.
No entry, exit, projected-R, stop geometry, pair selection, or deploy changes.

## Bug
`reconcile_live()` refreshed `persisted` once, then:
1. looped `TRADES` and called `finalize_live()` (STATS + note_result + pop)
2. looped the stale persisted snapshot (`closed=False`)
3. `_resolve_absent_trade()` rehydrated the trade and finalized again

`state.record_outcome()` rejected the second persist write. STATS / PAIR_RESULTS did not.

Observed: 3 unique broker closes +9.10p displayed as heartbeat +18.2p.

## Fix
1. Refresh `persisted = state.tracked_trades()` again after the TRADES absent-trade loop.
2. `finalize_live()` books STATS / `note_result()` only if `record_outcome()` writes a new close.
3. Skip resolve/finalize when `_FINALIZED` or persisted `closed=True`.
4. VERSION string MP13.2.3 so logs identify the accounting drop. No strategy change.

## Install
Replace `moneypenny/runtime.py` and `moneypenny/__init__.py`.
Do not mix other files into this drop.
