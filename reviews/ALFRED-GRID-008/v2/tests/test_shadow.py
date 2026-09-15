import json
import unittest
from decimal import Decimal

from alfred_grid_008_shadow import (
    FeeSchedule,
    LaneStats,
    LeaseTracker,
    ShadowConfig,
    ShadowLane,
    crv_case_from_issue,
    review_productivity_lease,
)

D = Decimal
FEES = FeeSchedule(maker_buy=D("0.0015"), maker_sell=D("0.0015"), friction=D("0.0008"))
ZERO = D("0")


class VirtualGridV2Tests(unittest.TestCase):
    def test_proximity_cap_blocks_crv_deep_bids(self):
        lane = ShadowLane("CRV/USD", D("0.3351515"), ShadowConfig(), FEES)
        self.assertEqual(lane.nearest_live_buys(D("0.34610")), [])

    def test_nearest_two_live_when_market_is_near_origin(self):
        lane = ShadowLane("TEST/USD", D("1.000000"), ShadowConfig(), FEES)
        live = lane.nearest_live_buys(D("0.994"))
        self.assertEqual(len(live), 2)
        self.assertEqual([o.level for o in live], [1, 2])

    def test_buy_fill_paired_sell_no_same_bar_lookahead(self):
        lane = ShadowLane("TEST/USD", D("1.000000"), ShadowConfig(level_budget_usd=D("12.00")), FEES)
        first = lane.nearest_live_buys(D("0.994"))[0]
        target = lane.sell_target_for(first.price)
        events = lane.on_market(first.price, target, mid=D("0.994"))
        self.assertTrue(any(e.startswith("BUY_FILL") for e in events))
        self.assertFalse(any(e.startswith("SELL_FILL") for e in events))
        events = lane.on_market(target, target, mid=target)
        self.assertTrue(any(e.startswith("SELL_FILL") for e in events))
        self.assertEqual(lane.completed_round_trips, 1)
        self.assertGreater(lane.realized_net_usd, ZERO)

    def test_rearm_parks_if_mid_left_origin_window(self):
        lane = ShadowLane("TEST/USD", D("1.000000"), ShadowConfig(level_budget_usd=D("12.00")), FEES)
        first = lane.nearest_live_buys(D("0.994"))[0]
        lane.on_market(first.price, first.price, mid=D("0.994"))
        target = lane.paired_sells()[0].price
        lane.on_market(target, target, mid=D("1.05"))
        self.assertEqual(lane.rungs[first.level].state, "parked")
        self.assertEqual(lane.nearest_live_buys(D("1.05")), [])

    def test_parked_rung_unparks_only_when_price_returns(self):
        lane = ShadowLane("TEST/USD", D("1.000000"), ShadowConfig(), FEES)
        lane.rungs[1].state = "parked"
        self.assertEqual(lane.nearest_live_buys(D("1.05")), [])
        live = lane.nearest_live_buys(D("1.001"))
        self.assertIn(1, [o.level for o in live])

    def test_dual_confirm_release(self):
        cfg = ShadowConfig()
        tracker = LeaseTracker()
        stats = LaneStats(last_fill_age_hours=D("39.77"), completed_round_trips=2, realized_net_usd=D("0.079"))
        first = review_productivity_lease(
            incumbent_score=D("0.2672"), challenger_symbol="LTC/USD", challenger_score=D("0.9203"),
            stats=stats, cfg=cfg, tracker=tracker,
        )
        second = review_productivity_lease(
            incumbent_score=D("0.2672"), challenger_symbol="LTC/USD", challenger_score=D("0.9203"),
            stats=stats, cfg=cfg, tracker=tracker,
        )
        self.assertEqual(first.action, "HOLD")
        self.assertEqual(second.action, "RELEASE")
        self.assertEqual(second.release_confirmations, 2)

    def test_absolute_edge_required(self):
        cfg = ShadowConfig(challenger_ratio=D("1.05"), challenger_absolute_edge=D("0.20"))
        tracker = LeaseTracker()
        review = review_productivity_lease(
            incumbent_score=D("0.50"), challenger_symbol="UNI/USD", challenger_score=D("0.60"),
            stats=LaneStats(last_fill_age_hours=D("50")), cfg=cfg, tracker=tracker,
        )
        self.assertEqual(review.action, "HOLD")
        self.assertEqual(review.reason, "challenger_absolute_edge")

    def test_tracker_resets_when_candidate_changes(self):
        tracker = LeaseTracker()
        self.assertEqual(tracker.observe("LTC/USD"), 1)
        self.assertEqual(tracker.observe("LTC/USD"), 2)
        self.assertEqual(tracker.observe("UNI/USD"), 1)

    def test_persist_cycle_and_order_ids(self):
        lane = ShadowLane("TEST/USD", D("1.000000"), ShadowConfig(level_budget_usd=D("12.00")), FEES)
        first = lane.nearest_live_buys(D("0.994"))[0]
        lane.on_market(first.price, first.price, mid=D("0.994"))
        sell = lane.paired_sells()[0]
        state = json.loads(json.dumps(lane.to_state()))
        restored = ShadowLane.from_state(state, lane.cfg, FEES)
        self.assertEqual(restored.rungs[first.level].cycle, first.cycle)
        self.assertEqual(restored.rungs[first.level].last_buy_order_id, first.order_id)
        self.assertEqual(restored.rungs[first.level].last_sell_order_id, sell.order_id)

    def test_min_round_trips_before_protected_is_used(self):
        cfg = ShadowConfig(min_round_trips_before_protected=2)
        tracker = LeaseTracker()
        review = review_productivity_lease(
            incumbent_score=D("0.20"), challenger_symbol="UNI/USD", challenger_score=D("1.00"),
            stats=LaneStats(last_fill_age_hours=D("2"), completed_round_trips=1, realized_net_usd=D("0.05")),
            cfg=cfg, tracker=tracker,
        )
        self.assertFalse(review.productive_history)
        self.assertEqual(review.reason, "recent_fill")
        review2 = review_productivity_lease(
            incumbent_score=D("0.20"), challenger_symbol="UNI/USD", challenger_score=D("1.00"),
            stats=LaneStats(last_fill_age_hours=D("2"), completed_round_trips=2, realized_net_usd=D("0.05")),
            cfg=cfg, tracker=tracker,
        )
        self.assertTrue(review2.productive_history)
        self.assertEqual(review2.reason, "recent_productive_fill")

    def test_fees_are_injected(self):
        cheap = FeeSchedule(D("0"), D("0"), D("0"))
        expensive = FeeSchedule(D("0.002"), D("0.002"), D("0.001"))
        cfg = ShadowConfig()
        lane_a = ShadowLane("TEST/USD", D("1"), cfg, cheap)
        lane_b = ShadowLane("TEST/USD", D("1"), cfg, expensive)
        self.assertLess(lane_a.minimum_profitable_exit(D("1")), lane_b.minimum_profitable_exit(D("1")))

    def test_crv_fixture_releases_after_two_confirmations(self):
        review = crv_case_from_issue(2)
        self.assertEqual(review.action, "RELEASE")
        self.assertEqual(review.reason, "stale_better_challenger_confirmed")


if __name__ == "__main__":
    unittest.main()
