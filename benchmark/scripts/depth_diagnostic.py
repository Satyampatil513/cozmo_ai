"""Per-frame depth sanity check: what does each photo imply about the camera's own height?

    python benchmark/scripts/depth_diagnostic.py benchmark/raw/photo

The trick this exploits: the distance from the camera to the floor plane is a quantity we
independently know. A person holding a phone at chest height is about 1.4-1.5 m above the
floor, and that does not change between rooms. So the camera-to-floor distance the depth
model implies is a free, ground-truth-free probe of its scale error - available on every
frame, before any measurement is reported and before any tape comes out.

It is also directly actionable. If a frame says the camera was 2.4 m above the floor, the
depth field for that frame is roughly 1.7x too deep, and every dimension derived from it is
wrong by that factor. Dividing by the ratio is the floor-plane scale anchor the capture
protocol asks one number for.

Read the columns as: cam->floor should be near-constant across every frame of every room.
Where it is not, the depth model, not the room, is what changed.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
warnings.filterwarnings("ignore")

from pipeline.capture.depth import get_backend          # noqa: E402
from pipeline.capture.photo import load_frame           # noqa: E402
from pipeline.geometry.lift import lift                 # noqa: E402
from pipeline.geometry.planes import fit_floor_ceiling  # noqa: E402
from pipeline.geometry.walls import ceiling_height      # noqa: E402

PHOTO_EXT = ("*.jpg", "*.jpeg", "*.png", "*.heic")

# A handheld phone is somewhere in this band above the floor. Outside it, the frame's scale
# is wrong or its floor plane is not the floor.
PLAUSIBLE_CAM_H = (1.0, 1.8)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture_dir")
    ap.add_argument("--backend", default="metric3d_v2",
                    help="metric3d_v2 (default, uses intrinsics) or "
                         "depth_anything_v2_metric_indoor (baseline)")
    ap.add_argument("--stride", type=int, default=2)
    args = ap.parse_args()

    be = get_backend(args.backend)
    t0 = time.time()

    print(f"\n{'room':<12} {'frame':<16} {'cam->floor':>11} {'ceiling':>10} {'spread':>8}  flag")
    print("-" * 70)

    per_room: dict[str, list[tuple[float, float]]] = {}
    for d in sorted(p for p in glob.glob(os.path.join(args.capture_dir, "*")) if os.path.isdir(p)):
        room = os.path.basename(d)
        files: list[str] = []
        for pat in PHOTO_EXT:
            files += glob.glob(os.path.join(d, pat))
        rows = []
        for path in sorted(files):
            f = load_frame(path)
            depth = be.infer(f.image, f.K).depth
            pts, nrm = lift(depth, f.K, stride=args.stride)
            got = fit_floor_ceiling(pts, nrm, threshold=0.05)
            if got is None:
                print(f"{room:<12} {os.path.basename(path):<16} {'no planes':>11}")
                continue
            _, floor, ceil, _walls, score = got
            if floor is None:
                print(f"{room:<12} {os.path.basename(path):<16} {'no floor':>11}")
                continue
            if ceil is None:
                print(f"{room:<12} {os.path.basename(path):<16} "
                      f"{'ABSTAIN':>11}   (selection score {score:.4f})")
                continue
            cam_h = abs(floor.d)          # camera sits at the origin, so |d| is its height
            h, sp = ceiling_height(floor, ceil, pts)
            flag = "" if PLAUSIBLE_CAM_H[0] <= cam_h <= PLAUSIBLE_CAM_H[1] else "<-- implausible"
            print(f"{room:<12} {os.path.basename(path):<16} {cam_h:10.3f}m {h:9.3f}m "
                  f"{sp * 100:7.0f}cm  {flag}")
            rows.append((cam_h, h))
        if rows:
            per_room[room] = rows

    print(f"\n({time.time() - t0:.0f}s)\n")
    print(f"{'room':<12} {'cam height':>22} {'ceiling':>22} {'implied scale err':>18}")
    print("-" * 78)
    for room, rows in per_room.items():
        ch = np.array([r[0] for r in rows])
        ce = np.array([r[1] for r in rows if np.isfinite(r[1])])
        # If a person really was ~1.45 m up, this is how far off that frame's depth field is.
        err = ch.mean() / 1.45
        print(f"{room:<12} {ch.mean():10.3f} +- {ch.std():.3f} m "
              f"{ce.mean():12.3f} +- {ce.std():.3f} m {err:16.2f}x")

    print("\ncam->floor should be near-constant everywhere. Where it is not, the depth model "
          "changed, not the room.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
