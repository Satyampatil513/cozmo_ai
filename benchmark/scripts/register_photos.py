"""Register a room's photos into one frame, verify it, then measure fused vs per-frame.

    python benchmark/scripts/register_photos.py --room "Room 2"
    python benchmark/scripts/register_photos.py                # all rooms

The verification is the point. Registration that "ran" tells you nothing; a fused cloud built
on wrong poses still looks like a reconstruction. Three checks, none needing ground truth:

  pair scale        how much each frame's depth scale disagrees with its neighbour. Our error
                    budget says per-frame depth scale dominates, and this measures it directly.
  world residual    the same feature, seen in two frames, transformed into the common frame by
                    each frame's pose. The distance between the two estimates is how wrong the
                    alignment is, in centimetres.
  correspondence    the visual version: matched features drawn side by side with matching
  overlay           numbers, so "corner 7 in photo A is corner 7 in photo B" is checkable by
                    eye rather than asserted.

Then the measurement that matters: ceiling height per frame, versus ceiling height from the
fused cloud, against tape.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from pipeline.capture.depth import get_backend                    # noqa: E402
from pipeline.capture.depth_cache import infer_cached             # noqa: E402
from pipeline.capture.photo import WORK_PX, load                  # noqa: E402
from pipeline.capture.register import match_all, register         # noqa: E402
from pipeline.geometry.fuse import fuse_frames                    # noqa: E402
from pipeline.geometry.lift import lift                           # noqa: E402
from pipeline.geometry.planes import CAMERA_UP, fit_floor_ceiling  # noqa: E402
from pipeline.geometry.walls import ceiling_height, wall_pair_dimensions  # noqa: E402

TRUE_CEILING_M = 2.64
CAPTURE = os.path.join(ROOT, "benchmark", "raw", "photo")
OUT_DIR = os.path.join(ROOT, "benchmark", "results")
DEBUG_DIR = os.path.join(ROOT, "out_register", "debug")


def correspondence_overlay(fa, fb, px_a, px_b, out_path: str, max_pts: int = 30) -> str:
    """Two photos side by side with matched features numbered identically in both.

    The direct answer to "is corner 7 in photo A the same corner 7 in photo B". Points are
    thinned to keep the numbers legible - all 400 inliers drawn would be an unreadable smear
    and would prove nothing a sample does not.
    """
    a = cv2.cvtColor(fa.image, cv2.COLOR_RGB2BGR)
    b = cv2.cvtColor(fb.image, cv2.COLOR_RGB2BGR)
    h = max(a.shape[0], b.shape[0])
    a = cv2.copyMakeBorder(a, 0, h - a.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(20, 20, 20))
    b = cv2.copyMakeBorder(b, 0, h - b.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(20, 20, 20))
    canvas = np.hstack([a, np.full((h, 6, 3), 30, np.uint8), b])
    off = a.shape[1] + 6

    step = max(1, len(px_a) // max_pts)
    sel = list(range(0, len(px_a), step))[:max_pts]
    for n, k in enumerate(sel, 1):
        col = tuple(int(c) for c in cv2.applyColorMap(
            np.array([[(n * 23) % 256]], np.uint8), cv2.COLORMAP_HSV)[0, 0])
        pa = (int(px_a[k][0]), int(px_a[k][1]))
        pb = (int(px_b[k][0]) + off, int(px_b[k][1]))
        for p in (pa, pb):
            cv2.circle(canvas, p, 7, col, 2)
            cv2.putText(canvas, str(n), (p[0] + 9, p[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(canvas, str(n), (p[0] + 9, p[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
        cv2.line(canvas, pa, pb, col, 1, cv2.LINE_AA)

    scale = 1700 / canvas.shape[1]
    canvas = cv2.resize(canvas, (1700, int(canvas.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, canvas)
    return out_path


def measure(points, normals, prior, cam_origin):
    got = fit_floor_ceiling(points, normals, threshold=0.05,
                            gravity_prior=prior, camera_at_origin=cam_origin)
    if got is None:
        return None
    g, floor, ceil, walls, score = got
    out = {"score": round(float(score), 4), "n_walls": len(walls),
           "ceiling_m": None, "gravity": np.round(g, 3).tolist()}
    if floor is not None and ceil is not None:
        h, sp = ceiling_height(floor, ceil, points)
        if np.isfinite(h):
            out["ceiling_m"] = round(float(h), 4)
            out["spread_cm"] = round(float(sp) * 100, 1)
    out["wall_pairs"] = [p["separation_m"] for p in wall_pair_dimensions(walls, g, points)[:3]]
    return out


def run_room(room_id: str, frames, backend, debug: bool) -> dict:
    t0 = time.time()
    for f in frames:
        if f.depth is None:
            f.depth, _ = infer_cached(backend, f.image_path, f.image, f.K, WORK_PX, True)

    pairs = match_all(frames)
    reg = register(frames, pairs)

    rec: dict = {
        "room": room_id, "n_frames": len(frames),
        "registered": reg.n_registered, "reference": reg.reference,
        "residual_median_cm": round(reg.residual_median_m * 100, 2),
        "residual_p90_cm": round(reg.residual_p90_m * 100, 2),
        "per_frame_scale": [round(s, 4) for s in reg.scales],
        "pairs": reg.per_pair,
    }

    # Per-frame measurement, unchanged, for the comparison.
    per = []
    for f in frames:
        pts, nrm = lift(f.depth, f.K, stride=2)
        m = measure(pts, nrm, CAMERA_UP, True)
        if m and m["ceiling_m"]:
            per.append(m["ceiling_m"])
    rec["per_frame_ceilings"] = per
    if per:
        a = np.array(per)
        rec["per_frame_mean"] = round(float(a.mean()), 4)
        rec["per_frame_sd"] = round(float(a.std()), 4)
        rec["per_frame_err_pct"] = round((a.mean() - TRUE_CEILING_M) / TRUE_CEILING_M * 100, 2)

    # Fused measurement, using the poses we just solved for.
    posed = []
    for f, T in zip(frames, reg.poses):
        if T is None:
            continue
        f.T_wc = T
        posed.append(f)
    rec["posed_frames"] = len(posed)
    if len(posed) >= 3:
        fc = fuse_frames(posed, stride=2)
        rec["fused_points"] = int(len(fc.points))
        # The world frame here is the reference camera's frame, so "up" is still camera -y.
        m = measure(fc.points, fc.normals, np.array([0.0, -1.0, 0.0]), False)
        if m:
            rec["fused"] = m
            if m["ceiling_m"]:
                rec["fused_err_pct"] = round(
                    (m["ceiling_m"] - TRUE_CEILING_M) / TRUE_CEILING_M * 100, 2)

    if debug:
        best = max((p for p in pairs if p.ok), key=lambda p: p.n_inliers, default=None)
        if best is not None:
            rec["overlay"] = correspondence_overlay(
                frames[best.i], frames[best.j], best.px_i, best.px_j,
                os.path.join(DEBUG_DIR, f"{room_id}_match_{best.i}_{best.j}.png"))

    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", default=CAPTURE)
    ap.add_argument("--room", default=None)
    ap.add_argument("--no-debug", action="store_true")
    args = ap.parse_args()

    scene = load(args.capture)
    backend = get_backend("metric3d_v2")

    records = []
    for room in scene.rooms:
        if args.room and room.room_id != args.room:
            continue
        print(f"\n=== {room.room_id} ({len(room.frames)} frames) ===")
        rec = run_room(room.room_id, room.frames, backend, not args.no_debug)
        records.append(rec)
        print(f"  registered {rec['registered']}/{rec['n_frames']} "
              f"(ref {rec['reference']})   {rec['seconds']}s")
        print(f"  world residual: median {rec['residual_median_cm']} cm, "
              f"p90 {rec['residual_p90_cm']} cm")
        sc = rec["per_frame_scale"]
        print(f"  per-frame depth scale vs reference: "
              f"{min(sc):.3f} .. {max(sc):.3f}  (1.0 = agrees)")
        if "per_frame_mean" in rec:
            print(f"  per-frame ceiling {rec['per_frame_mean']:.3f} +- {rec['per_frame_sd']:.3f} m"
                  f"   err {rec['per_frame_err_pct']:+.1f}%")
        if "fused" in rec and rec["fused"].get("ceiling_m"):
            print(f"  FUSED     ceiling {rec['fused']['ceiling_m']:.3f} m"
                  f"   err {rec.get('fused_err_pct'):+.1f}%"
                  f"   walls {rec['fused']['n_walls']}"
                  f"   pairs {[round(x,2) for x in rec['fused']['wall_pairs']]}")
        elif "fused" in rec:
            print("  FUSED     abstained")
        if rec.get("overlay"):
            print(f"  correspondence overlay: {rec['overlay']}")

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "register_photos.json")
    with open(out, "w") as fh:
        json.dump({"ground_truth_ceiling_m": TRUE_CEILING_M, "rooms": records}, fh, indent=2)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
