"""Score the brief's gates against the laser survey, and name every gate we cannot score.

    python benchmark/scripts/gates.py

Reads measurements already produced by the pipeline - `benchmark/results/compare_photo_modes.json`
for the photo tier and `out_{lidar,video}/result.json` for the others - so this scores exactly
the numbers the pipeline last emitted rather than a fresh run that might differ.

THE GATES, quoted from the brief rather than paraphrased:

  Opening widths   <= 2 cm on >= 85% of openings, detection itself scored: a missed opening and
                   a phantom opening each count as a miss
  Ceiling height   <= 1.5 cm per room; where a room is captured more than once, spread across
                   captures <= 1 cm
  Repeatability    two captures of the same room at the same tier agree within 1 cm or 0.5%
                   per wall
  Wall lengths     photo +/- 8% with calibrated intervals, video +/- 3%
  Drift            report states what is done about drift, with an on/off ablation
  Stitch           per-room photo folders produce one stitched plan, footprint +/- 8%

A gate with no ground truth is reported UNSCOREABLE with the reason, never as a pass and never
quietly omitted. The brief scores compliance coverage, and an honest "we cannot score this,
here is why" is a row; a missing row is not.

ON THE CEILING TRUTH. The laser survey reads 2.74 m in every room. An earlier tape figure of
2.64 m was hardcoded in four scripts and is superseded - it is 10 cm out, which is nearly seven
times the entire 1.5 cm gate, so which number is used decides the result far more than any
pipeline change does. The sheet is now the single source.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ground_truth import coverage, load_mapping, load_openings, load_rooms  # noqa: E402

CEILING_GATE_M = 0.015
WALL_GATE_PCT = {"photo": 8.0, "video": 3.0}
OPENING_GATE_M = 0.02
OPENING_PASS_FRACTION = 0.85

OUT_JSON = os.path.join(ROOT, "benchmark", "results", "gates.json")
OUT_MD = os.path.join(ROOT, "benchmark", "results", "gates.md")


def match_spans(measured: list[float], truth: list[float]) -> list[dict]:
    """Pair each measured span with a distinct truth dimension, closest first.

    Greedy on absolute error, and each truth dimension is consumed once. Without the
    consumption rule a pipeline that reported the same wall four times would match all four
    against the one dimension it happens to be near and score four passes from one measurement.
    """
    pool = list(truth)
    out = []
    for m in sorted(measured, reverse=True):
        if not pool:
            out.append({"measured_m": round(m, 3), "truth_m": None, "err_pct": None,
                        "note": "no unmatched ground-truth dimension left"})
            continue
        best = min(pool, key=lambda t: abs(t - m))
        pool.remove(best)
        out.append({"measured_m": round(m, 3), "truth_m": round(best, 3),
                    "err_pct": round((m - best) / best * 100, 2)})
    for t in pool:
        out.append({"measured_m": None, "truth_m": round(t, 3), "err_pct": None,
                    "note": "dimension never measured by the pipeline"})
    return out


def score_ceiling(measured, truth) -> dict:
    if measured is None:
        return {"status": "ABSTAINED", "detail": "pipeline declined to report a ceiling"}
    if truth is None:
        return {"status": "UNSCOREABLE", "detail": "no ceiling height in the survey"}
    err = measured - truth
    return {"status": "PASS" if abs(err) <= CEILING_GATE_M else "FAIL",
            "measured_m": round(measured, 4), "truth_m": round(truth, 4),
            "error_m": round(err, 4), "error_cm": round(err * 100, 1),
            "error_pct": round(err / truth * 100, 2),
            "gate_cm": CEILING_GATE_M * 100}


def score_walls(spans, truth_dims, tier) -> dict:
    gate = WALL_GATE_PCT.get(tier)
    if not truth_dims:
        return {"status": "UNSCOREABLE", "detail": "no wall lengths in the survey"}
    if gate is None:
        return {"status": "UNSCOREABLE",
                "detail": f"no wall gate defined for the {tier} tier in this brief "
                          f"(Round 1 gates apply and are not restated here)"}
    if not spans:
        return {"status": "NO OUTPUT", "detail": "pipeline produced no wall spans",
                "truth_m": [round(t, 3) for t in truth_dims]}
    pairs = match_spans(spans, truth_dims)
    scored = [p for p in pairs if p.get("err_pct") is not None]
    within = [p for p in scored if abs(p["err_pct"]) <= gate]
    return {"status": ("PASS" if scored and len(within) == len(scored)
                       else ("PARTIAL" if within else "FAIL")),
            "gate_pct": gate, "pairs": pairs,
            "within_gate": len(within), "scored": len(scored),
            "worst_err_pct": (max((abs(p["err_pct"]) for p in scored), default=None))}


def load_photo_results() -> dict:
    p = os.path.join(ROOT, "benchmark", "results", "compare_photo_modes.json")
    if not os.path.isfile(p):
        return {}
    return json.load(open(p))


def load_tier_result(tier: str) -> dict | None:
    p = os.path.join(ROOT, f"out_{tier}", "result.json")
    return json.load(open(p)) if os.path.isfile(p) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--modes", nargs="+",
                    default=["per_frame", "multiview_unvalidated", "multiview"])
    args = ap.parse_args()

    rooms, mapping, openings = load_rooms(), load_mapping(), load_openings()
    cov = coverage()
    photo = load_photo_results()

    report: dict = {"coverage": cov, "gates": {}, "rooms": []}

    for r in photo.get("rooms", []):
        cap = r["room"]
        rid = mapping.get(cap)
        gt = rooms.get(rid) if rid else None
        row = {"capture_room": cap, "ground_truth_room": rid, "modes": {}}
        if gt is None:
            row["error"] = f"no ground-truth row mapped for capture folder {cap!r}"
            report["rooms"].append(row)
            continue
        row["truth"] = {"ceiling_m": gt.ceiling_m, "dimensions_m": gt.dimensions,
                        "floor_area_m2": gt.floor_area_m2,
                        "floor_area_derived": gt.floor_area_derived,
                        "notes": gt.notes}
        for mode in args.modes:
            s = r["modes"].get(mode)
            if not s:
                continue
            row["modes"][mode] = {
                "ceiling": score_ceiling(s.get("ceiling_m"), gt.ceiling_m),
                "walls": score_walls(s.get("wall_spans_m") or [], gt.dimensions, "photo"),
                "polygon": s.get("polygon"),
                "floor_area_m2": s.get("floor_area_m2"),
            }
        report["rooms"].append(row)

    # Gates we cannot score, each with the specific reason. These are rows in the compliance
    # matrix, and a reason is worth more than a blank.
    op_measured = cov["openings_measured"]
    report["gates"]["openings"] = {
        "status": "UNSCOREABLE" if not op_measured else "SCOREABLE",
        "gate": f"<= {OPENING_GATE_M * 100:.0f} cm on >= {OPENING_PASS_FRACTION:.0%}",
        "detail": (f"all {cov['openings_rows']} rows in openings.csv are unmeasured - "
                   f"no width, height or sill anywhere" if not op_measured else ""),
    }
    report["gates"]["repeatability"] = {
        "status": "UNSCOREABLE",
        "gate": "two captures of one room agree within 1 cm or 0.5% per wall",
        "detail": "no room has been captured twice at the same tier",
    }
    report["gates"]["drift_ablation"] = {
        "status": "FAIL",
        "gate": "report states drift handling, with an on/off ablation",
        "detail": ("multi-room stitching is NOT BUILT, so there is no stitched footprint to "
                   "ablate. Within a room, photo multiview now rejects inconsistent poses by "
                   "cycle consistency (see multiview_findings.md), but the brief's row is "
                   "about accumulated drift across rooms and that is not addressed"),
    }
    report["gates"]["photo_stitch"] = {
        "status": "FAIL",
        "gate": "per-room photo folders -> one stitched plan, footprint +/- 8%",
        "detail": "pipeline/stitching/stitch.py raises NotImplementedError",
    }
    report["gates"]["head_to_head"] = {
        "status": "NOT BUILT",
        "gate": "beat or tie an incumbent app on >= 70% of shared dimensions, 2 rooms",
        "detail": "no incumbent app export captured",
    }

    with open(OUT_JSON, "w") as fh:
        json.dump(report, fh, indent=2)
    write_md(report, args.modes)
    print(f"wrote {OUT_JSON}\nwrote {OUT_MD}")
    print_summary(report, args.modes)
    return 0


def print_summary(rep: dict, modes: list[str]) -> None:
    print("\nGROUND TRUTH COVERAGE")
    c = rep["coverage"]
    print(f"  rooms {c['rooms_complete']}/{c['rooms']} complete   "
          f"walls {c['walls_measured']}/{c['walls_expected']}   "
          f"openings {c['openings_measured']}/{c['openings_rows']}")
    for r in rep["rooms"]:
        if "truth" not in r:
            print(f"\n{r['capture_room']}: {r.get('error')}")
            continue
        t = r["truth"]
        print(f"\n{r['capture_room']} -> {r['ground_truth_room']}   "
              f"truth ceiling {t['ceiling_m']:.3f} m, dims {t['dimensions_m']}")
        for m in modes:
            s = r["modes"].get(m)
            if not s:
                continue
            c_, w = s["ceiling"], s["walls"]
            cs = (f"{c_['status']:<12} {c_.get('error_cm', ''):>7}"
                  + (" cm" if "error_cm" in c_ else "   "))
            ws = w["status"]
            if w.get("scored"):
                ws += f" {w['within_gate']}/{w['scored']} within {w['gate_pct']}%"
                ws += f", worst {w['worst_err_pct']:.1f}%"
            print(f"    {m:<24} ceiling {cs}   walls {ws}")


def write_md(rep: dict, modes: list[str]) -> None:
    c = rep["coverage"]
    L = ["# Gate report", "",
         "Regenerate: `python benchmark/scripts/gates.py`  ",
         "Ground truth: `benchmark/ground_truth/`, laser survey. Ceiling 2.74 m "
         "(supersedes an earlier 2.64 m tape figure that was hardcoded in four scripts).", "",
         "## Ground-truth coverage", "",
         f"- Rooms: **{c['rooms_complete']}/{c['rooms']}** complete",
         f"- Wall lengths: **{c['walls_measured']}/{c['walls_expected']}** measured",
         f"- Ceiling readings: **{c['ceilings_measured']}**",
         f"- Floor areas: **{c['floor_areas_derived']}** derived from walls "
         f"(none measured directly)",
         f"- Openings: **{c['openings_measured']}/{c['openings_rows']}** measured",
         ""]

    L += ["## Ceiling height - gate <= 1.5 cm per room", "",
          "| Room | truth | " + " | ".join(modes) + " |",
          "|---|---|" + "---|" * len(modes)]
    for r in rep["rooms"]:
        if "truth" not in r:
            continue
        cells = []
        for m in modes:
            s = r["modes"].get(m)
            if not s:
                cells.append("-")
                continue
            g = s["ceiling"]
            cells.append(g["status"] if "error_cm" not in g
                         else f"{g['measured_m']:.3f} ({g['error_cm']:+.1f} cm) {g['status']}")
        L.append(f"| {r['capture_room']} | {r['truth']['ceiling_m']:.3f} m | "
                 + " | ".join(cells) + " |")

    L += ["", "## Wall lengths - gate +/- 8% (photo tier)", "",
          "| Room | truth dims | " + " | ".join(modes) + " |",
          "|---|---|" + "---|" * len(modes)]
    for r in rep["rooms"]:
        if "truth" not in r:
            continue
        cells = []
        for m in modes:
            s = r["modes"].get(m)
            if not s:
                cells.append("-")
                continue
            w = s["walls"]
            cells.append(f"{w['within_gate']}/{w['scored']} within 8%, worst "
                         f"{w['worst_err_pct']:.1f}% - **{w['status']}**"
                         if w.get("scored") else w["status"])
        dims = ", ".join(f"{d:.2f}" for d in r["truth"]["dimensions_m"])
        L.append(f"| {r['capture_room']} | {dims} | " + " | ".join(cells) + " |")

    L += ["", "### Span by span", ""]
    for r in rep["rooms"]:
        if "truth" not in r:
            continue
        for m in modes:
            s = r["modes"].get(m)
            if not s or not s["walls"].get("pairs"):
                continue
            L.append(f"**{r['capture_room']} / {m}**")
            L.append("")
            L.append("| measured | truth | error |")
            L.append("|---|---|---|")
            for p in s["walls"]["pairs"]:
                mm = "-" if p["measured_m"] is None else f"{p['measured_m']:.3f} m"
                tt = "-" if p["truth_m"] is None else f"{p['truth_m']:.3f} m"
                ee = p.get("note") or (f"{p['err_pct']:+.1f}%" if p["err_pct"] is not None
                                       else "-")
                L.append(f"| {mm} | {tt} | {ee} |")
            L.append("")

    L += ["## Gates that cannot be scored, and why", "",
          "| Gate | Status | Why |", "|---|---|---|"]
    for k, g in rep["gates"].items():
        L.append(f"| {k} | **{g['status']}** | {g.get('detail') or g.get('gate')} |")

    with open(OUT_MD, "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
