from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Dict, List, Tuple

D = lambda x: Decimal(str(x))
ZERO = Decimal("0")
ONE = Decimal("1")


def q_price(value: Decimal, places: str = "0.000001", up: bool = False) -> Decimal:
    return D(value).quantize(D(places), rounding=ROUND_UP if up else ROUND_DOWN)


def q_qty(value: Decimal, places: str = "0.00000001") -> Decimal:
    return max(ZERO, D(value)).quantize(D(places), rounding=ROUND_DOWN)


@dataclass(frozen=True)
class ShadowConfig:
    virtual_rungs: int = 15
    live_buy_slots: int = 2
    step_pct: Decimal = D("0.007")
    grid_profit_pct: Decimal = D("0.007")
    maker_fee: Decimal = D("0.0015")
    friction_pct: Decimal = D("0.0008")
    min_net_pct: Decimal = D("0.0012")
    level_budget_usd: Decimal = D("12.41")
    min_notional_usd: Decimal = D("10.00")
    stale_after_hours: Decimal = D("12")
    challenger_ratio: Decimal = D("1.25")
    min_round_trips_before_protected: int = 1

    def validate(self) -> None:
        if self.virtual_rungs < 3:
            raise ValueError("virtual_rungs must be >= 3")
        if not 1 <= self.live_buy_slots <= self.virtual_rungs:
            raise ValueError("live_buy_slots must be within virtual_rungs")
        if self.step_pct <= 0 or self.grid_profit_pct <= 0:
            raise ValueError("grid spacing/profit must be positive")
        if self.challenger_ratio <= 1:
            raise ValueError("challenger_ratio must be > 1")


@dataclass
class VirtualRung:
    level: int
    buy_price: Decimal
    cycle: int = 0
    state: str = "armed"
    filled_qty: Decimal = ZERO
    entry_price: Decimal = ZERO
    sell_target: Decimal = ZERO
    realized_net: Decimal = ZERO


@dataclass(frozen=True)
class ShadowOrder:
    symbol: str
    side: str
    level: int
    cycle: int
    price: Decimal
    qty: Decimal
    paired: bool = False


@dataclass
class LaneStats:
    last_fill_age_hours: Decimal = ZERO
    completed_round_trips: int = 0
    realized_net_usd: Decimal = ZERO
    inventory_qty: Decimal = ZERO
    working_sell_count: int = 0


@dataclass(frozen=True)
class LeaseReview:
    action: str
    reason: str
    incumbent_score: Decimal
    challenger_score: Decimal
    challenger_symbol: str
    last_fill_age_hours: Decimal
    completed_round_trips: int
    realized_net_usd: Decimal


@dataclass
class ShadowLane:
    symbol: str
    origin: Decimal
    cfg: ShadowConfig
    rungs: Dict[int, VirtualRung] = field(default_factory=dict)
    realized_net_usd: Decimal = ZERO
    completed_round_trips: int = 0
    fill_events: int = 0

    def __post_init__(self) -> None:
        self.cfg.validate()
        if self.origin <= 0:
            raise ValueError("origin must be positive")
        if not self.rungs:
            for level in range(1, self.cfg.virtual_rungs + 1):
                px = q_price(self.origin * (ONE - self.cfg.step_pct * D(level)))
                self.rungs[level] = VirtualRung(level=level, buy_price=px)

    def minimum_profitable_exit(self, entry: Decimal) -> Decimal:
        required = self.cfg.maker_fee + self.cfg.maker_fee + self.cfg.friction_pct + self.cfg.min_net_pct
        return q_price(entry * (ONE + required), up=True)

    def sell_target_for(self, entry: Decimal) -> Decimal:
        grid = q_price(entry * (ONE + self.cfg.grid_profit_pct), up=True)
        return max(grid, self.minimum_profitable_exit(entry))

    def nearest_live_buys(self, mid: Decimal) -> List[ShadowOrder]:
        mid = D(mid)
        candidates: List[Tuple[Decimal, VirtualRung]] = []
        for rung in self.rungs.values():
            if rung.state != "armed" or rung.buy_price >= mid:
                continue
            candidates.append((mid - rung.buy_price, rung))
        candidates.sort(key=lambda item: item[0])
        out: List[ShadowOrder] = []
        for _, rung in candidates[: self.cfg.live_buy_slots]:
            qty = q_qty(self.cfg.level_budget_usd / rung.buy_price)
            if qty * rung.buy_price < self.cfg.min_notional_usd:
                continue
            out.append(ShadowOrder(self.symbol, "buy", rung.level, rung.cycle, rung.buy_price, qty))
        return out

    def paired_sells(self) -> List[ShadowOrder]:
        out: List[ShadowOrder] = []
        for rung in sorted(self.rungs.values(), key=lambda r: r.level):
            if rung.state != "sell_armed" or rung.filled_qty <= 0:
                continue
            out.append(ShadowOrder(self.symbol, "sell", rung.level, rung.cycle, rung.sell_target, rung.filled_qty, True))
        return out

    def on_market(self, low: Decimal, high: Decimal) -> List[str]:
        low, high = D(low), D(high)
        events: List[str] = []
        preexisting_sells = list(self.paired_sells())
        live = {(o.level, o.cycle): o for o in self.nearest_live_buys(high)}
        for (level, cycle), order in live.items():
            rung = self.rungs[level]
            if rung.cycle != cycle or rung.state != "armed":
                continue
            if low <= order.price <= high:
                rung.state = "sell_armed"
                rung.filled_qty = order.qty
                rung.entry_price = order.price
                rung.sell_target = self.sell_target_for(order.price)
                self.fill_events += 1
                events.append(f"BUY_FILL L{level} C{cycle} {order.qty}@{order.price}")

        for order in preexisting_sells:
            if low <= order.price <= high:
                rung = self.rungs[order.level]
                gross = (order.price - rung.entry_price) * rung.filled_qty
                fees = (
                    rung.entry_price * rung.filled_qty * self.cfg.maker_fee
                    + order.price * rung.filled_qty * self.cfg.maker_fee
                    + rung.entry_price * rung.filled_qty * self.cfg.friction_pct
                )
                net = gross - fees
                rung.realized_net += net
                self.realized_net_usd += net
                self.completed_round_trips += 1
                self.fill_events += 1
                events.append(f"SELL_FILL L{order.level} C{order.cycle} net={net}")
                rung.cycle += 1
                rung.state = "armed"
                rung.filled_qty = ZERO
                rung.entry_price = ZERO
                rung.sell_target = ZERO
        return events

    def inventory_qty(self) -> Decimal:
        return sum((r.filled_qty for r in self.rungs.values() if r.state == "sell_armed"), ZERO)

    def snapshot(self, mid: Decimal) -> dict:
        buys = self.nearest_live_buys(mid)
        sells = self.paired_sells()
        return {
            "symbol": self.symbol,
            "origin": str(self.origin),
            "virtual_rungs": self.cfg.virtual_rungs,
            "live_buys": len(buys),
            "live_sells": len(sells),
            "completed_round_trips": self.completed_round_trips,
            "realized_net_usd": str(self.realized_net_usd),
            "inventory_qty": str(self.inventory_qty()),
            "buy_orders": [o.__dict__ for o in buys],
            "sell_orders": [o.__dict__ for o in sells],
        }


def review_productivity_lease(*, incumbent_score: Decimal, challenger_symbol: str, challenger_score: Decimal, stats: LaneStats, cfg: ShadowConfig) -> LeaseReview:
    incumbent_score = D(incumbent_score)
    challenger_score = D(challenger_score)
    if stats.inventory_qty > ZERO:
        action, reason = "HOLD", "inventory"
    elif stats.working_sell_count > 0:
        action, reason = "HOLD", "working_sell"
    elif stats.last_fill_age_hours < cfg.stale_after_hours:
        action, reason = "HOLD", "recent_fill"
    else:
        hurdle = incumbent_score * cfg.challenger_ratio if incumbent_score > 0 else ZERO
        if challenger_score <= ZERO or not challenger_symbol:
            action, reason = "HOLD", "no_challenger"
        elif incumbent_score > 0 and challenger_score < hurdle:
            action, reason = "HOLD", "challenger_edge"
        else:
            action, reason = "RELEASE", "stale_better_challenger"
    return LeaseReview(action, reason, incumbent_score, challenger_score, challenger_symbol, stats.last_fill_age_hours, stats.completed_round_trips, stats.realized_net_usd)


def crv_case_from_issue() -> LeaseReview:
    cfg = ShadowConfig()
    stats = LaneStats(last_fill_age_hours=D("39.77"), completed_round_trips=2, realized_net_usd=D("0.079018666493078280"), inventory_qty=ZERO, working_sell_count=0)
    return review_productivity_lease(incumbent_score=D("0.2672"), challenger_symbol="LTC/USD", challenger_score=D("0.9203"), stats=stats, cfg=cfg)
