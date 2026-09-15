# ALFRED-GRID-008 — fresh live UNI evidence

Read-only inspection only. No broker, state, code, variables, or deployment changes were made.

Fresh live evidence timestamp: `2026-09-15T02:13:07Z`.

## Current live Alfred

- active symbol: `UNI/USD`
- lease: `HOLD`
- lease reason: `inventory`
- grid origin: `6.67985`
- current scout leader: `UNI/USD` score `0.7798`
- challenger: `SUSHI/USD` score `0.7438`
- current live grid code is still the existing ALFG2 static 3-level engine with capital lease, not the ALFRED-GRID-008 virtual-grid shadow/replay implementation

## Current Alfred-tracked UNI orders

- L1/C1 BUY `ALFG2-UNIUSD-L1-C1-BUY` — `6.633`, qty `1.87094828`, filled
- L1/C1 SELL `ALFG2-UNIUSD-L1-C1-SELL-P1` — `6.6795`, qty `1.86814186`, status `new`
- L2/C0 BUY `ALFG2-UNIUSD-L2-C0-BUY` — `6.5863`, qty `1.8842142`, status `new`
- L3/C0 BUY `ALFG2-UNIUSD-L3-C0-BUY` — `6.5395`, qty `1.8976986`, status `new`

The Alpaca screenshot showing three UNI limit rows is therefore consistent with this live UNI lane. The screenshot price near `$6.60` is unit price, not order notional. Each buy is about `$12.41` notional.

## Reconciliation

- last explicit UNI reconcile timestamp visible in state: `2026-09-15T02:07:17.319451Z`
- no mismatch warning was logged
- Alfred's internal ledger shows 2 open BUYs + 1 open SELL after the L1 fill
- direct broker open-order array was not separately exposed by this read-only runtime inspection, so broker truth beyond the internal ledger should not be inferred

## Important correction

The Railway agent initially labeled ALFG2 as the new virtual-grid architecture. That inference is wrong. Current `alfred/engine.py` still seeds every level using:

`for lvl in range(1, self.cfg.grid_levels + 1)`

and the live state shows three static levels around one origin. ALFRED-GRID-008 v4 remains isolated shadow/replay only.

POSITION: agree with Grok's request for fresh state
STAGE: READY_FOR_TEST
LIVE: current Alfred remains old ALFG2 static grid; ALFRED-GRID-008 is not live

No deployment requested by this evidence update.
