# ALFRED-GRID-008 — v4 recorded-bar + fake-broker replay

Shadow/review only. **No live deployment. No broker credentials. No Railway changes.**

This follows Grok's v3 approval and next-step request. It keeps the v3 strategy rules and adds one integration boundary required for a real broker replay: `ShadowLane.apply_broker_fill(...)`. A resting limit can fill even if the bar closes through the limit, so replay cannot safely infer persisted broker fills only from `nearest_live_buys(mid)`.

Replay fixture: sampled 1-minute **recorded CRV/USD Alpaca bars from 2026-09-13**, including the first-cross bars that reproduce the two observed buy fills and two observed grid exits. OHLC values are recorded, not synthetic.

The fake broker:
- submits only planned shadow orders,
- prevents shorts,
- respects available cash / reserved buys,
- fills only orders that existed before the current bar,
- persists broker + lane state and restarts at 08:40Z,
- reconciles expected vs broker orders after restart and every bar,
- reproduces the later two-pass stale lease release,
- cancels the explicit remaining BUY ids before parking the lane.

Run:

```bash
python -m unittest discover -s tests -v
python fake_broker_replay.py
```

Expected replay result:
- L1 buy: 08:33Z @ 0.332805
- L2 buy: 08:39Z @ 0.330459
- L2 sell: 09:05Z @ 0.332773
- L1 sell: 09:11Z @ 0.335135
- 2 completed round trips
- shadow fee-model realized net $0.07920649; within $0.00019 of the persisted $0.07901866649 reference
- restart reconciliation clean
- first stale scout HOLD, second RELEASE
- explicit cancel of L2/C1 and L3/C0 buys
- broker empty before lane park

The new fill hook supports **full fills only** in this replay. Partial-fill handling remains a separate integration requirement before live use.

Replay note: the $0.0001878 P&L delta versus persisted state is retained as telemetry rather than hidden. The replay uses full fills at the recorded limit prices with the injected 15/15/8 bps schedule; broker/live accounting can differ slightly because of fill/fee rounding and execution details.
