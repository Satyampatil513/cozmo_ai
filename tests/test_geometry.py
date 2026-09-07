"""Geometry stage against rooms whose dimensions we know exactly.

Run: python tests/test_geometry.py     (no pytest dependency needed)

These are the tests that let the deterministic half of the pipeline be finished before any
real capture exists. Every case below is a bug that was actually found and fixed here, not a
hypothetical.
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from pipeline.geometry.planes import fit_floor_ceiling          # noqa: E402
from pipeline.geometry.walls import extract_walls               # noqa: E402
from tests.synthetic_room import make_room                      # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


# Synthetic rooms are built in a z-up world frame. The library's default prior is the
# camera-frame -y, which is right for the photo tier and wrong here, so the convention is
# stated explicitly rather than inherited.
WORLD_UP = np.array([0.0, 0.0, 1.0])


def solve(room, threshold=0.03):
    got = fit_floor_ceiling(room.points, room.normals, threshold=threshold,
                            gravity_prior=WORLD_UP)
    if got is None:
        return None
    g, floor, ceiling, walls = got
    return extract_walls(room.points, floor, ceiling, walls=walls, gravity=g)


def corner_angles(C: np.ndarray) -> np.ndarray:
    prev, nxt = np.roll(C, 1, axis=0), np.roll(C, -1, axis=0)
    a, b = prev - C, nxt - C
    a /= np.linalg.norm(a, axis=1, keepdims=True)
    b /= np.linalg.norm(b, axis=1, keepdims=True)
    return np.sort(np.degrees(np.arccos(np.clip(np.sum(a * b, axis=1), -1, 1))))


def wall_err_pct(geo, room) -> float:
    return float(np.max(np.abs(np.sort(geo.wall_lengths) - np.sort(room.wall_lengths))
                        / np.sort(room.wall_lengths)) * 100)


def test_exact_on_clean_input():
    r = make_room()
    geo = solve(r)
    check("clean room recovers 4 corners", geo is not None and len(geo.corners) == 4)
    check("clean wall lengths exact", wall_err_pct(geo, r) < 0.01, f"{wall_err_pct(geo, r):.4f}%")
    check("clean ceiling height exact", abs(geo.ceiling_height - r.height) < 0.001,
          f"{geo.ceiling_height:.4f} vs {r.height}")
    check("clean floor area exact", abs(geo.floor_area - r.floor_area) < 0.01)


def test_noise_is_nearly_free():
    """Plane fitting averages per-point noise away. This is why the budget is bias, not noise."""
    r = make_room(noise_m=0.05, seed=3)
    geo = solve(r, threshold=0.15)
    e = wall_err_pct(geo, r)
    check("5cm per-point noise stays under 0.5% wall error", e < 0.5, f"{e:.3f}%")


def test_clutter_is_not_mistaken_for_walls():
    """A wardrobe front 60cm off the wall is large, vertical and planar. RANSAC likes it.

    Accepting one as a wall measured 14% wall-length error at LiDAR-grade noise - the single
    largest error source found anywhere in this stage.
    """
    r = make_room(noise_m=0.01, clutter=True, outlier_frac=0.02, seed=2)
    geo = solve(r, threshold=0.05)
    check("furniture planes rejected", geo is not None and len(geo.corners) == 4)
    e = wall_err_pct(geo, r)
    check("clutter does not corrupt wall lengths", e < 1.0, f"{e:.3f}%")


def test_rotation_invariance():
    r = make_room(noise_m=0.01, yaw_deg=23.0, seed=4)
    geo = solve(r)
    e = wall_err_pct(geo, r)
    check("room not axis-aligned still solves", e < 0.5, f"{e:.3f}% at 23deg yaw")


def test_skewed_room_is_not_forced_square():
    """The regression that matters most.

    Judging Manhattan snapping per wall pulled a room whose real corners were 83 and 97
    degrees onto a perfect rectangle, because the fitted frame is a compromise and each wall
    sits only half the skew away from it. Orthogonality is a property of the room, so the
    whole room now has to qualify or nothing snaps.
    """
    r = make_room(shear=0.12, noise_m=0.01, seed=0)      # ~6.8 degrees off square
    geo = solve(r)
    got, truth = corner_angles(geo.corners), np.sort(r.corner_angles_deg)
    worst = float(np.max(np.abs(got - truth)))
    check("6.8deg skew reported, not squared", worst < 1.0,
          f"truth {np.round(truth, 1)} got {np.round(got, 1)}")
    check("skewed room wall lengths still accurate", wall_err_pct(geo, r) < 0.5)

    r2 = make_room(shear=0.25, noise_m=0.01, seed=0)     # ~14 degrees
    geo2 = solve(r2)
    worst2 = float(np.max(np.abs(corner_angles(geo2.corners) - np.sort(r2.corner_angles_deg))))
    check("14deg skew reported, not squared", worst2 < 1.0, f"max angle err {worst2:.2f}deg")


def test_ceiling_spread_reported():
    r = make_room(noise_m=0.01, seed=1)
    geo = solve(r)
    check("ceiling spread is finite and small on a flat ceiling",
          np.isfinite(geo.ceiling_height_spread) and geo.ceiling_height_spread < 0.10,
          f"{geo.ceiling_height_spread * 100:.2f}cm")


def test_error_budget_is_driven_by_bias():
    """Wall error should scale with per-surface depth bias, and barely with noise.

    This is the number that decides which depth model is good enough, so it is asserted
    rather than left as a one-off observation.
    """
    lo = wall_err_pct(solve(make_room(noise_m=0.03, surface_bias_m=0.02, seed=0), 0.15),
                      make_room(noise_m=0.03, surface_bias_m=0.02, seed=0))
    hi = wall_err_pct(solve(make_room(noise_m=0.03, surface_bias_m=0.10, seed=0), 0.30),
                      make_room(noise_m=0.03, surface_bias_m=0.10, seed=0))
    check("error grows with bias", hi > lo * 2, f"2cm bias {lo:.2f}% -> 10cm bias {hi:.2f}%")
    check("2cm bias clears the video gate (3%)", lo < 3.0, f"{lo:.2f}%")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    sys.exit(1 if FAILURES else 0)
