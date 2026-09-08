"""Run every approach on the SAME raw inputs and save metrics, JSON, config and debug images.

    python benchmark/scripts/compare_approaches.py
    python benchmark/scripts/compare_approaches.py --room "Room 1" --approach joint_consensus

Two independent axes, and every combination stays runnable. Nothing is replaced:

    --selector  largest | joint          floor/ceiling plane selection
    --model     fused   | consensus      how planes are obtained in the first place

      largest     the original rule: the two largest horizontal planes
      joint       six signals scored jointly and multiplied
      fused       merge all frames into one cloud, then fit planes on it
      consensus   fit planes per frame, then match them across frames by identity

Output layout, one directory per approach, so a defense can put two images side by side:

    out_compare/<approach>/
        config.json      exactly what was run
        metrics.json     the numbers
        <room>/          debug images, overlay + clean, per stage per frame

SCOPE: ordinary residential rooms - planar floor and ceiling, vertical walls. Staircases,
split levels and multi-height ceilings are out of scope and are expected to do badly.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
import warnings

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from pipeline.capture.depth import get_backend                          # noqa: E402
from pipeline.capture.depth_cache import infer_cached                   # noqa: E402
from pipeline.capture.photo import WORK_PX, load                        # noqa: E402
from pipeline.capture.register import match_all, register               # noqa: E402
from pipeline.geometry.fuse import fuse_frames                          # noqa: E402
from pipeline.geometry.lift import lift                                 # noqa: E402
from pipeline.geometry.openings import detect_openings                  # noqa: E402
from pipeline.geometry.plane_model import build_room_model, room_dimensions  # noqa: E402
from pipeline.geometry.planes import (                                  # noqa: E402
    CAMERA_UP, classify, estimate_gravity, extract_planes, merge_coplanar, regularize,
)
from pipeline.geometry.walls import (                                   # noqa: E402
    ceiling_height, corners_with_status, extract_walls, wall_pair_dimensions,
)
from pipeline.output import viz                                         # noqa: E402

TRUE_CEILING_M = 2.64
OUT_ROOT = os.path.join(ROOT, "out_compare")
WORLD_UP = np.array([0.0, -1.0, 0.0])     # world frame is the reference camera's frame


def per_frame_planes(frames, poses, selector):
    """Planes in each frame's own camera coords, with that frame's pose."""
    out = []
    for i, (f, T) in enumerate(zip(frames, poses)):
        if T is None:
            continue
        pts, nrm, pix = lift(f.depth, f.K, stride=2, return_pixels=True)
        pl = merge_coplanar(extract_planes(pts, nrm, threshold=0.05), pts)
        g = estimate_gravity(pl, prior=CAMERA_UP)
        pl, g, score = classify(pl, g, pts, True, selector=selector)
        pl = regularize(pl, g, pts)
        out.append({"i": i, "frame": f, "planes": pl, "T": T, "pts": pts,
                    "nrm": nrm, "pix": pix, "gravity": g, "score": score})
    return out


def run(room, selector: str, model: str, out_dir: str, debug: bool) -> dict:
    t0 = time.time()
    frames = room.frames
    reg = register(frames, match_all(frames))
    posed = [f for f, T in zip(frames, reg.poses) if T is not None]

    rec: dict = {"room": room.room_id, "selector": selector, "model": model,
                 "n_frames": len(frames), "registered": len(posed),
                 "register_residual_cm": round(reg.residual_median_m * 100, 2)}
    if len(posed) < 2:
        rec["error"] = "too few frames registered"
        return rec

    pf = per_frame_planes(frames, reg.poses, selector)

    if model == "fused":
        fc = fuse_frames(posed, stride=2)
        planes = merge_coplanar(extract_planes(fc.points, fc.normals, threshold=0.05), fc.points)
        g = estimate_gravity(planes, prior=WORLD_UP)
        planes, g, score = classify(planes, g, fc.points, False, selector=selector)
        planes = regularize(planes, g, fc.points)
        floor = next((p for p in planes if p.kind == "floor"), None)
        ceil = next((p for p in planes if p.kind == "ceiling"), None)
        walls = [p for p in planes if p.kind == "wall"]
        cloud = fc.points
        rec["selection_score"] = round(float(score), 4)
        if floor is not None and ceil is not None:
            h, sp = ceiling_height(floor, ceil, cloud)
            if np.isfinite(h):
                rec["ceiling_height"] = round(float(h), 4)
                rec["ceiling_spread_cm"] = round(float(sp) * 100, 1)
        rec["wall_pairs"] = [p["separation_m"] for p in wall_pair_dimensions(walls, g, cloud)[:4]]
        geo = (extract_walls(cloud, floor, ceil, walls=walls, gravity=g)
               if floor is not None and walls else None)
    else:
        m = build_room_model([(p["i"], p["planes"], p["T"]) for p in pf])
        dims = room_dimensions(m, WORLD_UP)
        rec.update({k: v for k, v in dims.items() if k != "wall_pairs"})
        rec["wall_pairs"] = [p["separation_m"] for p in dims.get("wall_pairs", [])[:4]]
        if "ceiling_height" in rec:
            rec["ceiling_height"] = round(float(rec["ceiling_height"]), 4)
        # Render from the model's confirmed planes, in the world frame.
        planes = [mp.to_plane() for mp in m if mp.confirmed]
        fc = fuse_frames(posed, stride=2)
        cloud, g = fc.points, WORLD_UP
        # Attach supporting points for rendering and for the extent tests. The band is wider
        # than the within-frame fit tolerance because a consensus plane is an average across
        # frames whose poses differ slightly, so it sits a few centimetres off any single
        # frame's surface. At 0.06 it matched nothing and every wall vanished before rendering.
        for pl in planes:
            pl.inliers = np.flatnonzero(np.abs(cloud @ pl.normal + pl.d) < 0.12)
        floor = next((p for p in planes if p.kind == "floor"), None)
        ceil = next((p for p in planes if p.kind == "ceiling"), None)
        walls = [p for p in planes if p.kind == "wall"]
        geo = (extract_walls(cloud, floor, ceil, walls=walls, gravity=g)
               if floor is not None and walls else None)

    if rec.get("ceiling_height"):
        rec["ceiling_err_pct"] = round(
            (rec["ceiling_height"] - TRUE_CEILING_M) / TRUE_CEILING_M * 100, 2)

    corners = corners_with_status(walls, floor, cloud, g)
    rec["corners_accepted"] = sum(1 for c in corners if c.accepted)
    rec["corners_rejected"] = sum(1 for c in corners if not c.accepted)
    rec["reject_reasons"] = sorted({c.reason for c in corners if not c.accepted})

    try:
        ops = [o.to_json() for o in detect_openings(walls, cloud, g, floor)]
    except Exception as exc:
        ops = []
        rec["openings_error"] = f"{type(exc).__name__}: {exc}"
    rec["openings"] = ops
    rec["polygon"] = (None if geo is None else
                      {"corners": int(len(geo.corners)),
                       "wall_lengths": np.round(geo.wall_lengths, 3).tolist(),
                       "floor_area": round(float(geo.floor_area), 3)})
    rec["seconds"] = round(time.time() - t0, 1)

    if debug:
        d = os.path.join(out_dir, room.room_id)
        for p in pf:
            f, stem = p["frame"], os.path.splitext(os.path.basename(p["frame"].image_path))[0]
            # Planes are rendered from THIS FRAME's own fit, not the world model. A consensus
            # plane is an average across frames, so testing fused points against it catches
            # only a sliver where the average happens to graze the real surface - the overlay
            # came out as thin strips that said nothing about what the frame saw. The
            # per-frame planes are also exactly what the consensus model is built from, so
            # this shows the actual input to the stage rather than a projection of its output.
            if model == "consensus":
                viz.save_pair(*viz.viz_planes(f.image, p["pix"], p["planes"]),
                              d, f"{stem}_A_planes")
            else:
                world = (p["T"][:3, :3] @ p["pts"].T).T + p["T"][:3, 3]
                viz.save_pair(*viz.viz_planes(f.image, p["pix"], planes, world_points=world),
                              d, f"{stem}_A_planes")
            viz.save_pair(*viz.viz_corners(f.image, corners, p["T"], f.K, walls, floor, ceil),
                          d, f"{stem}_B_corners")
            viz.save_pair(*viz.viz_openings(f.image, ops, walls, p["T"], f.K, g),
                          d, f"{stem}_C_openings")
            viz.save_pair(*viz.viz_room(f.image, geo, p["T"], f.K, g,
                                        rec.get("ceiling_height")),
                          d, f"{stem}_D_room")
        rec["debug_dir"] = d
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", default=os.path.join(ROOT, "benchmark", "raw", "photo"))
    ap.add_argument("--room", default=None)
    ap.add_argument("--selector", choices=["largest", "joint", "both"], default="both")
    ap.add_argument("--model", choices=["fused", "consensus", "both"], default="both")
    ap.add_argument("--no-debug", action="store_true")
    args = ap.parse_args()

    sels = ["largest", "joint"] if args.selector == "both" else [args.selector]
    mods = ["fused", "consensus"] if args.model == "both" else [args.model]

    scene = load(args.capture)
    be = get_backend("metric3d_v2")
    for room in scene.rooms:
        for f in room.frames:
            if f.depth is None:
                f.depth, _ = infer_cached(be, f.image_path, f.image, f.K, WORK_PX, True)

    summary = {}
    for sel, mod in itertools.product(sels, mods):
        name = f"{sel}_{mod}"
        out_dir = os.path.join(OUT_ROOT, name)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "config.json"), "w") as fh:
            json.dump({"selector": sel, "model": mod, "capture": args.capture,
                       "depth_backend": "metric3d_v2",
                       "ground_truth_ceiling_m": TRUE_CEILING_M,
                       "scope": "ordinary residential rooms; staircases and split levels "
                                "are out of scope"}, fh, indent=2)

        recs = []
        print(f"\n===== {name} =====")
        for room in scene.rooms:
            if args.room and room.room_id != args.room:
                continue
            r = run(room, sel, mod, out_dir, not args.no_debug)
            recs.append(r)
            ch = r.get("ceiling_height")
            print(f"  {room.room_id:<10} ceiling={f'{ch:.3f}m' if ch else 'abstain':<10} "
                  f"err={str(r.get('ceiling_err_pct', '-')):>7}%  "
                  f"corners {r.get('corners_accepted',0)}/{r.get('corners_accepted',0)+r.get('corners_rejected',0)}"
                  f"  openings {len(r.get('openings',[]))}  {r.get('seconds','-')}s")

        errs = [r["ceiling_err_pct"] for r in recs if "ceiling_err_pct" in r]
        agg = {"rooms_reported": len(errs), "rooms_total": len(recs),
               "mean_err_pct": round(float(np.mean(errs)), 2) if errs else None,
               "sd_err_pct": round(float(np.std(errs)), 2) if errs else None,
               "within_8pct": int(sum(abs(e) <= 8 for e in errs))}
        with open(os.path.join(out_dir, "metrics.json"), "w") as fh:
            json.dump({"aggregate": agg, "rooms": recs}, fh, indent=2)
        summary[name] = agg
        print(f"  -> reported {agg['rooms_reported']}/{agg['rooms_total']}  "
              f"mean {agg['mean_err_pct']}%  sd {agg['sd_err_pct']}%  "
              f"within8 {agg['within_8pct']}")

    with open(os.path.join(OUT_ROOT, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\n{'approach':<22} {'reported':>9} {'mean':>8} {'sd':>7} {'within8':>8}")
    print("-" * 58)
    for k, v in summary.items():
        print(f"{k:<22} {v['rooms_reported']}/{v['rooms_total']:<7} "
              f"{str(v['mean_err_pct']):>7}% {str(v['sd_err_pct']):>6}% {v['within_8pct']:>8}")
    print(f"\nwrote {OUT_ROOT}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
