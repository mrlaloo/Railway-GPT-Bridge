# ALFRED-GRID-008 — paper/shadow review build

This package is deliberately isolated from Alfred's live broker execution. It is a review/test artifact only.

## What it implements

- 15 virtual BUY rungs from a stable lane origin.
- Only the nearest 2 BUY rungs below market are exposed at once.
- A simulated BUY fill immediately creates exactly one paired profitable SELL.
- A simulated SELL fill realizes fee/friction-adjusted P&L and re-arms the same rung for the next cycle.
- Productivity lease uses `last_fill_age`, inventory, working SELLs, completed round trips, realized net, and challenger score.
- Pending BUY orders do **not** count as productivity and therefore cannot indefinitely trap capital.
- The ALFRED-GRID-008 CRV evidence fixture (`39.77h` idle, CRV score `0.2672`, LTC `0.9203`) resolves to `RELEASE / stale_better_challenger`.

## What it does NOT do

- No Alpaca order submission.
- No cancellation.
- No liquidation.
- No Railway deployment.
- No mutation of Alfred's current state file.
- No threshold-only patch to the existing lease engine.

## Run

```bash
python shadow_runner.py
python -m unittest discover -s tests -v
```

## Review questions for Grok / ChatGPT

1. Is the virtual-rung activation rule correct for a small account, or should the live BUY window be 1 instead of 2 when free cash is low?
2. Should capital escape require a minimum absolute challenger edge in addition to the ratio?
3. Should a flat stale lane be released immediately at the stale threshold, or require two consecutive scout confirmations?
4. On a completed SELL, should the rung re-arm at the original stable price (current behavior) or be allowed to migrate only after the lane is fully flat?

## Acceptance target before any live work

- repeated simulated round trips,
- no orphan inventory or exits,
- positive realized net after modeled fees/friction,
- pending BUYs cannot lock a lane past the stale/productivity gate,
- deterministic restart/state behavior added before integration.
