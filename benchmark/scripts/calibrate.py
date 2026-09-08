"""Calibration: is a nominal 95% interval actually a 95% interval, at every tier.

    python benchmark/scripts/calibrate.py

The brief scores calibration at every tier ("calibration is scored at every tier and
confident garbage on thin input caps your total score"). This script:

  1. collects every (measured, truth) pair we have - photo-tier ceiling heights and wall
     spans against the laser survey, plus the LiDAR room's ceiling against its own tape;
  2. scores the CURRENT `pipeline/confidence/intervals.ERROR_MODEL` - the fraction of truths
     that land inside the interval it produces, at nominal 50 / 80 / 95%;
  3. refits (rel_sigma, abs_sigma) per tier on those same residuals, so the fitted numbers
     that belong in `intervals.py` are printed rather than guessed.

Data is thin (5 photo rooms, 1 LiDAR room), stated on the row. Writes
benchmark/results/calibration.md.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ground_truth import load_mapping, load_rooms                       # noqa: E402
from pipeline.confidence.calibrate import coverage, fit_error_model, residuals  # noqa: E402
from pipeline.confidence.intervals import ERROR_MODEL, length_measurement  # noqa: E402
from pipeline.types import Scale                                        # noqa: E402

CMP_JSON = os.path.join(ROOT, "benchmark", "results", "compare_photo_modes.json")
LIDAR_JSON = os.path.join(ROOT, "out_lidar", "result.json")
LIDAR_CEILING_TRUTH_M = 2.6           # benchmark/raw/lidar/measurements.md, that property's tape
OUT_MD = os.path.join(ROOT, "benchmark", "results", "calibration.md")


def _match(measured: list[float], truth: list[float]) -> list[tuple[float, float]]:
    """Greedy nearest-truth pairing, each truth used once - same rule gates.py uses."""
    pairs, used = [], set()
    for m in sorted(measured, reverse=True):
        cand = [(abs(m - t), i) for i, t in enumerate(truth) if i not in used]
        if not cand:
            break
        _, i = min(cand)
        used.add(i)
        pairs.append((m, truth[i]))
    return pairs


def collect() -> dict[str, dict]:
    """tier -> {'ceiling': [(v,t)...], 'wall': [(v,t)...]}"""
    out = {"photo": {"ceiling": [], "wall": []}, "lidar": {"ceiling": [], "wall": []}}
    rooms = load_rooms()
    mapping = load_mapping()

    if os.path.isfile(CMP_JSON):
        cmp = json.load(open(CMP_JSON))
        for room in cmp["rooms"]:
            gt_id = mapping.get(room["room"])
            rt = rooms.get(gt_id) if gt_id else None
            if rt is None:
                continue
            m = room["modes"].get("per_frame", {})        # the shipped default mode
            if m.get("ceiling_m") and rt.ceiling_m:
                out["photo"]["ceiling"].append((m["ceiling_m"], rt.ceiling_m))
            spans = m.get("wall_spans_m") or []
            out["photo"]["wall"] += _match(spans, rt.dimensions)

    if os.path.isfile(LIDAR_JSON):
        lid = json.load(open(LIDAR_JSON))
        for room in lid.get("rooms", []):
            if room.get("ceiling_height"):
                out["lidar"]["ceiling"].append((room["ceiling_height"], LIDAR_CEILING_TRUTH_M))
    return out


def sigma_for(value_m: float, tier: str) -> float:
    """The sigma the current ERROR_MODEL actually produces for this measurement."""
    mm = length_measurement(value_m, tier, Scale(), "calibration probe").to_json()
    lo, hi = mm["interval"]
    return (hi - lo) / 2 / 1.96


def main() -> int:
    data = collect()
    L = ["# Calibration report", "",
         "Regenerate: `python benchmark/scripts/calibrate.py`", "",
         "Does a nominal interval contain the truth as often as it claims. Truth is the laser",
         "survey (`benchmark/ground_truth/`) for the photo tier and the `.r3d` room's own tape",
         "for LiDAR. **Data is thin** - 5 photo rooms, 1 LiDAR room - so these are directional,",
         "not converged; the row says so.", ""]

    all_pairs_by_tier: dict[str, list] = {}
    L += ["## Residuals", "",
          "| Tier | Quantity | n | bias | RMS error | worst |",
          "|---|---|---|---|---|---|"]
    for tier, kinds in data.items():
        tier_pairs = []
        for kind, pairs in kinds.items():
            if not pairs:
                continue
            tier_pairs += pairs
            s = residuals(pairs)
            L.append(f"| {tier} | {kind} | {s['n']} | {s['bias_pct']:+.1f}% "
                     f"| {s['rms_pct']:.1f}% | {s['worst_pct']:.1f}% |")
        all_pairs_by_tier[tier] = tier_pairs

    L += ["", "## Coverage of the current ERROR_MODEL", "",
          "Nominal vs empirical - the fraction of truths inside `measured +/- z*sigma` for the",
          "sigma `intervals.py` produces today. Calibrated means the two columns match.", "",
          "| Tier | n | nominal 50% | nominal 80% | nominal 95% |",
          "|---|---|---|---|---|"]
    for tier, pairs in all_pairs_by_tier.items():
        if not pairs:
            continue
        triples = [(v, t, sigma_for(v, tier)) for v, t in pairs]
        c = coverage(triples)
        L.append(f"| {tier} | {c['n']} | {c['cov_50']:.0%} | {c['cov_80']:.0%} "
                 f"| {c['cov_95']:.0%} |")

    L += ["", "## Refit on these residuals", "",
          "The `(rel_sigma, abs_sigma)` a least-squares fit puts in `intervals.ERROR_MODEL`,",
          "next to what is there now. Replace the placeholders with the fitted column and say",
          "in the report that they were fitted.", "",
          "| Tier | current (rel, abs m) | fitted (rel, abs m) | n |",
          "|---|---|---|---|"]
    for tier, pairs in all_pairs_by_tier.items():
        cur = ERROR_MODEL.get(tier, (0.0, 0.0))
        if len(pairs) < 2:
            L.append(f"| {tier} | ({cur[0]:.3f}, {cur[1]:.3f}) | too few samples to fit "
                     f"| {len(pairs)} |")
            continue
        rel, ab = fit_error_model(pairs)
        L.append(f"| {tier} | ({cur[0]:.3f}, {cur[1]:.3f}) | ({rel:.3f}, {ab:.3f}) "
                 f"| {len(pairs)} |")

    L += ["",
          "**What the coverage says.** Where empirical 95% coverage is far below 95%, the",
          "residual is dominated by a per-tier *bias* - a symmetric band around the measured",
          "value cannot contain a truth the measurement is consistently offset from. Widening",
          "sigma alone raises coverage only by making every interval uselessly wide; the honest",
          "fix is the depth-scale bias itself (technical report section 5), not the interval.", ""]

    with open(OUT_MD, "w") as fh:
        fh.write("\n".join(L))
    print("\n".join(L))
    print(f"\nwrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
