from decimal import Decimal
from pprint import pprint

from alfred_grid_008_shadow import ShadowConfig, ShadowLane, crv_case_from_issue


if __name__ == "__main__":
    cfg = ShadowConfig()
    lane = ShadowLane(symbol="CRV/USD", origin=Decimal("0.3351515"), cfg=cfg)
    print("CRV evidence lease decision:")
    pprint(crv_case_from_issue())
    print("\nVirtual grid snapshot at live bid ~0.34610:")
    pprint(lane.snapshot(Decimal("0.34610")))
