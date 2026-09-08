"""Damage stage against a synthetic wall with damage painted on at known size and height.

Run: python tests/test_damage.py     (no pytest dependency needed)

THE HONEST LIMIT OF THIS FILE, stated the same way `tests/test_geometry.py` stated it for
the single-room pipeline before any real capture existed: no staged damage has been captured.
`benchmark/ground_truth/damage.csv` is still placeholders, so the thresholds exercised here
are the module's UNFITTED DEFAULTS, not calibrated ones. What these cases pin down is the
part that does not need calibration: that a colour anomaly on a wall is turned into an extent
in metres and a height above the floor using the same wall frame `openings.py` uses, that the
two damage classes come out distinct, that the concealed-damage rules fire on the right
geometry and name themselves, and that a scope line item never re-estimates a quantity the
geometry already produced.

Every synthetic case below is built in a z-up world frame, matching `tests/synthetic_room.py`.
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from pipeline.damage import detect as detect_mod                 # noqa: E402
from pipeline.damage.detect import DamageRegion, detect_damage_on_wall   # noqa: E402
from pipeline.damage.rules import evaluate                       # noqa: E402
from pipeline.damage.scope import line_items                     # noqa: E402
from pipeline.geometry.planes import Plane                       # noqa: E402

FAILURES: list[str] = []

WORLD_UP = np.array([0.0, 0.0, 1.0])

WALL_W = 3.00
WALL_H = 2.60
BASE_RGB = np.array([200.0, 195.0, 185.0])      # a beige wall - not white, on purpose: a
                                               # fixed RGB threshold could not tell this from
                                               # a real stain, which is why the detector uses
                                               # the wall's OWN colour distribution
STAIN_RGB = np.array([110.0, 90.0, 70.0])       # brown water staining
CRACK_RGB = np.array([30.0, 30.0, 30.0])        # a dark hairline

# Staged damage, in wall coordinates (x along the wall, z up from the floor).
STAIN_CX, STAIN_CZ = 0.60, 0.30                 # centre 0.30 m above the floor -> inside the
STAIN_RX, STAIN_RZ = 0.20, 0.175               # 0.5 m band CONCEAL-WATER-02 watches
CRACK_X = 2.40
CRACK_HALF_W = 0.025                            # 0.05 m wide
CRACK_Z0 = 1.75                                 # runs from here up to the ceiling junction


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def _make_wall(paint_damage: bool, seed: int = 0):
    """A single wall at y=0 (normal +y), as an oriented point cloud plus the one frame's
    image and the per-point pixel mapping `detect_damage_on_wall` consumes.

    Returns (wall_plane, floor_plane, points, pix, image, gravity).
    """
    rng = np.random.default_rng(seed)

    nx, nz = 240, 210
    xs = np.linspace(0.0, WALL_W, nx)
    zs = np.linspace(0.0, WALL_H, nz)
    gx, gz = np.meshgrid(xs, zs, indexing="ij")
    wall_pts = np.stack([gx.ravel(), np.zeros(gx.size), gz.ravel()], axis=1)

    # A floor patch so the detector has something to measure height above.
    fx = np.linspace(0.0, WALL_W, 60)
    fy = np.linspace(0.0, 2.0, 40)
    fgx, fgy = np.meshgrid(fx, fy, indexing="ij")
    floor_pts = np.stack([fgx.ravel(), fgy.ravel(), np.zeros(fgx.size)], axis=1)

    points = np.vstack([wall_pts, floor_pts])
    wall_idx = np.arange(len(wall_pts))
    floor_idx = np.arange(len(wall_pts), len(points))

    img_h, img_w = 520, 600

    def world_to_px(x, z):
        col = np.rint(x / WALL_W * (img_w - 1)).astype(int)
        row = np.rint((WALL_H - z) / WALL_H * (img_h - 1)).astype(int)
        return row, col

    # Per-point pixel mapping. Floor points are off-image and get dropped by the detector's
    # own bounds check - exactly the fused-cloud case its `valid` mask exists for.
    pix = np.zeros((len(points), 2), dtype=int)
    r, c = world_to_px(wall_pts[:, 0], wall_pts[:, 2])
    pix[wall_idx, 0] = np.clip(r, 0, img_h - 1)
    pix[wall_idx, 1] = np.clip(c, 0, img_w - 1)
    pix[floor_idx] = -1

    # Build the image in wall coordinates, then add mild sensor noise so the MAD floor in the
    # detector is doing real work rather than dividing by zero.
    px_col, px_row = np.meshgrid(np.arange(img_w), np.arange(img_h))
    wx = px_col / (img_w - 1) * WALL_W
    wz = WALL_H - px_row / (img_h - 1) * WALL_H

    image = np.tile(BASE_RGB, (img_h, img_w, 1))
    if paint_damage:
        stain = (((wx - STAIN_CX) / STAIN_RX) ** 2 + ((wz - STAIN_CZ) / STAIN_RZ) ** 2) <= 1.0
        crack = (np.abs(wx - CRACK_X) <= CRACK_HALF_W) & (wz >= CRACK_Z0)
        image[stain] = STAIN_RGB
        image[crack] = CRACK_RGB
    image = image + rng.normal(0.0, 2.0, size=image.shape)
    image = np.clip(image, 0, 255)

    wall = Plane(normal=np.array([0.0, 1.0, 0.0]), d=0.0, inliers=wall_idx, kind="wall")
    floor = Plane(normal=np.array([0.0, 0.0, 1.0]), d=0.0, inliers=floor_idx, kind="floor")
    return wall, floor, points, pix, image, WORLD_UP


def _detect(paint_damage: bool):
    wall, floor, points, pix, image, g = _make_wall(paint_damage)
    return detect_damage_on_wall(wall, "w0", "wall", points, pix, image, g, floor)


def test_clean_wall_reports_nothing():
    """The threshold floor has to survive ordinary paint and sensor noise with no damage."""
    regions = _detect(paint_damage=False)
    check("clean wall -> no damage regions", regions == [], f"{len(regions)} found")


def test_two_classes_come_out_distinct():
    regions = _detect(paint_damage=True)
    classes = sorted(r.damage_class for r in regions)
    stains = [r for r in regions if r.damage_class == "water_stain"]
    cracks = [r for r in regions if r.damage_class == "crack"]
    check("both damage classes detected", "crack" in classes and "water_stain" in classes,
          str(classes))
    check("exactly one crack", len(cracks) == 1, f"{len(cracks)}")
    check("exactly one water_stain", len(stains) == 1, f"{len(stains)}")


def test_extent_and_height_are_metric():
    regions = _detect(paint_damage=True)
    stain = next(r for r in regions if r.damage_class == "water_stain")
    crack = next(r for r in regions if r.damage_class == "crack")

    # Painted ellipse: 0.40 m wide, 0.35 m tall. Raster is 0.05 m, so a cell of slack each way.
    check("stain long axis ~ 0.40 m", abs(stain.long_axis_m - 0.40) <= 0.08,
          f"{stain.long_axis_m:.3f} m")
    check("stain short axis ~ 0.35 m", abs(stain.short_axis_m - 0.35) <= 0.08,
          f"{stain.short_axis_m:.3f} m")
    check("stain sits ~0.30 m above the floor", abs(stain.height_above_floor_m - 0.30) <= 0.10,
          f"{stain.height_above_floor_m:.3f} m")
    check("stain area is long*short order of magnitude",
          0.06 <= stain.area_m2 <= 0.16, f"{stain.area_m2:.3f} m2")

    # Painted crack: 0.05 m wide, from z=1.75 up to the ceiling at 2.60 -> 0.85 m long.
    check("crack long axis ~ 0.85 m", abs(crack.long_axis_m - 0.85) <= 0.12,
          f"{crack.long_axis_m:.3f} m")
    check("crack aspect ratio is high", crack.long_axis_m / crack.short_axis_m >= 4.0,
          f"{crack.long_axis_m / crack.short_axis_m:.1f}")
    check("crack flagged as reaching the wall-ceiling junction", crack.crosses_junction)


def test_every_detection_labels_its_thresholds_unfitted():
    regions = _detect(paint_damage=True)
    check("each region carries the 'unfitted defaults' caveat in notes",
          regions and all(any("unfitted" in n for n in r.notes) for r in regions))


def test_rules_fire_on_the_right_geometry_and_name_themselves():
    regions = _detect(paint_damage=True)
    stain = next(r for r in regions if r.damage_class == "water_stain")
    crack = next(r for r in regions if r.damage_class == "crack")

    fs = evaluate(stain, "wall")
    check("low wall stain -> CONCEAL-WATER-02", fs.get("raised") and fs["rule_id"] == "CONCEAL-WATER-02",
          str(fs))
    fc = evaluate(crack, "wall")
    check("junction crack -> CONCEAL-CRACK-01", fc.get("raised") and fc["rule_id"] == "CONCEAL-CRACK-01",
          str(fc))
    check("a fired flag carries evidence text", "evidence" in fc and "m" in fc["evidence"])

    # Same detected stain, re-evaluated against a corrected surface classification - no
    # re-detection. On a ceiling it is CONCEAL-WATER-01 instead.
    fceil = evaluate(stain, "ceiling")
    check("same stain on a ceiling -> CONCEAL-WATER-01",
          fceil.get("raised") and fceil["rule_id"] == "CONCEAL-WATER-01", str(fceil))

    # A stain high on a wall matches no rule - and that is an answer, not an error.
    high = DamageRegion(id="x", damage_class="water_stain", surface_id="w0", surface_type="wall",
                        long_axis_m=0.3, short_axis_m=0.25, area_m2=0.06,
                        height_above_floor_m=1.8, confidence=0.5)
    check("high wall stain -> no rule, stated as {'raised': False}",
          evaluate(high, "wall") == {"raised": False})


def test_scope_items_inherit_the_measured_quantity():
    regions = _detect(paint_damage=True)
    items = line_items(regions)
    check("one scope item per detected region", len(items) == len(regions), f"{len(items)}")

    by_dmg = {it["damage_id"]: it for it in items}
    for r in regions:
        it = by_dmg[r.id]
        if r.damage_class == "crack":
            check(f"{r.id}: crack priced by length",
                  it["quantity"]["basis"] == "long_axis_m" and it["quantity"]["unit"] == "m"
                  and it["quantity"]["value"] == round(r.long_axis_m, 4), str(it["quantity"]))
        else:
            check(f"{r.id}: stain priced by area",
                  it["quantity"]["basis"] == "area_m2" and it["quantity"]["unit"] == "m2"
                  and it["quantity"]["value"] == round(r.area_m2, 4), str(it["quantity"]))


def test_scope_skips_an_undefined_combination_rather_than_inventing_one():
    floor_dmg = DamageRegion(id="f0_dmg0", damage_class="water_stain", surface_id="f0",
                             surface_type="floor", long_axis_m=0.4, short_axis_m=0.3,
                             area_m2=0.1, height_above_floor_m=0.0, confidence=0.5)
    check("(water_stain, floor) has no SCOPE_TABLE entry -> skipped, not defaulted",
          line_items([floor_dmg]) == [])


def test_detect_wrapper_is_explicitly_not_wired_in():
    """The `detect(frame, surfaces)` entry point is deliberately unimplemented until a
    staged-damage capture exists to validate the measure.py integration against. That is a
    stated gap, and the error says so rather than failing silently."""
    raised = False
    try:
        detect_mod.detect(object(), [])
    except NotImplementedError as e:
        raised = "detect_damage_on_wall" in str(e)
    check("detect() raises NotImplementedError pointing at the real entry point", raised)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    sys.exit(1 if FAILURES else 0)
