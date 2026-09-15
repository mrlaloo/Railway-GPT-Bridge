# Replay result

Local run: **20/20 tests pass** (16 v3 shadow tests + 4 recorded fake-broker replay tests).

Recorded CRV/USD first-cross sequence reproduced:
- BUY L1/C0: 2026-09-13 08:33Z @ 0.332805, qty 37.28910322
- BUY L2/C0: 2026-09-13 08:39Z @ 0.330459, qty 37.55382664
- SELL L2/C0: 2026-09-13 09:05Z @ 0.332773
- SELL L1/C0: 2026-09-13 09:11Z @ 0.335135

Execution/restart:
- completed round trips: 2
- replay realized net: 0.079206490619451192 USD
- persisted reference: 0.079018666493078280 USD
- delta: +0.000187824126372912 USD
- forced restart at 08:40Z: reconciliation clean
- reconciliation checked after every replay bar: clean

Lease/release replay:
- stale scout #1: HOLD / release_confirm_1_of_2
- stale scout #2: RELEASE / stale_better_challenger_confirmed
- explicit cancels: ALFSH-CRVUSD-L2-C1-BUY, ALFSH-CRVUSD-L3-C0-BUY
- broker orders after cancel: none
- inventory before park: zero
- lane parked only after broker confirms empty

Important replay finding: v3's bar simulator cannot safely represent a persisted resting BUY solely through `nearest_live_buys(mid)`. On the real 08:33 bar, CRV closed below the L1 limit even though the resting L1 order filled. v4 therefore adds an explicit **confirmed broker-fill event boundary** (`apply_broker_fill`) without changing the grid/lease strategy.

Current limitation: `apply_broker_fill` intentionally accepts full fills only. Partial-fill behavior remains unresolved and must be designed/tested before any live integration.

Status: **shadow/replay only; no deployment.**
