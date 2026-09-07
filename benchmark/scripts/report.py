"""Consolidated benchmark report across whatever tiers have been run.

    python benchmark/scripts/report.py                      # read existing out_*/result.json
    python benchmark/scripts/report.py --run                 # run every tier first, then report

Reads the `result.json` each tier writes and tabulates ceiling error, wall-length error,
opening width/height error, processing time and repeatability, against ground truth found
beside the raw captures.

It reports only what it can verify. A metric with no ground truth is printed as "-", never
filled with a plausible number, and a tier that has not been run is listed as absent rather
than omitted - a missing row and a failing row must not look the same.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# Tape, Room 1 and Hallway of the 3BHK; assumed uniform across that flat (both measured
# rooms agree exactly). The .r3d is a different property with its own measurements file.
PHOTO_VIDEO_CEILING_M = 2.64

TIERS = {
    "photo": {"out": "out_photo", "input": "benchmark/raw/photo"},
    "video": {"out": "out_video", "input": "benchmark/raw/video"},
    "lidar": {"out": "out_lidar", "input": "benchmark/raw/lidar"},
}


def read_measurements(d: str) -> dict:
    for name in ("measurements.md", "measurements.txt", "ground_truth.md"):
        p = os.path.join(ROOT, d, name)
        if not os.path.isfile(p):
            continue
        txt = open(p, encoding="utf-8", errors="replace").read().lower()
        out = {}
        for key, pat in {
            "height_m": r"(?:room\s*)?height\D{0,12}([\d.]+)\s*m",
            "breadth_m": r"(?:bredth|breadth|width)\D{0,12}([\d.]+)\s*m",
            "length_m": r"length\D{0,12}([\d.]+)\s*m",
            "window_m": r"window\D{0,12}([\d.]+)\s*m",
            "door_m": r"door\D{0,12}([\d.]+)\s*m",
        }.items():
            m = re.search(pat, txt)
            if m:
                out[key] = float(m.group(1))
        return out
    return {}


def pct(got, truth):
    if got is None or truth is None or not np.isfinite(got) or truth == 0:
        return None
    return (got - truth) / truth * 100


def fmt(v, unit="%"):
    return "-" if v is None else (f"{v:+.1f}{unit}" if unit == "%" else f"{v:.3f}{unit}")


def load_result(tier: str) -> dict | None:
    p = os.path.join(ROOT, TIERS[tier]["out"], "result.json")
    if not os.path.isfile(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="run each tier before reporting")
    args = ap.parse_args()

    if args.run:
        for tier, cfg in TIERS.items():
            src = os.path.join(ROOT, cfg["input"])
            if not os.path.exists(src):
                print(f"skip {tier}: no {cfg['input']}")
                continue
            print(f"\n=== running {tier} ===")
            subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), src,
                            "--tier", tier, "--out", os.path.join(ROOT, cfg["out"])],
                           check=False)

    lines = ["# Benchmark report", "",
             "Regenerate: `python benchmark/scripts/report.py --run`", "",
             "Ground truth is tape. A metric with no ground truth reads `-` and is never",
             "filled with an estimate. A tier that has not been run says so.", ""]

    lines += ["## Ceiling height", "",
              "| Tier | Room | Measured | Truth | Error | Mode | Frames | Abstained |",
              "|---|---|---|---|---|---|---|---|"]
    timings = {}
    for tier in TIERS:
        res = load_result(tier)
        if res is None:
            lines.append(f"| {tier} | *not run* | - | - | - | - | - | - |")
            continue
        timings[tier] = res.get("timing_seconds")
        truth = (read_measurements(TIERS[tier]["input"]).get("height_m")
                 if tier == "lidar" else PHOTO_VIDEO_CEILING_M)
        for r in res.get("rooms", []):
            ch = r.get("ceiling_height")
            lines.append(
                f"| {tier} | {r.get('room_id','?')} | "
                f"{fmt(ch, 'm') if ch else 'abstained'} | {truth or '-'} | "
                f"{fmt(pct(ch, truth))} | {r.get('mode','-')} | "
                f"{r.get('n_frames','-')} | {r.get('abstained_frames', 0)} |")
    lines.append("")

    lines += ["## Wall dimensions (opposite-pair separation)", "",
              "| Tier | Room | Pair | Separation | Nearest truth | Error |",
              "|---|---|---|---|---|---|"]
    any_wall = False
    for tier in TIERS:
        res = load_result(tier)
        if res is None:
            continue
        gt = read_measurements(TIERS[tier]["input"])
        dims = sorted(d for d in (gt.get("breadth_m"), gt.get("length_m")) if d)
        for r in res.get("rooms", []):
            for i, pr in enumerate(r.get("wall_pairs", [])[:3]):
                sep = pr["separation_m"]
                near = min(dims, key=lambda d: abs(d - sep)) if dims else None
                lines.append(f"| {tier} | {r.get('room_id','?')} | {i + 1} | {sep:.3f}m | "
                             f"{near if near else '-'} | {fmt(pct(sep, near))} |")
                any_wall = True
    if not any_wall:
        lines.append("| - | - | - | - | - | - |")
    lines.append("")

    lines += ["## Openings", "",
              "| Tier | Room | Kind | Width | Height | Sill | Confidence |",
              "|---|---|---|---|---|---|---|"]
    any_open = False
    for tier in TIERS:
        res = load_result(tier)
        if res is None:
            continue
        for r in res.get("rooms", []):
            for o in r.get("openings", [])[:6]:
                lines.append(f"| {tier} | {r.get('room_id','?')} | {o['kind']} | "
                             f"{o['width_m']:.2f}m | {o['height_m']:.2f}m | "
                             f"{o['sill_height_m']:.2f}m | {o['confidence']:.2f} |")
                any_open = True
    if not any_open:
        lines.append("| - | - | - | - | - | - | - |")
    lines.append("")

    lines += ["## Repeatability", "",
              "Within-room spread across frames, for tiers measuring per frame. This is the",
              "closest thing we have to the brief's repeatability gate until a second capture",
              "of the same room exists - it measures frame-to-frame agreement, NOT the",
              "capture-to-capture agreement the gate actually asks for.", "",
              "| Tier | Room | Frames reported | Ceiling SD |",
              "|---|---|---|---|"]
    any_rep = False
    for tier in TIERS:
        res = load_result(tier)
        if res is None:
            continue
        for r in res.get("rooms", []):
            sd = r.get("ceiling_height_sd")
            if sd is None:
                continue
            lines.append(f"| {tier} | {r.get('room_id','?')} | "
                         f"{r.get('reported_frames','-')} | {sd * 100:.1f} cm |")
            any_rep = True
    if not any_rep:
        lines.append("| - | - | - | - |")
    lines.append("")

    lines += ["## Processing time", "", "| Tier | Seconds | Notes |", "|---|---|---|"]
    for tier in TIERS:
        t = timings.get(tier)
        note = ("CPU depth inference dominates" if tier in ("photo", "video")
                else "no depth inference; sensor supplies it")
        lines.append(f"| {tier} | {t if t is not None else '-'} | {note} |")
    lines += ["", "All timings are CPU-only (no CUDA build installed). The depth model is",
              "~13 s/frame on CPU, which is the bulk of the photo and video numbers.", ""]

    out = os.path.join(ROOT, "benchmark", "results", "benchmark_report.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
