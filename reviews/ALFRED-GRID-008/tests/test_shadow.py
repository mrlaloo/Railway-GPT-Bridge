import unittest
from decimal import Decimal

from alfred_grid_008_shadow import (
    LaneStats,
    ShadowConfig,
    ShadowLane,
    crv_case_from_issue,
    review_productivity_lease,
)

D = Decimal


class VirtualGridTests(unittest.TestCase):
    def test_only_nearest_two_buys_are_live(self):
        lane = ShadowLane("CRV/USD", D("0.3351515"), ShadowConfig())
        live = lane.nearest_live_buys(D("0.34610"))
        self.assertEqual(len(live), 2)
        self.assertEqual([o.level for o in live], [1, 2])

    def test_buy_fill_creates_exact_paired_sell_and_rearms(self):
        lane = ShadowLane("TEST/USD", D("1.000000"), ShadowConfig(level_budget_usd=D("12.00")))
        first = lane.nearest_live_buys(D("1.001"))[0]
        events = lane.on_market(first.price, D("1.001"))
        self.assertTrue(any(e.startswith("BUY_FILL") for e in events))
        sells = lane.paired_sells()
        self.assertEqual(len(sells), 1)
        self.assertTrue(sells[0].paired)
        self.assertGreater(sells[0].price, first.price)

        target = sells[0].price
        events = lane.on_market(target, target)
        self.assertTrue(any(e.startswith("SELL_FILL") for e in events))
        self.assertEqual(lane.completed_round_trips, 1)
        self.assertGreater(lane.realized_net_usd, D("0"))
        self.assertEqual(lane.rungs[first.level].state, "armed")
        self.assertEqual(lane.rungs[first.level].cycle, first.cycle + 1)

    def test_pending_buys_do_not_block_capital_escape(self):
        review = crv_case_from_issue()
        self.assertEqual(review.action, "RELEASE")
        self.assertEqual(review.reason, "stale_better_challenger")

    def test_inventory_blocks_release(self):
        cfg = ShadowConfig()
        review = review_productivity_lease(
            incumbent_score=D("0.20"),
            challenger_symbol="UNI/USD",
            challenger_score=D("1.00"),
            stats=LaneStats(
                last_fill_age_hours=D("50"),
                completed_round_trips=0,
                inventory_qty=D("1"),
            ),
            cfg=cfg,
        )
        self.assertEqual(review.action, "HOLD")
        self.assertEqual(review.reason, "inventory")

    def test_recent_fill_blocks_release(self):
        cfg = ShadowConfig(stale_after_hours=D("12"))
        review = review_productivity_lease(
            incumbent_score=D("0.20"),
            challenger_symbol="UNI/USD",
            challenger_score=D("1.00"),
            stats=LaneStats(last_fill_age_hours=D("2")),
            cfg=cfg,
        )
        self.assertEqual(review.action, "HOLD")
        self.assertEqual(review.reason, "recent_fill")


if __name__ == "__main__":
    unittest.main()
