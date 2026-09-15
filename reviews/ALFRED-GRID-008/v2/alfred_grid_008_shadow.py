from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Dict, List, Tuple, Any

D = lambda x: Decimal(str(x))
ZERO = Decimal("0")
ONE = Decimal("1")


def q_price(value: Decimal, places: str = "0.000001", up: bool = False) -> Decimal:
    return D(value).quantize(D(places), rounding=ROUND_UP if up else ROUND_DOWN)


def q_qty(value: Decimal, places: str = "0.00000001") -> Decimal:
    return max(ZERO, D(value)).quantize(D(places), rounding=ROUND_DOWN)


@dataclass(frozen=True)
class FeeSchedule:
    maker_buy: Decimal
    maker_sell: Decimal
    friction: Decimal

    def validate(self) -> None:
        if min(self.maker_buy, self.maker_sell, self.friction) < ZERO:
            raise ValueError("fees/friction cannot be negative")


@dataclass(frozen=True)
class ShadowConfig:
    virtual_rungs: int = 15
    live_buy_slots: int = 2
    step_pct: Decimal = D("0.007")
    grid_profit_pct: Decimal = D("0.007")
    min_net_pct: Decimal = D("0.0012")
    level_budget_usd: Decimal = D("12.41")
    min_notional_usd: Decimal = D("10.00")
    max_live_buy_distance_steps: Decimal = D("1.5")
    rearm_origin_window_steps: Decimal = D("3")
    stale_after_hours: Decimal = D("12")
    challenger_ratio: Decimal = D("1.25")
    challenger_absolute_edge: Decimal = D("0.20")
    release_confirmations_required: int = 2
    min_round_trips_before_protected: int = 1

    def validate(self) -> None:
        if self.virtual_rungs < 3:
            raise ValueError("virtual_rungs must be >= 3")
        if not 1 <= self.live_buy_slots <= self.virtual_rungs:
            raise ValueError("live_buy_slots must be within virtual_rungs")
        if self.step_pct <= 0 or self.grid_profit_pct <= 0:
            raise ValueError("grid spacing/profit must be positive")
        if self.max_live_buy_distance_steps <= 0 or self.rearm_origin_window_steps <= 0:
            raise ValueError("distance/window steps must be positive")
        if self.challenger_ratio <= 1:
            raise ValueError("challenger_ratio must be > 1")
        if self.challenger_absolute_edge < 0:
            raise ValueError("challenger_absolute_edge cannot be negative")
        if self.release_confirmations_required < 2:
            raise ValueError("release_confirmations_required must be >= 2")
        if self.min_round_trips_before_protected < 0:
            raise ValueError("min_round_trips_before_protected cannot be negative")


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
    last_buy_order_id: str = ""
    last_sell_order_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "buy_price": str(self.buy_price),
            "cycle": self.cycle,
            "state": self.state,
            "filled_qty": str(self.filled_qty),
            "entry_price": str(self.entry_price),
            "sell_target": str(self.sell_target),
            "realized_net": str(self.realized_net),
            "last_buy_order_id": self.last_buy_order_id,
            "last_sell_order_id": self.last_sell_order_id,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "VirtualRung":
        return cls(
            level=int(row["level"]),
            buy_price=D(row["buy_price"]),
            cycle=int(row.get("cycle", 0)),
            state=str(row.get("state", "armed")),
            filled_qty=D(row.get("filled_qty", 0)),
            entry_price=D(row.get("entry_price", 0)),
            sell_target=D(row.get("sell_target", 0)),
            realized_net=D(row.get("realized_net", 0)),
            last_buy_order_id=str(row.get("last_buy_order_id", "")),
            last_sell_order_id=str(row.get("last_sell_order_id", "")),
        )


@dataclass(frozen=True)
class ShadowOrder:
    order_id: str
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


@dataclass
class LeaseTracker:
    candidate: str = ""
    consecutive: int = 0

    def reset(self) -> None:
        self.candidate = ""
        self.consecutive = 0

    def observe(self, candidate: str) -> int:
        if candidate and candidate == self.candidate:
            self.consecutive += 1
        else:
            self.candidate = candidate
            self.consecutive = 1 if candidate else 0
        return self.consecutive

    def to_dict(self) -> dict[str, Any]:
        return {"candidate": self.candidate, "consecutive": self.consecutive}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "LeaseTracker":
        return cls(candidate=str(row.get("candidate", "")), consecutive=int(row.get("consecutive", 0)))


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
    productive_history: bool
    release_confirmations: int


@dataclass
class ShadowLane:
    symbol: str
    origin: Decimal
    cfg: ShadowConfig
    fees: FeeSchedule
    lane_kept: bool = True
    rungs: Dict[int, VirtualRung] = field(default_factory=dict)
    realized_net_usd: Decimal = ZERO
    completed_round_trips: int = 0
    fill_events: int = 0

    def __post_init__(self) -> None:
        self.cfg.validate()
        self.fees.validate()
        if self.origin <= 0:
            raise ValueError("origin must be positive")
        if not self.rungs:
            for level in range(1, self.cfg.virtual_rungs + 1):
                px = q_price(self.origin * (ONE - self.cfg.step_pct * D(level)))
                self.rungs[level] = VirtualRung(level=level, buy_price=px)

    def _order_id(self, side: str, rung: VirtualRung) -> str:
        code = self.symbol.replace("/", "")
        return f"ALFSH-{code}-L{rung.level}-C{rung.cycle}-{side.upper()}"

    def within_origin_window(self, mid: Decimal) -> bool:
        mid = D(mid)
        max_move = self.cfg.step_pct * self.cfg.rearm_origin_window_steps
        return abs(mid - self.origin) / self.origin <= max_move

    def within_live_buy_distance(self, mid: Decimal, buy_price: Decimal) -> bool:
        mid, buy_price = D(mid), D(buy_price)
        if mid <= 0 or buy_price >= mid:
            return False
        distance = (mid - buy_price) / mid
        return distance <= self.cfg.step_pct * self.cfg.max_live_buy_distance_steps

    def minimum_profitable_exit(self, entry: Decimal) -> Decimal:
        required = self.fees.maker_buy + self.fees.maker_sell + self.fees.friction + self.cfg.min_net_pct
        return q_price(entry * (ONE + required), up=True)

    def sell_target_for(self, entry: Decimal) -> Decimal:
        grid = q_price(entry * (ONE + self.cfg.grid_profit_pct), up=True)
        return max(grid, self.minimum_profitable_exit(entry))

    def _refresh_parked(self, mid: Decimal) -> None:
        if not self.lane_kept or not self.within_origin_window(mid):
            return
        for rung in self.rungs.values():
            if rung.state == "parked":
                rung.state = "armed"

    def nearest_live_buys(self, mid: Decimal) -> List[ShadowOrder]:
        mid = D(mid)
        if not self.lane_kept:
            return []
        self._refresh_parked(mid)
        candidates: List[Tuple[Decimal, VirtualRung]] = []
        for rung in self.rungs.values():
            if rung.state != "armed":
                continue
            if not self.within_live_buy_distance(mid, rung.buy_price):
                continue
            candidates.append((mid - rung.buy_price, rung))
        candidates.sort(key=lambda item: item[0])
        out: List[ShadowOrder] = []
        for _, rung in candidates[: self.cfg.live_buy_slots]:
            qty = q_qty(self.cfg.level_budget_usd / rung.buy_price)
            if qty * rung.buy_price < self.cfg.min_notional_usd:
                continue
            oid = self._order_id("buy", rung)
            rung.last_buy_order_id = oid
            out.append(ShadowOrder(oid, self.symbol, "buy", rung.level, rung.cycle, rung.buy_price, qty))
        return out

    def paired_sells(self) -> List[ShadowOrder]:
        out: List[ShadowOrder] = []
        for rung in sorted(self.rungs.values(), key=lambda r: r.level):
            if rung.state != "sell_armed" or rung.filled_qty <= 0:
                continue
            oid = self._order_id("sell", rung)
            rung.last_sell_order_id = oid
            out.append(ShadowOrder(oid, self.symbol, "sell", rung.level, rung.cycle, rung.sell_target, rung.filled_qty, True))
        return out

    def on_market(self, low: Decimal, high: Decimal, mid: Decimal | None = None) -> List[str]:
        low, high = D(low), D(high)
        mid = D(mid if mid is not None else (low + high) / D("2"))
        events: List[str] = []
        preexisting_sells = list(self.paired_sells())
        live = {(o.level, o.cycle): o for o in self.nearest_live_buys(mid)}
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
                events.append(f"BUY_FILL {order.order_id} {order.qty}@{order.price}")

        for order in preexisting_sells:
            if low <= order.price <= high:
                rung = self.rungs[order.level]
                gross = (order.price - rung.entry_price) * rung.filled_qty
                fees = (
                    rung.entry_price * rung.filled_qty * self.fees.maker_buy
                    + order.price * rung.filled_qty * self.fees.maker_sell
                    + rung.entry_price * rung.filled_qty * self.fees.friction
                )
                net = gross - fees
                rung.realized_net += net
                self.realized_net_usd += net
                self.completed_round_trips += 1
                self.fill_events += 1
                events.append(f"SELL_FILL {order.order_id} net={net}")
                rung.cycle += 1
                rung.filled_qty = ZERO
                rung.entry_price = ZERO
                rung.sell_target = ZERO
                rung.state = "armed" if self.lane_kept and self.within_origin_window(mid) else "parked"
        return events

    def inventory_qty(self) -> Decimal:
        return sum((r.filled_qty for r in self.rungs.values() if r.state == "sell_armed"), ZERO)

    def to_state(self) -> dict[str, Any]:
        return {
            "schema": 2,
            "symbol": self.symbol,
            "origin": str(self.origin),
            "lane_kept": self.lane_kept,
            "realized_net_usd": str(self.realized_net_usd),
            "completed_round_trips": self.completed_round_trips,
            "fill_events": self.fill_events,
            "rungs": {str(k): v.to_dict() for k, v in self.rungs.items()},
        }

    @classmethod
    def from_state(cls, state: dict[str, Any], cfg: ShadowConfig, fees: FeeSchedule) -> "ShadowLane":
        rungs = {int(k): VirtualRung.from_dict(v) for k, v in state.get("rungs", {}).items()}
        return cls(
            symbol=str(state["symbol"]),
            origin=D(state["origin"]),
            cfg=cfg,
            fees=fees,
            lane_kept=bool(state.get("lane_kept", True)),
            rungs=rungs,
            realized_net_usd=D(state.get("realized_net_usd", 0)),
            completed_round_trips=int(state.get("completed_round_trips", 0)),
            fill_events=int(state.get("fill_events", 0)),
        )

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
            "buy_orders": [serialize_order(o) for o in buys],
            "sell_orders": [serialize_order(o) for o in sells],
        }


def serialize_order(order: ShadowOrder) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side,
        "level": order.level,
        "cycle": order.cycle,
        "price": str(order.price),
        "qty": str(order.qty),
        "paired": order.paired,
    }


def review_productivity_lease(
    *,
    incumbent_score: Decimal,
    challenger_symbol: str,
    challenger_score: Decimal,
    stats: LaneStats,
    cfg: ShadowConfig,
    tracker: LeaseTracker,
) -> LeaseReview:
    incumbent_score = D(incumbent_score)
    challenger_score = D(challenger_score)
    productive_history = (
        stats.completed_round_trips >= cfg.min_round_trips_before_protected
        and stats.realized_net_usd > ZERO
    )

    if stats.inventory_qty > ZERO:
        action, reason = "HOLD", "inventory"
        tracker.reset()
    elif stats.working_sell_count > 0:
        action, reason = "HOLD", "working_sell"
        tracker.reset()
    elif stats.last_fill_age_hours < cfg.stale_after_hours:
        action, reason = "HOLD", "recent_productive_fill" if productive_history else "recent_fill"
        tracker.reset()
    else:
        ratio_ok = challenger_score > ZERO and (
            incumbent_score <= ZERO or challenger_score >= incumbent_score * cfg.challenger_ratio
        )
        absolute_ok = challenger_score - incumbent_score >= cfg.challenger_absolute_edge
        if challenger_score <= ZERO or not challenger_symbol:
            action, reason = "HOLD", "no_challenger"
            tracker.reset()
        elif not ratio_ok:
            action, reason = "HOLD", "challenger_ratio"
            tracker.reset()
        elif not absolute_ok:
            action, reason = "HOLD", "challenger_absolute_edge"
            tracker.reset()
        else:
            count = tracker.observe(challenger_symbol)
            if count < cfg.release_confirmations_required:
                action, reason = "HOLD", f"release_confirm_{count}_of_{cfg.release_confirmations_required}"
            else:
                action, reason = "RELEASE", "stale_better_challenger_confirmed"

    return LeaseReview(
        action,
        reason,
        incumbent_score,
        challenger_score,
        challenger_symbol,
        stats.last_fill_age_hours,
        stats.completed_round_trips,
        stats.realized_net_usd,
        productive_history,
        tracker.consecutive,
    )


def crv_case_from_issue(confirmations: int = 2) -> LeaseReview:
    cfg = ShadowConfig()
    tracker = LeaseTracker()
    stats = LaneStats(
        last_fill_age_hours=D("39.77"),
        completed_round_trips=2,
        realized_net_usd=D("0.079018666493078280"),
        inventory_qty=ZERO,
        working_sell_count=0,
    )
    result = None
    for _ in range(confirmations):
        result = review_productivity_lease(
            incumbent_score=D("0.2672"),
            challenger_symbol="LTC/USD",
            challenger_score=D("0.9203"),
            stats=stats,
            cfg=cfg,
            tracker=tracker,
        )
    assert result is not None
    return result
