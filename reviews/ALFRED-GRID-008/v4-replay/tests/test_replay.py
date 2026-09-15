import unittest
from decimal import Decimal
from pathlib import Path

from fake_broker_replay import run_replay

D = Decimal
ROOT = Path(__file__).resolve().parents[1]


class RecordedFakeBrokerReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_replay(ROOT / "fixtures" / "crv_2026-09-13_recorded.json")

    def test_restart_reconcile_is_clean(self):
        self.assertTrue(self.result["restart_reconcile_clean"])

    def test_recorded_fill_order_and_timestamps(self):
        fills = self.result["fills"]
        self.assertEqual([(f["timestamp"], f["side"], f["order_id"]) for f in fills], [
            ("2026-09-13T08:33:00Z", "buy", "ALFSH-CRVUSD-L1-C0-BUY"),
            ("2026-09-13T08:39:00Z", "buy", "ALFSH-CRVUSD-L2-C0-BUY"),
            ("2026-09-13T09:05:00Z", "sell", "ALFSH-CRVUSD-L2-C0-SELL"),
            ("2026-09-13T09:11:00Z", "sell", "ALFSH-CRVUSD-L1-C0-SELL"),
        ])

    def test_two_round_trips_are_positive_and_close_to_live_evidence(self):
        self.assertEqual(self.result["completed_round_trips"], 2)
        realized = D(self.result["realized_net_usd"])
        self.assertGreater(realized, D("0"))
        self.assertLess(abs(realized - D("0.079018666493078280")), D("0.00025"))

    def test_dual_confirm_release_then_explicit_cancel_and_flat_park(self):
        self.assertEqual(self.result["lease_first"]["action"], "HOLD")
        self.assertEqual(self.result["lease_first"]["confirmations"], 1)
        self.assertEqual(self.result["lease_second"]["action"], "RELEASE")
        self.assertEqual(self.result["lease_second"]["confirmations"], 2)
        self.assertEqual(self.result["release_cancel_ids"], [
            "ALFSH-CRVUSD-L2-C1-BUY",
            "ALFSH-CRVUSD-L3-C0-BUY",
        ])
        self.assertEqual(self.result["remaining_broker_orders"], [])
        self.assertFalse(self.result["lane_kept"])


if __name__ == "__main__":
    unittest.main()
