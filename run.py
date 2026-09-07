"""One command per capture.

    python run.py capture/ --tier lidar --out out/

Tier is auto-detected from folder contents unless given. Everything below the capture layer
is tier-agnostic by design.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SCHEMA_VERSION = "0.1"


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


def detect_tier(capture_dir: str) -> str:
    names = []
    for root, _, files in os.walk(capture_dir):
        names.extend(f.lower() for f in files)
    if any(n.endswith(".r3d") for n in names):
        return "lidar"
    if any(n.endswith((".mov", ".mp4")) for n in names):
        return "video"
    return "photo"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture_dir")
    ap.add_argument("--tier", choices=["photo", "video", "lidar"], default=None)
    ap.add_argument("--out", default="out")
    ap.add_argument("--no-drift-correction", action="store_true",
                    help="ablation: compose room poses as-is, no pose graph")
    args = ap.parse_args()

    tier = args.tier or detect_tier(args.capture_dir)
    os.makedirs(args.out, exist_ok=True)

    result = {
        "schema_version": SCHEMA_VERSION,
        "capture": {
            "tier": tier,
            "device": "unknown",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_commit": git_commit(),
            "scale_source": "none",
            "drift_correction": not args.no_drift_correction,
        },
        "property": {"rooms": [], "connections": [], "footprint_area": None},
        "rooms": [],
    }

    # Pipeline stages land here in order. Each one is committed separately.
    #   scene   = capture.load(args.capture_dir)
    #   planes  = geometry.planes.fit_floor_ceiling(...)
    #   walls   = geometry.walls.extract_walls(...)
    #   opens   = geometry.openings.detect_openings(...)
    #   dmg     = damage.detect.detect(...)
    #   plan    = stitching.stitch.stitch(rooms, drift_correction=...)
    #   render  = output.render.render_plan(...)

    with open(os.path.join(args.out, "result.json"), "w") as fh:
        json.dump(result, fh, indent=2)

    print(f"tier={tier} commit={result['capture']['pipeline_commit']}")
    print(f"wrote {os.path.join(args.out, 'result.json')}")
    print("STAGES NOT BUILT: geometry, stitching, damage, render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
