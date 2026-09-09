"""Floor-plan extraction against a synthetic cloud whose rooms have known dimensions.

Run: python tests/test_floorplan.py     (no pytest dependency needed)

This is the LiDAR measurement path (`pipeline/geometry/floorplan.py`). It replaces the
plane-per-wall polygon for LiDAR because that path needs every wall to have "nothing behind
it" and so returns None on any walkthrough, and because trajectory-density room splitting
over-segments a single scan. The cases here pin down the part that does not need real data:
that a fused metric cloud is projected to the floor, rasterised, carved into rooms by the
wall-line arrangement (Hough centre-lines -> face split -> merge faces whose separating line
carries little wall evidence), and that each room comes back with a length x width and a
defended - or honestly abstained - ceiling height.

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


def one_room_with_furniture(seed=1):
    """One 5 x 4 m room with a 1.4 x 1.0 m solid block standing in the middle of the floor -
    a wardrobe / island. A distance-transform split fragments on this; the arrangement
    method must not, because the block casts no wall LINE across the room."""
    rng = np.random.default_rng(seed)
    P, N = [], []

    def add(p, n):
        p = np.asarray(p, float)
        P.append(p + rng.normal(0, 0.006, p.shape))
        N.append(np.tile(np.asarray(n, float), (len(p), 1)) if np.ndim(n) == 1 else n)

    add(*_slab(0, 5, 0, 4, 0.0, [0, 1, 0]))
    add(*_slab(0, 5, 0, 4, CEIL, [0, -1, 0]))
    add(*_wall(0, 0, 0, 4, "x")); add(*_wall(5, 5, 0, 4, "x"))
    add(*_wall(0, 5, 0, 0, "z")); add(*_wall(0, 5, 4, 4, "z"))
    # furniture block: four short faces, ~1.4 m tall, standing free at (1.8-3.2, 1.5-2.5)
    for x in (1.8, 3.2):
        zz, yy = np.meshgrid(np.linspace(1.5, 2.5, 40), np.linspace(0, 1.4, 60))
        add(np.stack([np.full(zz.size, x), yy.ravel(), zz.ravel()], 1), [1.0, 0, 0])
    for z in (1.5, 2.5):
        xx, yy = np.meshgrid(np.linspace(1.8, 3.2, 56), np.linspace(0, 1.4, 60))
        add(np.stack([xx.ravel(), yy.ravel(), np.full(xx.size, z)], 1), [0, 0, 1.0])

    cams = np.array([[0.8, 1.4, 0.8], [1.0, 1.4, 3.4], [4.2, 1.4, 0.7],
                     [4.3, 1.4, 3.3], [2.5, 1.4, 3.6]])
    return np.concatenate(P), np.concatenate(N), cams


def test_furniture_does_not_split_a_room():
    pts, nrm, cams = one_room_with_furniture()
    fp = extract_floorplan(pts, nrm, UP, camera_centers=cams)
    check("furniture in open floor -> still one room",
          fp is not None and len(fp.rooms) == 1,
          f"{None if fp is None else len(fp.rooms)}")
    if fp and fp.rooms:
        rm = fp.rooms[0]
        lo, hi = sorted((rm.length_m, rm.width_m))
        check("room is ~4 x 5 m", abs(lo - 4.0) < 0.5 and abs(hi - 5.0) < 0.5,
              f"{hi:.2f} x {lo:.2f} m")


def l_shaped_room_cloud(seed=2):
    """One L-shaped room: a 5 x 4 m rectangle with a 2 x 2 m bite taken out of one corner.
    True floor area 16 m2; its bounding box is 20 m2. The reported outline must follow the L,
    not fill the box."""
    rng = np.random.default_rng(seed)
    P, N = [], []

    def add(p, n):
        p = np.asarray(p, float)
        P.append(p + rng.normal(0, 0.006, p.shape))
        N.append(np.tile(np.asarray(n, float), (len(p), 1)) if np.ndim(n) == 1 else n)

    # floor + ceiling over the L (two rectangles: 5x2 plus 3x2)
    add(*_slab(0, 5, 0, 2, 0.0, [0, 1, 0])); add(*_slab(0, 3, 2, 4, 0.0, [0, 1, 0]))
    add(*_slab(0, 5, 0, 2, CEIL, [0, -1, 0])); add(*_slab(0, 3, 2, 4, CEIL, [0, -1, 0]))
    # the six walls of the L
    add(*_wall(0, 0, 0, 4, "x")); add(*_wall(5, 5, 0, 2, "x")); add(*_wall(3, 3, 2, 4, "x"))
    add(*_wall(0, 5, 0, 0, "z")); add(*_wall(0, 3, 4, 4, "z")); add(*_wall(3, 5, 2, 2, "z"))
    cams = np.array([[0.7, 1.4, 0.7], [4.3, 1.4, 0.8], [1.0, 1.4, 3.3],
                     [2.5, 1.4, 1.0], [2.6, 1.4, 3.3]])
    return np.concatenate(P), np.concatenate(N), cams


def test_l_shaped_room_outline_is_preserved():
    pts, nrm, cams = l_shaped_room_cloud()
    fp = extract_floorplan(pts, nrm, UP, camera_centers=cams)
    check("L room -> one room", fp is not None and len(fp.rooms) == 1,
          f"{None if fp is None else len(fp.rooms)}")
    if not fp or not fp.rooms:
        return
    rm = fp.rooms[0]
    check("outline follows the L (6 corners, not 4)", len(rm.poly_world) == 6,
          f"{len(rm.poly_world)}")
    check("reported area is the L (~16 m2), not the bounding box (20 m2)",
          abs(rm.area_m2 - 16.0) < 3.0, f"{rm.area_m2:.1f} m2")
    # polygon area via the shoelace formula, from the reported outline
    p = rm.poly_world
    shoe = 0.5 * abs(float(np.dot(p[:, 0], np.roll(p[:, 1], -1))
                          - np.dot(p[:, 1], np.roll(p[:, 0], -1))))
    check("outline polygon area matches the footprint", abs(shoe - rm.area_m2) < 3.0,
          f"outline {shoe:.1f} vs footprint {rm.area_m2:.1f} m2")


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
    check("polygon has a real outline for the renderer (>= 4 pts, closed)",
          isinstance(poly.get("world_corners"), list) and len(poly["world_corners"]) >= 4)
    check("polygon has matching local corners for the schema adapter",
          isinstance(poly.get("corners"), list)
          and len(poly["corners"]) == len(poly["world_corners"]))
    check("outline edge count matches wall_lengths",
          len(poly["wall_lengths"]) == len(poly["world_corners"]))
    check("a plain rectangular room comes back as ~4 corners",
          4 <= len(poly["world_corners"]) <= 6, f"{len(poly['world_corners'])}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    sys.exit(1 if FAILURES else 0)
