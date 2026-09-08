"""Three photo-tier approaches, all five rooms, one table.

    python benchmark/scripts/compare_photo_modes.py                  # all rooms, all modes
    python benchmark/scripts/compare_photo_modes.py --room "Room 1"
    python benchmark/scripts/compare_photo_modes.py --no-viz         # numbers only, faster

The three approaches run on the SAME raw photos and the same cached depth, so any difference
between them is the registration and nothing else:

  photo_per_frame              each photo measured in its own camera frame, aggregated by
                               median. The shipped default. No polygon: unposed frames share
                               no coordinate system.
  photo_multiview_unvalidated  maximum-support spanning tree, every edge trusted. The naive
                               baseline, kept runnable rather than remembered, because the
                               whole finding is what it does wrong.
  photo_multiview              edges whose loops do not close are cut first, then fuse.

WHY ALL FIVE ROOMS. Room 1 alone could make either method look good or bad by accident: it is
one room, its per-frame error happens to be small, and its registration happens to be dense.
The rooms differ in exactly the ways that matter here - the Hallway's photos barely overlap,
the Kitchen has four photos rather than six, and two of the bedrooms are where the bed gets
fitted as the floor. A method that only works on Room 1 is not a method.

WHAT IS AND IS NOT SCORED. Ceiling height has tape ground truth (2.64 m). Wall lengths do NOT
- `benchmark/ground_truth/rooms.csv` is still empty - so wall spans are reported as measured
values and cross-mode agreement, never as errors. Calling an unverified number an improvement
is the failure this whole exercise exists to avoid.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from pipeline.capture.depth import get_backend                      # noqa: E402
from pipeline.capture.depth_cache import infer_cached               # noqa: E402
from pipeline.capture.multiview import clear_poses                  # noqa: E402
from pipeline.capture.photo import WORK_PX, load                    # noqa: E402
from pipeline.measure import measure_room                           # noqa: E402

TRUE_CEILING_M = 2.64          # tape, 3BHK. The only photo-tier ground truth we have.
CAPTURE = os.path.join(ROOT, "benchmark", "raw", "photo")
OUT_JSON = os.path.join(ROOT, "benchmark", "results", "compare_photo_modes.json")
OUT_MD = os.path.join(ROOT, "benchmark", "results", "compare_photo_modes.md")
VIZ_DIR = os.path.join(ROOT, "out_multiview")

MODES = ["per_frame", "multiview_unvalidated", "multiview"]


def err_pct(v):
    return None if v is None else round((v - TRUE_CEILING_M) / TRUE_CEILING_M * 100, 2)


def summarise(res: dict) -> dict:
    """Pull the six comparison axes out of a measure_room result."""
    poly = res.get("polygon")
    reg = res.get("registration") or {}
    walls = [round(float(w["separation_m"]), 3) for w in (res.get("wall_pairs") or [])][:4]
    if poly:
        walls = [round(float(x), 3) for x in poly["wall_lengths"]][:6]

    # Per-frame measures each photo separately, so its structure counts live in the per-frame
    # list rather than at the top level. Reported as the median over frames that reported at
    # all - a mean would be dragged by the frames that abstained and contributed nothing.
    per = res.get("per_frame") or []
    if per and res.get("corners_accepted") is None:
        ca = [m["corners_accepted"] for m in per if m.get("corners_accepted") is not None]
        cr = [m["corners_rejected"] for m in per if m.get("corners_rejected") is not None]
        nw = [m["n_walls"] for m in per if m.get("n_walls") is not None]
        res = dict(res,
                   corners_accepted=(int(np.median(ca)) if ca else None),
                   corners_rejected=(int(np.median(cr)) if cr else None),
                   n_walls=(int(np.median(nw)) if nw else res.get("n_walls")))
        if not walls:
            allsp = [w["separation_m"] for m in per for w in (m.get("wall_pairs") or [])]
            walls = [round(float(np.median(allsp)), 3)] if allsp else []
        poly = res.get("polygon")
    return {
        "approach": res.get("approach"),
        "mode": res.get("mode"),
        "ceiling_m": (None if res.get("ceiling_height") is None
                      else round(float(res["ceiling_height"]), 4)),
        "ceiling_err_pct": err_pct(res.get("ceiling_height")),
        # Spread means different things per mode and must not be silently pooled: per-frame
        # is the disagreement BETWEEN frames, fused is the thickness of one ceiling plane.
        "spread_m": (round(float(res["ceiling_height_sd"]), 4)
                     if res.get("ceiling_height_sd") is not None
                     else (round(float(res["ceiling_spread_m"]), 4)
                           if res.get("ceiling_spread_m") is not None else None)),
        "spread_kind": ("between-frame SD" if res.get("ceiling_height_sd") is not None
                        else ("plane thickness" if res.get("ceiling_spread_m") is not None
                              else None)),
        "wall_spans_m": walls,
        "n_walls": res.get("n_walls"),
        "corners_accepted": res.get("corners_accepted"),
        "corners_rejected": res.get("corners_rejected"),
        "polygon": bool(poly),
        "polygon_reason": res.get("polygon_reason"),
        "floor_area_m2": (round(float(poly["floor_area"]), 3) if poly else None),
        "openings": len(res.get("openings") or []),
        "registered": reg.get("n_registered"),
        "n_frames": res.get("n_frames"),
        "posed_frames": res.get("posed_frames"),
        "residual_max_cm": reg.get("residual_max_cm"),
        "residual_median_cm": reg.get("residual_median_cm"),
        "rejected_pairwise": reg.get("rejected_pairwise"),
        "rejected_cycle": reg.get("rejected_cycle"),
        "trustworthy": reg.get("trustworthy"),
        "fell_back": bool(res.get("multiview_rejected")),
        "seconds": res.get("seconds"),
    }


def run_room(room, backend, modes, viz: bool) -> dict:
    """Measure one room under each approach. Depth is inferred once and shared."""
    for f in room.frames:
        if f.depth is None:
            f.depth, _ = infer_cached(backend, f.image_path, f.image, f.K, WORK_PX,
                                      use_cache=True)

    out: dict = {"room": room.room_id, "n_frames": len(room.frames), "modes": {}}
    for mode in modes:
        clear_poses(room.frames)          # every mode starts from the same unposed state
        t0 = time.time()
        res = measure_room(room, depth_backend=backend, cache=True, photo_mode=mode)
        s = summarise(res)
        s["wall_seconds"] = round(time.time() - t0, 1)
        out["modes"][mode] = s
        if mode == "multiview" and res.get("registration_report"):
            out["registration_report"] = res["registration_report"]
        if mode == "multiview":
            out["registration"] = res.get("registration")

        ceil = "abstained" if s["ceiling_m"] is None else f"{s['ceiling_m']:.3f} m"
        errs = "" if s["ceiling_err_pct"] is None else f" ({s['ceiling_err_pct']:+.1f}%)"
        reg = "" if s["registered"] is None else f"  registered={s['registered']}/{s['n_frames']}"
        fb = "  FELL BACK to per-frame" if s["fell_back"] else ""
        print(f"    {mode:<28} {ceil:>12}{errs:<9}  mode={s['mode']:<10}"
              f"corners={s['corners_accepted']}  polygon={'yes' if s['polygon'] else 'no'}"
              f"{reg}{fb}  {s['seconds']}s")

        if viz and mode == "multiview":
            out["viz"] = write_viz(room, res, backend)

    clear_poses(room.frames)
    return out


def write_viz(room, res: dict, backend) -> dict:
    """Regenerate the gated registration and render every stage of it.

    Registration is re-run rather than threaded out of `measure_room`, because the measurement
    path deliberately keeps no reference to it once the poses are applied. Matching is
    deterministically seeded, so this reproduces the same graph that produced the numbers.
    """
    from pipeline.capture.multiview import register_multiview
    from pipeline.capture.register import match_all
    from pipeline.geometry.fuse import fuse_frames
    from pipeline.output import multiview_viz as mv

    d = os.path.join(VIZ_DIR, room.room_id.replace(" ", "_"))
    # Wipe first: a verdict is part of each filename, so a re-run after a gate change would
    # otherwise leave A_match_01_kept.png beside A_match_01_implausible.png and the directory
    # would document two contradictory runs as though both were current.
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith((".png", ".txt", ".jpg")):
                os.remove(os.path.join(d, f))
    os.makedirs(d, exist_ok=True)
    pairs = match_all(room.frames)
    reg = register_multiview(room.frames, pairs)
    status = {(e.edge): e.status for e in reg.edges}

    files = {"graph": mv.viz_overlap_graph(reg, os.path.join(d, "B_overlap_graph.png")),
             "cameras": mv.viz_cameras(reg, os.path.join(d, "C_cameras.png"))}

    # Correspondences for every edge that had a fit, cut or kept. The cut ones are the point:
    # a reader can see that the features really do match and the transform is still wrong,
    # which is what makes "good pairwise fit, bad composed pose" concrete.
    for pm in pairs:
        st = status.get((pm.i, pm.j), "no_overlap")
        if st == "no_overlap":
            continue
        pm.status = st
        mv.viz_matches(room.frames[pm.i], room.frames[pm.j], pm,
                       os.path.join(d, f"A_match_{pm.i}{pm.j}_{st}.png"))

    if reg.n_registered >= 2:
        from pipeline.capture.multiview import apply_poses, clear_poses as _cp
        _cp(room.frames)
        apply_poses(room.frames, reg.poses)
        files["clouds"] = mv.viz_clouds_by_source(
            room.frames, reg, os.path.join(d, "D_clouds_by_source.png"))
        if reg.trustworthy:
            posed = [f for f in room.frames if f.T_wc is not None]
            if len(posed) >= 3:
                files["geometry"] = mv.viz_geometry(
                    room.frames, reg, fuse_frames(posed, stride=2), d, "E_")
        _cp(room.frames)

    with open(os.path.join(d, "graph.txt"), "w") as fh:
        fh.write(f"{room.room_id}\n\n" + mv.graph_ascii(reg) + "\n\n"
                 + "\n".join(reg.report_lines()) + "\n")
    print(f"      viz -> {d}")
    return {k: v for k, v in files.items() if v}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--room", default=None, help="one room id; default all")
    ap.add_argument("--modes", nargs="+", default=MODES, choices=MODES)
    ap.add_argument("--no-viz", action="store_true")
    args = ap.parse_args()

    scene = load(CAPTURE)
    rooms = [r for r in scene.rooms if args.room in (None, r.room_id)]
    if not rooms:
        raise SystemExit(f"no room matching {args.room!r}")
    backend = get_backend("metric3d_v2")

    results = []
    for room in rooms:
        print(f"\n{room.room_id} ({len(room.frames)} photos)")
        results.append(run_room(room, backend, args.modes, not args.no_viz))

    payload = {"ground_truth_ceiling_m": TRUE_CEILING_M, "modes": args.modes,
               "rooms": results}
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)

    # A single-room or reduced-mode run writes to its own file. Letting it overwrite the
    # aggregate report would leave a document titled "all five rooms" containing one, which
    # is the kind of quiet inconsistency a reader has no way to notice.
    partial = args.room is not None or set(args.modes) != set(MODES)
    out_json, out_md = OUT_JSON, OUT_MD
    if partial:
        tag = (args.room or "modes").replace(" ", "_").lower()
        out_json = OUT_JSON.replace(".json", f".partial_{tag}.json")
        out_md = OUT_MD.replace(".md", f".partial_{tag}.md")
        print(f"\npartial run ({args.room or 'subset of modes'}) - writing to a separate file "
              f"so the full report is not overwritten")
    with open(out_json, "w") as fh:
        json.dump(payload, fh, indent=2)
    write_markdown(payload, args.modes, out_md)
    print(f"\nwrote {out_json}\nwrote {out_md}")
    return 0


def _cell(s: dict, key: str, fmt="{}", dash="-"):
    v = s.get(key)
    return dash if v is None else fmt.format(v)


def write_markdown(payload: dict, modes: list[str], out_md: str = OUT_MD) -> None:
    L: list[str] = ["# Photo tier: per-frame vs naive multi-view vs cycle-gated multi-view",
                    "",
                    f"Regenerate: `python benchmark/scripts/compare_photo_modes.py`  ",
                    f"Ground truth: ceiling {TRUE_CEILING_M} m by tape. Wall lengths have NO "
                    "ground truth (`benchmark/ground_truth/rooms.csv` is empty), so wall "
                    "spans below are measurements, not errors.", ""]

    L += ["## Ceiling height, against 2.64 m tape", "",
          "| Room | " + " | ".join(modes) + " |",
          "|---|" + "---|" * len(modes)]
    for r in payload["rooms"]:
        cells = []
        for m in modes:
            s = r["modes"].get(m, {})
            if s.get("ceiling_m") is None:
                cells.append("abstained")
            else:
                mark = " *(fell back)*" if s.get("fell_back") else ""
                cells.append(f"{s['ceiling_m']:.3f} m ({s['ceiling_err_pct']:+.1f}%){mark}")
        L.append(f"| {r['room']} | " + " | ".join(cells) + " |")

    L += ["", "## Registration quality (gated mode)", "",
          "`max, all edges` is the misleading number: a tree edge is the edge a pose was "
          "composed from, so checking the pose against it is circular and always passes. "
          "`max, non-tree` is the only independent evidence in the graph, and it is what the "
          "verdict uses. A room with zero non-tree edges has no loop anywhere in it and has "
          "verified nothing, however small its residuals look.", "",
          "| Room | photos | registered | edges | cut: pairwise | cut: cycle | non-tree edges "
          "| max, all edges | max, non-tree | camera height dev | verdict |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in payload["rooms"]:
        s = r["modes"].get("multiview", {})
        reg = r.get("registration") or {}
        nt = reg.get("n_nontree_edges")
        L.append(
            f"| {r['room']} | {r['n_frames']} | {_cell(s, 'registered')} | "
            f"{reg.get('candidate_edges', '-')} | {reg.get('rejected_pairwise', '-')} | "
            f"{reg.get('rejected_cycle', '-')} | {'-' if nt is None else nt} | "
            f"{_cell(s, 'residual_max_cm', '{} cm')} | "
            + ("**none**" if not nt else
               f"{_cell(reg, 'residual_max_nontree_cm', '{} cm')}")
            + f" | {_cell(reg, 'camera_height_dev_m', '{} m')} | "
            f"{'TRUSTED' if s.get('trustworthy') else 'REJECTED -> per-frame'} |")

    L += ["", "### Frames dropped, and why", ""]
    any_drop = False
    for r in payload["rooms"]:
        reg = r.get("registration") or {}
        for u in reg.get("unregistered", []):
            L.append(f"- **{r['room']}** frame {u['frame']}: {u['reason']}")
            any_drop = True
        for i in reg.get("unverified_frames", []):
            L.append(f"- **{r['room']}** frame {i}: placed, but on no loop - "
                     f"never independently cross-checked")
            any_drop = True
    if not any_drop:
        L.append("Every frame placed and cross-checked.")

    L += ["", "## Structure recovered", "",
          "| Room | mode | walls | corners | polygon | floor area | openings | spread | "
          "runtime |", "|---|---|---|---|---|---|---|---|---|"]
    for r in payload["rooms"]:
        for m in modes:
            s = r["modes"].get(m, {})
            sp = ("-" if s.get("spread_m") is None
                  else f"{s['spread_m'] * 100:.1f} cm ({s.get('spread_kind')})")
            L.append(
                f"| {r['room']} | {m} | {_cell(s, 'n_walls')} | "
                f"{_cell(s, 'corners_accepted')} | {'yes' if s.get('polygon') else 'no'} | "
                f"{_cell(s, 'floor_area_m2', '{} m2')} | {_cell(s, 'openings')} | {sp} | "
                f"{_cell(s, 'seconds', '{} s')} |")

    L += ["", "## Wall spans, as measured (no ground truth to score against)", "",
          "| Room | mode | spans (m) |", "|---|---|---|"]
    for r in payload["rooms"]:
        for m in modes:
            s = r["modes"].get(m, {})
            spans = s.get("wall_spans_m") or []
            L.append(f"| {r['room']} | {m} | "
                     + (", ".join(f"{v:.3f}" for v in spans) if spans else "-") + " |")

    for r in payload["rooms"]:
        if r.get("registration_report"):
            L += ["", f"### {r['room']} - gate detail", "", "```"]
            L += r["registration_report"]
            L += ["```"]

    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
