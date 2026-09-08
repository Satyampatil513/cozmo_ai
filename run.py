"""One command per capture, for every tier.

    python run.py benchmark/raw/photo --tier photo --out out/
    python run.py benchmark/raw/video/IMG_0460.MOV --tier video --out out/
    python run.py benchmark/raw/lidar/scan.r3d --tier lidar --out out/

Tier is auto-detected from the input when not given. All three produce the same output
contract: `out/result.json` against `schemas/output.schema.json`.

The tiers differ only in the loader. Everything from `pipeline/measure.py` down is shared, so
a geometry fix lands in all three at once and none of them can silently diverge.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCHEMA_VERSION = "0.1"
VIDEO_EXT = {".mov", ".mp4", ".m4v"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


def _is_stray(path: str) -> bool:
    """Stray Scanner capture: a directory (or zip) holding camera_matrix.csv + depth/."""
    if path.lower().endswith(".zip"):
        try:
            import zipfile
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
            return any(n.endswith("camera_matrix.csv") for n in names)
        except Exception:
            return False
    if not os.path.isdir(path):
        return False
    for root in (path, *(os.path.join(path, d) for d in os.listdir(path)
                         if os.path.isdir(os.path.join(path, d)))):
        if os.path.isfile(os.path.join(root, "camera_matrix.csv")):
            return True
    return False


def detect_tier(path: str) -> str:
    if _is_stray(path):
        return "lidar"
    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".r3d":
            return "lidar"
        if ext in VIDEO_EXT:
            return "video"
        return "photo"
    names: list[str] = []
    for _root, _dirs, files in os.walk(path):
        names.extend(f.lower() for f in files)
    if any(n.endswith(".r3d") for n in names):
        return "lidar"
    if any(os.path.splitext(n)[1] in VIDEO_EXT for n in names):
        return "video"
    return "photo"


class _NpEncoder(json.JSONEncoder):
    """numpy scalars and arrays leak into results from every stage; serialise them once here."""

    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, np.bool_):
            return bool(o)
        return super().default(o)


def load_scene(path: str, tier: str, args, depth_backend):
    """Dispatch to the tier's loader. The only place the tiers differ."""
    if tier == "lidar":
        # Two LiDAR container formats in the wild, and they are not interchangeable: their
        # depth encodings AND their pose conventions differ. Dispatch on content.
        if _is_stray(path):
            from pipeline.capture.stray import load as load_stray
            return load_stray(path, stride=args.lidar_stride), {}
        from pipeline.capture.lidar import load as load_r3d
        if os.path.isdir(path):
            cands = [os.path.join(path, f) for f in sorted(os.listdir(path))
                     if f.lower().endswith(".r3d")]
            if not cands:
                raise SystemExit(f"no .r3d under {path}")
            path = cands[0]
        return load_r3d(path, stride=args.lidar_stride), {}

    if tier == "video":
        from pipeline.capture.video import load as load_video
        if os.path.isdir(path):
            cands = [os.path.join(path, f) for f in sorted(os.listdir(path))
                     if os.path.splitext(f)[1].lower() in VIDEO_EXT]
            if not cands:
                raise SystemExit(f"no video under {path}")
            path = cands[0]
        return load_video(path, every_n=args.video_every_n, max_frames=args.max_frames,
                          depth_backend=depth_backend, progress=True)

    from pipeline.capture.photo import load as load_photo
    return load_photo(path), {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", help="capture directory, .r3d file, or video file")
    ap.add_argument("--tier", choices=["photo", "video", "lidar"], default=None)
    ap.add_argument("--out", default="out")
    ap.add_argument("--backend", default="metric3d_v2", help="depth backend for photo/video")
    ap.add_argument("--no-cache", action="store_true", help="skip the depth cache")
    ap.add_argument("--lidar-stride", type=int, default=30)
    ap.add_argument("--video-every-n", type=int, default=30)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--photo-mode",
                    choices=["per_frame", "multiview_unvalidated", "multiview"],
                    default="per_frame",
                    help="photo tier only. per_frame: measure each photo alone (default). "
                         "multiview_unvalidated: register with a spanning tree, trust every "
                         "edge (the naive baseline, kept for the fix loop). "
                         "multiview: reject edges whose cycles do not close, then fuse")
    ap.add_argument("--debug", action="store_true",
                    help="write per-frame diagnostic overlays to <out>/debug/")
    ap.add_argument("--no-drift-correction", action="store_true",
                    help="ablation: video/lidar stitching only. Compose poses as-is, "
                         "skip the plane-anchored correction between rooms")
    args = ap.parse_args()

    t_start = time.time()
    tier = args.tier or detect_tier(args.input)
    os.makedirs(args.out, exist_ok=True)

    depth_backend = None
    if tier in ("photo", "video"):
        from pipeline.capture.depth import get_backend
        depth_backend = get_backend(args.backend)

    scene, meta = load_scene(args.input, tier, args, depth_backend)
    if not scene.rooms or not scene.frames:
        # A run that finds nothing must not exit 0 having written an empty result. Before this
        # guard, a flat folder of photos printed "tier=photo 17.8s" and wrote a result with no
        # rooms in it - indistinguishable from success at a glance, which is the worst way to
        # fail in front of someone timing you.
        raise SystemExit(f"no usable frames found in {args.input} (tier={tier}). Nothing to "
                         f"measure. Run scripts/validate_capture.py for a per-room diagnosis.")

    from pipeline.measure import measure_room
    rooms = []
    for room in scene.rooms:
        print(f"  measuring {room.room_id} ({len(room.frames)} frames)...")
        rooms.append(measure_room(room, depth_backend=depth_backend,
                                  cache=not args.no_cache,
                                  debug_dir=os.path.join(args.out, "debug") if args.debug
                                  else None,
                                  photo_mode=args.photo_mode,
                                  drift_correction=not args.no_drift_correction))

    result = {
        "schema_version": SCHEMA_VERSION,
        "capture": {
            "tier": tier,
            "input": os.path.basename(os.path.normpath(args.input)),
            "device": scene.device,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_commit": git_commit(),
            "scale_source": scene.rooms[0].scale.source if scene.rooms else "none",
            "depth_backend": args.backend if depth_backend else None,
            "drift_correction": not args.no_drift_correction,
            "approach": (f"photo_{args.photo_mode}" if tier == "photo" else tier),
            "loader_meta": {k: v for k, v in (meta or {}).items() if k != "odometry"},
        },
        "property": {
            # A stitched capture reports several sub-rooms under one RoomCapture, so the
            # property's own room list has to expand those rather than listing the capture
            # once - "rooms": ["video_room_0", "video_room_1"], not ["video_room"].
            "rooms": [sr["room_id"] for r in rooms
                     for sr in (r.get("sub_rooms") or [r])],
            "connections": [c for r in rooms for c in (r.get("connections") or [])],
            "footprint_area": (next((r["footprint_area_m2"] for r in rooms
                                    if r.get("footprint_area_m2") is not None), None)),
        },
        "rooms": rooms,
        "timing_seconds": round(time.time() - t_start, 1),
    }

    out_path = os.path.join(args.out, "result.json")
    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2, cls=_NpEncoder)

    blueprint_report = None
    if any(r.get("mode") == "stitched" for r in rooms):
        # A blueprint is only meaningful once there is more than one room to place relative
        # to another - a single unstitched room has no adjacency to draw. Written from the
        # SAME dict just serialised to result.json, via json round-trip, so the renderer is
        # exercised against exactly what a caller re-reading result.json later would see,
        # not a richer in-memory object this process happens to still be holding.
        from pipeline.output.render import render_plan
        blueprint_report = render_plan(
            json.loads(json.dumps(result, cls=_NpEncoder)),
            os.path.join(args.out, "blueprint.svg"), os.path.join(args.out, "blueprint.png"))

    print(f"\ntier={tier}  commit={result['capture']['pipeline_commit']}  "
          f"{result['timing_seconds']}s")
    for r in rooms:
        if r.get("mode") == "stitched":
            dc = r.get("drift_correction", {})
            print(f"  {r['room_id']:<24} mode=stitched   "
                  f"{r.get('n_rooms_detected', '?')} room(s) detected, "
                  f"{len(r.get('connections', []))} connection(s), "
                  f"footprint={r.get('footprint_area_m2', '-')} m2")
            print(f"      drift correction: {dc.get('method', '-')}")
            print(f"      shared-wall gap: {dc.get('shared_wall_gap_before_cm', '-')} cm "
                  f"-> {dc.get('shared_wall_gap_after_cm', '-')} cm   "
                  f"footprint: {dc.get('footprint_before_m2', '-')} "
                  f"-> {dc.get('footprint_after_m2', '-')} m2")
            for sr in r.get("sub_rooms", []):
                sch = sr.get("ceiling_height")
                print(f"      {sr['room_id']:<22} "
                      f"ceiling={f'{sch:.3f}m' if sch else 'abstained':<12} "
                      f"walls={sr.get('n_walls','-')} "
                      f"openings={len(sr.get('openings', []))}")
            continue
        ch = r.get("ceiling_height")
        print(f"  {r['room_id']:<24} mode={r.get('mode','-'):<10} "
              f"ceiling={f'{ch:.3f}m' if ch else 'abstained':<12} "
              f"walls={r.get('n_walls','-')} openings={len(r.get('openings', []))}")
        # After the room's own line, never before it: printed first, a block of registration
        # numbers reads as belonging to the room above it.
        reg = r.get("registration")
        if reg:
            for line in r.get("registration_report", [
                    f"{reg['n_registered']}/{reg['n_frames']} registered (unvalidated)"]):
                print(f"      {line}")
        if r.get("multiview_rejected"):
            print(f"      -> {r['multiview_reject_reason']}")
    print(f"wrote {out_path}")
    if args.debug:
        print(f"wrote diagnostic overlays to {os.path.join(args.out, 'debug')}/")
    if blueprint_report is not None:
        print(f"wrote {os.path.join(args.out, 'blueprint.png')} and .svg "
              f"({blueprint_report['rooms_drawn']} room(s) drawn"
              + (f", {len(blueprint_report['rooms_unplaced'])} NOT drawn - no closed polygon: "
                 f"{blueprint_report['rooms_unplaced']}"
                if blueprint_report['rooms_unplaced'] else "") + ")")
    # Stated once, honestly, rather than as a blanket line every run repeats regardless of
    # what just happened: stitching and the blueprint are built for video/lidar and ran on
    # THIS capture whenever it produced more than one room; photo-tier stitching and damage
    # detection are not built at all, on any capture.
    not_built = ["damage detection"]
    if not any(r.get("mode") == "stitched" for r in rooms):
        not_built.insert(0, "multi-room stitching (photo-tier needs doorway-pair shots; "
                            "video/lidar needs >1 room detected in this capture)")
    print(f"NOT BUILT: {', '.join(not_built)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
