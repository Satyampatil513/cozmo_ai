"""Floor-plan extraction against a synthetic cloud whose rooms have known dimensions.

Run: python tests/test_floorplan.py     (no pytest dependency needed)

This is the LiDAR measurement path (`pipeline/geometry/floorplan.py`). It replaces the
plane-per-wall polygon for LiDAR because that path needs every wall to have "nothing behind
it" and so returns None on any walkthrough, and because trajectory-density room splitting
over-segments a single scan. The cases here pin down the part that does not need real data:
that a fused metric cloud is projected to the floor, rasterised, carved into rooms at doorway
pinch-points, and that each room comes back with a length x width and a defended - or
honestly abstained - ceiling height.

The synthetic cloud is built in an ARKit-style Y-up world frame, matching the LiDAR loader.
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from pipeline.geometry.floorplan import extract_floorplan          # noqa: E402

FAILURES: list[str] = []
UP = np.array([0.0, 1.0, 0.0])
CEIL = 2.70


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def _wall(x0, x1, z0, z1, axis, density=42):
    """A vertical wall panel from (x0,z0) to (x1,z1), floor to ceiling, normal along `axis`."""
    if axis == "x":
        zz, yy = np.meshgrid(np.linspace(z0, z1, max(2, int((z1 - z0) * density))),
                             np.linspace(0, CEIL, int(CEIL * density)))
        xx = np.full(zz.size, x0)
        n = [1.0, 0.0, 0.0]
    else:
        xx, yy = np.meshgrid(np.linspace(x0, x1, max(2, int((x1 - x0) * density))),
                             np.linspace(0, CEIL, int(CEIL * density)))
        zz = np.full(xx.size, z0)
        n = [0.0, 0.0, 1.0]
    p = np.stack([np.ravel(xx), np.ravel(yy), np.ravel(zz)], axis=1)
    return p, np.tile(n, (len(p), 1))


def _slab(x0, x1, z0, z1, y, ny, density=38):
    xx, zz = np.meshgrid(np.linspace(x0, x1, int((x1 - x0) * density)),
                         np.linspace(z0, z1, int((z1 - z0) * density)))
    p = np.stack([xx.ravel(), np.full(xx.size, y), zz.ravel()], axis=1)
    return p, np.tile(ny, (len(p), 1))


def two_room_cloud(doorway=(1.05, 1.95), with_ceiling=True, seed=0):
    """Rooms A [0,4]x[0,3] and B [4,8]x[0,3], shared wall at x=4 with a doorway gap."""
    rng = np.random.default_rng(seed)
    P, N = [], []

    def add(p, n):
        P.append(p + rng.normal(0, 0.006, p.shape))
        N.append(n)

    add(*_slab(0, 8, 0, 3, 0.0, [0, 1, 0]))                       # floor
    if with_ceiling:
        add(*_slab(0, 8, 0, 3, CEIL, [0, -1, 0]))                 # ceiling
    add(*_wall(0, 0, 0, 3, "x"))                                  # west
    add(*_wall(8, 8, 0, 3, "x"))                                  # east
    add(*_wall(0, 8, 0, 0, "z"))                                  # south
    add(*_wall(0, 8, 3, 3, "z"))                                  # north
    d0, d1 = doorway                                              # shared wall minus doorway
    add(*_wall(4, 4, 0, d0, "x"))
    add(*_wall(4, 4, d1, 3, "x"))

    pts = np.concatenate(P)
    nrm = np.concatenate(N)
    cams = np.array([[1.2, 1.4, 1.5], [2.6, 1.4, 1.8], [3.2, 1.4, 0.9],
                     [5.0, 1.4, 1.5], [6.4, 1.4, 1.9], [7.0, 1.4, 0.8]])
    return pts, nrm, cams


def test_two_rooms_split_at_the_doorway():
    pts, nrm, cams = two_room_cloud()
    fp = extract_floorplan(pts, nrm, UP, camera_centers=cams)
    check("a floor plan is produced", fp is not None)
    if fp is None:
        return
    check("exactly two rooms", len(fp.rooms) == 2, f"{len(fp.rooms)}")
    check("the two rooms are connected", len(fp.connections) == 1, f"{len(fp.connections)}")
    for rm in fp.rooms:
        lo, hi = sorted((rm.length_m, rm.width_m))
        check(f"{rm.room_id} is ~3 x 4 m",
              abs(lo - 3.0) < 0.4 and abs(hi - 4.0) < 0.4, f"{hi:.2f} x {lo:.2f} m")
        check(f"{rm.room_id} ceiling ~2.70 m",
              rm.ceiling_height_m is not None and abs(rm.ceiling_height_m - CEIL) < 0.12,
              f"{rm.ceiling_height_m}")


def test_footprint_is_the_sum_of_the_rooms():
    pts, nrm, cams = two_room_cloud()
    fp = extract_floorplan(pts, nrm, UP, camera_centers=cams)
    check("footprint ~ 24 m2 (2 x 12)", abs(fp.footprint_area_m2 - 24.0) < 6.0,
          f"{fp.footprint_area_m2:.1f} m2")


def test_thin_ceiling_abstains_on_height_but_still_gives_a_plan():
    pts, nrm, cams = two_room_cloud(with_ceiling=False)
    fp = extract_floorplan(pts, nrm, UP, camera_centers=cams)
    check("plan still produced without a ceiling surface", fp is not None and len(fp.rooms) >= 1)
    if fp is None:
        return
    check("every room abstains on height with a reason",
          all(r.ceiling_height_m is None and r.ceiling_note for r in fp.rooms),
          "; ".join(r.ceiling_note for r in fp.rooms)[:120])
    check("rooms still carry a length x width",
          all(r.length_m > 1.0 and r.width_m > 1.0 for r in fp.rooms))


def test_single_room_is_not_over_split():
    pts, nrm, cams = two_room_cloud(doorway=(0.0, 3.0))   # no shared wall at all -> one space
    fp = extract_floorplan(pts, nrm, UP, camera_centers=cams)
    check("one open space -> one room", fp is not None and len(fp.rooms) == 1,
          f"{None if fp is None else len(fp.rooms)}")


def test_result_dict_has_what_the_schema_adapter_and_renderer_read():
    from pipeline.types import Scale
    pts, nrm, cams = two_room_cloud()
    fp = extract_floorplan(pts, nrm, UP, camera_centers=cams)
    res = fp.to_result("cap", "lidar", Scale(source="lidar_metric"))
    check("mode is floorplan", res.get("mode") == "floorplan")
    check("sub_rooms present", len(res.get("sub_rooms", [])) == 2)
    sr = res["sub_rooms"][0]
    for key in ("polygon", "wall_length_measurements", "floor_area_measurement", "openings",
                "surfaces", "damage", "scope_items"):
        check(f"sub_room carries '{key}'", key in sr)
    poly = sr["polygon"]
    check("polygon has world_corners for the renderer",
          isinstance(poly.get("world_corners"), list) and len(poly["world_corners"]) == 4)
    check("polygon has local corners for the schema adapter",
          isinstance(poly.get("corners"), list) and len(poly["corners"]) == 4)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    sys.exit(1 if FAILURES else 0)
