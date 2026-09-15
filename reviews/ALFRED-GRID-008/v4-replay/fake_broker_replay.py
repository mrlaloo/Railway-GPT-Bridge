from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

from alfred_grid_008_shadow import (
    BrokerOpenOrder,
    D,
    FeeSchedule,
    LaneStats,
    LeaseTracker,
    ShadowConfig,
    ShadowLane,
    ShadowOrder,
    ZERO,
    review_productivity_lease,
)


@dataclass(frozen=True)
class RecordedBar:
    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class FakeBroker:
    """Minimal deterministic limit-order broker for shadow replay only."""

    def __init__(self, cash_usd: Decimal):
        self.cash_usd = D(cash_usd)
        self.open_orders: Dict[str, ShadowOrder] = {}
        self.position_qty: Dict[str, Decimal] = {}
        self.fills: List[dict] = []

    def reserved_buy_usd(self) -> Decimal:
        return sum((o.price * o.qty for o in self.open_orders.values() if o.side == "buy"), ZERO)

    def available_cash_usd(self) -> Decimal:
        return max(ZERO, self.cash_usd - self.reserved_buy_usd())

    def submit(self, order: ShadowOrder, lane: ShadowLane) -> None:
        if order.order_id in self.open_orders:
            return
        if order.side == "buy" and order.price * order.qty > self.available_cash_usd():
            raise RuntimeError(f"insufficient fake cash for {order.order_id}")
        self.open_orders[order.order_id] = order
        lane.register_open_order(order)

    def cancel(self, order_id: str, lane: ShadowLane) -> None:
        order = self.open_orders.pop(order_id, None)
        if order is not None:
            lane.clear_open_order(order_id)

    def broker_view(self) -> List[BrokerOpenOrder]:
        return [BrokerOpenOrder(o.order_id, o.symbol, o.side) for o in self.open_orders.values()]

    def process_bar(self, bar: RecordedBar, lane: ShadowLane) -> List[str]:
        """Fill only orders that existed before this bar; new orders wait for next bar."""
        events: List[str] = []
        preexisting = list(self.open_orders.values())
        for order in preexisting:
            if order.side == "buy" and bar.low <= order.price:
                self.open_orders.pop(order.order_id, None)
                self.cash_usd -= order.price * order.qty
                self.position_qty[order.symbol] = self.position_qty.get(order.symbol, ZERO) + order.qty
                event = lane.apply_broker_fill(order.order_id, order.price, order.qty, mid=bar.close)
                self.fills.append({"timestamp": bar.timestamp, "order_id": order.order_id, "side": "buy", "price": str(order.price), "qty": str(order.qty)})
                events.append(event)
            elif order.side == "sell" and bar.high >= order.price:
                self.open_orders.pop(order.order_id, None)
                held = self.position_qty.get(order.symbol, ZERO)
                if held < order.qty:
                    raise RuntimeError(f"fake broker short prevention: {order.order_id}")
                self.position_qty[order.symbol] = held - order.qty
                self.cash_usd += order.price * order.qty
                event = lane.apply_broker_fill(order.order_id, order.price, order.qty, mid=bar.close)
                self.fills.append({"timestamp": bar.timestamp, "order_id": order.order_id, "side": "sell", "price": str(order.price), "qty": str(order.qty)})
                events.append(event)
        return events

    def submit_lane_orders(self, lane: ShadowLane, mid: Decimal) -> None:
        for sell in lane.paired_sells():
            self.submit(sell, lane)
        for buy in lane.nearest_live_buys(mid, available_cash_usd=self.available_cash_usd()):
            self.submit(buy, lane)

    def to_state(self) -> dict:
        return {
            "cash_usd": str(self.cash_usd),
            "position_qty": {k: str(v) for k, v in self.position_qty.items()},
            "open_orders": [
                {
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "side": o.side,
                    "level": o.level,
                    "cycle": o.cycle,
                    "price": str(o.price),
                    "qty": str(o.qty),
                    "paired": o.paired,
                }
                for o in self.open_orders.values()
            ],
            "fills": list(self.fills),
        }

    @classmethod
    def from_state(cls, state: dict) -> "FakeBroker":
        obj = cls(D(state["cash_usd"]))
        obj.position_qty = {k: D(v) for k, v in state.get("position_qty", {}).items()}
        for row in state.get("open_orders", []):
            o = ShadowOrder(
                row["order_id"], row["symbol"], row["side"], int(row["level"]), int(row["cycle"]),
                D(row["price"]), D(row["qty"]), bool(row.get("paired", False)),
            )
            obj.open_orders[o.order_id] = o
        obj.fills = list(state.get("fills", []))
        return obj


def load_fixture(path: str | Path) -> tuple[dict, List[RecordedBar]]:
    raw = json.loads(Path(path).read_text())
    bars = [RecordedBar(b["timestamp"], D(b["open"]), D(b["high"]), D(b["low"]), D(b["close"])) for b in raw["bars"]]
    return raw, bars


def run_replay(fixture_path: str | Path, *, restart_after: str = "2026-09-13T08:40:00Z") -> dict:
    fixture, bars = load_fixture(fixture_path)
    cfg = ShadowConfig()
    fees = FeeSchedule(D("0.0015"), D("0.0015"), D("0.0008"))
    lane = ShadowLane(fixture["symbol"], D(fixture["origin"]), cfg, fees)
    broker = FakeBroker(D("55.00"))
    timeline: List[dict] = []
    restart_report = None

    for bar in bars:
        events = broker.process_bar(bar, lane)
        broker.submit_lane_orders(lane, bar.close)
        reconcile = lane.reconcile_broker_open_orders(broker.broker_view())
        if not reconcile.clean:
            raise RuntimeError(f"reconcile mismatch at {bar.timestamp}: {reconcile}")

        timeline.append({
            "timestamp": bar.timestamp,
            "events": events,
            "open_orders": sorted(broker.open_orders),
            "cash_usd": str(broker.cash_usd),
            "completed_round_trips": lane.completed_round_trips,
            "realized_net_usd": str(lane.realized_net_usd),
        })

        if bar.timestamp == restart_after:
            lane_state = json.loads(json.dumps(lane.to_state()))
            broker_state = json.loads(json.dumps(broker.to_state()))
            lane = ShadowLane.from_state(lane_state, cfg, fees)
            broker = FakeBroker.from_state(broker_state)
            restart_report = lane.reconcile_broker_open_orders(broker.broker_view())
            if not restart_report.clean:
                raise RuntimeError(f"restart reconcile mismatch: {restart_report}")

    tracker = LeaseTracker()
    stats = LaneStats(
        last_fill_age_hours=D("39.77"),
        completed_round_trips=lane.completed_round_trips,
        realized_net_usd=lane.realized_net_usd,
        inventory_qty=lane.inventory_qty(),
        working_sell_count=sum(1 for o in broker.open_orders.values() if o.side == "sell"),
    )
    review1 = review_productivity_lease(
        incumbent_score=D("0.2672"), challenger_symbol="LTC/USD", challenger_score=D("0.9203"),
        stats=stats, cfg=cfg, tracker=tracker,
    )
    review2 = review_productivity_lease(
        incumbent_score=D("0.2672"), challenger_symbol="LTC/USD", challenger_score=D("0.9203"),
        stats=stats, cfg=cfg, tracker=tracker,
    )

    release_plan = lane.build_release_cancel_plan(broker.broker_view()) if review2.action == "RELEASE" else None
    cancelled: List[str] = []
    if release_plan and release_plan.ready:
        for oid in release_plan.cancel_order_ids:
            broker.cancel(oid, lane)
            cancelled.append(oid)
        lane.mark_released_after_cancels(broker.broker_view())

    persisted_reference = D("0.079018666493078280")
    return {
        "fixture_source": fixture["source"],
        "bars_replayed": len(bars),
        "restart_reconcile_clean": bool(restart_report and restart_report.clean),
        "fills": broker.fills,
        "completed_round_trips": lane.completed_round_trips,
        "realized_net_usd": str(lane.realized_net_usd),
        "persisted_reference_realized_usd": str(persisted_reference),
        "realized_delta_usd": str(lane.realized_net_usd - persisted_reference),
        "lease_first": {"action": review1.action, "reason": review1.reason, "confirmations": review1.release_confirmations},
        "lease_second": {"action": review2.action, "reason": review2.reason, "confirmations": review2.release_confirmations},
        "release_cancel_ids": cancelled,
        "lane_kept": lane.lane_kept,
        "remaining_broker_orders": sorted(broker.open_orders),
        "timeline": timeline,
    }


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    result = run_replay(here / "fixtures" / "crv_2026-09-13_recorded.json")
    print(json.dumps(result, indent=2))
