"""Build the reproduction bundle: everything a clean machine needs to regenerate every
reported number from raw inputs.

    python scripts/make_reproduction_bundle.py [--out cozmo_reproduction_bundle.zip] [--lite]

Contents:
  benchmark/raw/          sensor logs - photo stills, video clips, the .r3d LiDAR scan
  benchmark/ground_truth/ laser survey (rooms, openings, room mapping)
  benchmark/cache/        content-hashed depth cache; replays deterministically, and
                          `--no-cache` still runs the live path (see README)
  BUNDLE_README.md        this bundle's own map: what regenerates what, one command each

`--lite` drops benchmark/raw/video (the two walkthrough clips, ~650 MB) - keep it for a
fast share when the video-tier numbers are not being re-checked.

The team-supplied LiDAR scans referenced in technical report section 9
(`single_room`, `single_scan_with_ceiling`, `single_scan_floor_only`) are large Stray
Scanner captures that live outside this repo; point `run.py --tier lidar` at their
directories directly. They are not bundled here.
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INCLUDE_DIRS = [
    "benchmark/raw/photo",
    "benchmark/raw/video",
    "benchmark/raw/lidar",
    "benchmark/ground_truth",
    "benchmark/cache",
]
INCLUDE_FILES = [
    "benchmark/raw/capture_info.txt",
    "README.md",
    "docs/REPRODUCTION.md",
    "docs/COMPLIANCE_MATRIX.md",
    "docs/DEVICE_MATRIX.md",
    "docs/CAPTURE_PROTOCOL.md",
    "schemas/output.schema.json",
]

BUNDLE_README = """# Cozmo reproduction bundle

Unzip **inside a clone of the repo** (it drops `benchmark/raw/`, `benchmark/ground_truth/`
and `benchmark/cache/` into place), then follow `docs/REPRODUCTION.md`.

    python -m venv .venv && . .venv/bin/activate     # Windows: .venv\\Scripts\\activate
    pip install -r requirements.txt
    python scripts/fetch_weights.py                  # one-time; then fully offline

One command per capture:

    python run.py benchmark/raw/photo              --tier photo --out out_photo
    python run.py benchmark/raw/video/IMG_0460.MOV --tier video --out out_video
    python run.py benchmark/raw/video/IMG_0462.MOV --tier video --out out_video2
    python run.py benchmark/raw/lidar/*.r3d        --tier lidar --out out_lidar

Regenerate every reported number:

    python benchmark/scripts/report.py --run     # gates at all 3 tiers, timing
    python benchmark/scripts/gates.py            # brief's gates vs the laser survey
    python benchmark/scripts/calibrate.py        # per-tier interval calibration
    python benchmark/scripts/ablation.py         # drift on/off
    python benchmark/scripts/fix_loop_photo.py   # fix-loop before/after
    python tests/test_smoke.py && python tests/test_geometry.py && python tests/test_floorplan.py

The depth cache under `benchmark/cache/` is content-hashed and replays deterministically;
`--no-cache` on any `run.py` call forces the live inference path, which is what the walk-in
test uses.

Full number → command → artifact map: `docs/REPRODUCTION.md`.
Requirement → path → artifact → status: `docs/COMPLIANCE_MATRIX.md`.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "cozmo_reproduction_bundle.zip"))
    ap.add_argument("--lite", action="store_true", help="omit benchmark/raw/video (~650 MB)")
    args = ap.parse_args()

    dirs = [d for d in INCLUDE_DIRS if not (args.lite and d == "benchmark/raw/video")]
    n, total = 0, 0
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("BUNDLE_README.md", BUNDLE_README)
        for rel in INCLUDE_FILES:
            p = os.path.join(ROOT, rel)
            if os.path.isfile(p):
                z.write(p, rel)
                n += 1
        for d in dirs:
            base = os.path.join(ROOT, d)
            if not os.path.isdir(base):
                print(f"  skip (missing): {d}")
                continue
            for root, _, files in os.walk(base):
                for f in files:
                    fp = os.path.join(root, f)
                    z.write(fp, os.path.relpath(fp, ROOT))
                    n += 1
                    total += os.path.getsize(fp)
            print(f"  added {d}")
    size_mb = os.path.getsize(args.out) / 1e6
    print(f"\nwrote {args.out}  ({n} files, {total/1e6:.0f} MB raw -> {size_mb:.0f} MB zip)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
