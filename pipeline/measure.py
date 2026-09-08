"""The single measurement path. Every tier ends up here.

Photo, video and LiDAR captures differ only in how they become a point cloud and whether that
cloud has poses. From this module down there is one code path, so a fix to the geometry
benefits all three tiers and none of them can quietly diverge.

Two modes, chosen by the data rather than by the tier:

  posed      frames carry T_wc (LiDAR always; video when odometry succeeded). All frames are
             fused into one world cloud and measured once. This is the only mode that can
             produce a room polygon, because no single view sees a whole room.

  unposed    frames have no poses (photo tier today). Each frame is measured in its own camera
             frame and the per-frame results are aggregated, reporting the spread rather than
             hiding it. A polygon is not attempted: without poses the frames share no
             coordinate system and any "room shape" would be fabricated.

Deciding on `T_wc` rather than on `scene.tier` is deliberate. A video whose odometry failed
must degrade to the unposed path, not pretend it has a trajectory.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np

from pipeline.confidence.intervals import area_measurement, length_measurement
from pipeline.geometry.fuse import fuse_frames
from pipeline.geometry.lift import lift
from pipeline.geometry.openings import detect_openings
from pipeline.geometry.planes import CAMERA_UP, fit_floor_ceiling
from pipeline.geometry.walls import (ceiling_height, corners_with_status, extract_walls,
                                     wall_pair_dimensions)
from pipeline.types import RoomCapture, Scale

# Plane inlier band by tier, in metres. LiDAR range noise is ~1 cm; lifted monocular depth is
# several times looser, and too tight a band fragments a wall into pieces.
PLANE_THRESHOLD = {"lidar": 0.03, "video": 0.05, "photo": 0.05}


def _measure_cloud(points: np.ndarray, normals: np.ndarray, tier: str,
                   gravity_prior: Optional[np.ndarray], camera_at_origin: bool,
                   want_polygon: bool, pix: Optional[np.ndarray] = None,
                   image: Optional[np.ndarray] = None) -> dict:
    """Point cloud -> every geometric quantity the output contract asks for.

    `pix` and `image` are optional and only used to run the first-pass damage detector: it
    needs the source RGB and a per-point pixel mapping, which exist on the per-frame path but
    not on a fused multi-frame cloud. When they are absent the `damage`, `surfaces` and
    `scope_items` keys are simply not set, and the schema adapter reports empty arrays.
    """
    out: dict = {"n_points": int(len(points))}
    got = fit_floor_ceiling(points, normals,
                            threshold=PLANE_THRESHOLD.get(tier, 0.05),
                            gravity_prior=gravity_prior,
                            camera_at_origin=camera_at_origin)
    if got is None:
        out["error"] = "no planes fitted"
        return out

    g, floor, ceiling, walls, score = got
    out["gravity"] = np.round(g, 4).tolist()
    out["selection_score"] = round(float(score), 4)
    out["n_walls"] = len(walls)

    if ceiling is None:
        # Abstention, not a failure. The brief penalises confident garbage, so a frame that
        # cannot defend a floor/ceiling pair reports why instead of a number.
        out["ceiling_height"] = None
        out["abstained"] = True
        out["abstain_reason"] = "no defensible floor/ceiling pair"
    elif floor is not None:
        h, spread = ceiling_height(floor, ceiling, points)
        if np.isfinite(h):
            out["ceiling_height"] = float(h)
            out["ceiling_spread_m"] = float(spread)

    out["wall_pairs"] = wall_pair_dimensions(walls, g, points)[:6]

    # Corners, kept and rejected. Reported as counts here because the comparison between the
    # photo approaches turns on whether multi-view recovers corners a single view cannot see.
    cands = corners_with_status(walls, floor, points, g)
    out["corners_accepted"] = sum(1 for c in cands if c.accepted)
    out["corners_rejected"] = sum(1 for c in cands if not c.accepted)
    out["corners"] = [c.to_json() for c in cands if c.accepted][:12]

    if want_polygon and floor is not None:
        geo = extract_walls(points, floor, ceiling, walls=walls, gravity=g)
        if geo is None:
            out["polygon"] = None
            out["polygon_reason"] = "walls did not close into a polygon"
        else:
            out["polygon"] = {
                "corners": np.round(geo.corners, 4).tolist(),
                "wall_lengths": np.round(geo.wall_lengths, 4).tolist(),
                "floor_area": round(float(geo.floor_area), 4),
                "walls_used": geo.walls_used,
                "walls_dropped": geo.walls_dropped,
            }

    try:
        out["openings"] = [o.to_json() for o in detect_openings(walls, points, g, floor)]
    except Exception as exc:                       # never let a first-pass detector kill a run
        out["openings"] = []
        out["openings_error"] = f"{type(exc).__name__}: {exc}"

    if pix is not None and image is not None:
        try:
            out.update(_measure_damage(walls, ceiling, points, pix, image, g, floor))
        except Exception as exc:                   # first-pass detector, never fatal
            out["damage"] = []
            out["damage_error"] = f"{type(exc).__name__}: {exc}"

    return out


def _measure_damage(walls, ceiling, points: np.ndarray, pix: np.ndarray, image: np.ndarray,
                    g: np.ndarray, floor) -> dict:
    """First-pass damage on the classified surfaces of one frame.

    Out of scope for scoring in this submission (no staged damage, unfitted thresholds), but
    the output contract lists per-surface damage, so the stage runs and emits rather than
    reporting a permanent empty array. Every region carries the detector's own `notes` saying
    the thresholds are defaults.
    """
    from pipeline.damage.detect import detect_damage_on_wall
    from pipeline.damage.rules import evaluate as evaluate_rule
    from pipeline.damage.scope import line_items

    surfaces = [(f"w{i}", "wall", w) for i, w in enumerate(walls)]
    if ceiling is not None:
        surfaces.append(("ceiling", "ceiling", ceiling))

    regions = []
    surface_json = []
    for sid, stype, plane in surfaces:
        found = detect_damage_on_wall(plane, sid, stype, points, pix, image, g, floor)
        if found:
            surface_json.append({"id": sid, "type": stype})
        regions.extend(found)

    damage_json = []
    for r in regions:
        d = r.to_json()
        d["concealed_flag"] = evaluate_rule(r, r.surface_type)
        damage_json.append(d)

    return {"damage": damage_json, "surfaces": surface_json,
            "scope_items": line_items(regions)}


def _write_debug(frame, pts, nrm, pix, m: dict, room: RoomCapture, debug_dir: str) -> str:
    """Re-fit the same cloud, keeping the labelled planes, and render a diagnostic sheet.

    _measure_cloud discards the plane objects once it has the numbers, so they are recomputed
    here with identical arguments. RANSAC is seeded deterministically, so this reproduces the
    exact planes that produced the numbers rather than a second, different opinion of them.
    """
    import os as _os

    from pipeline.geometry.planes import classify, estimate_gravity, extract_planes,         merge_coplanar, regularize
    from pipeline.output.debug import frame_sheet

    planes = extract_planes(pts, nrm, threshold=PLANE_THRESHOLD.get(room.tier, 0.05))
    if not planes:
        return ""
    planes = merge_coplanar(planes, pts)
    g = estimate_gravity(planes, prior=CAMERA_UP)
    planes, g, score = classify(planes, g, pts, True)
    planes = regularize(planes, g, pts)

    ch = m.get("ceiling_height")
    lines = [
        f"score {m.get('selection_score', 0):.4f}   walls {m.get('n_walls', 0)}",
        (f"ceiling {ch:.3f} m" if ch else "ABSTAINED"),
    ]
    if m.get("abstain_reason"):
        lines.append(m["abstain_reason"][:44])
    if m.get("openings"):
        lines.append(f"{len(m['openings'])} opening(s)")
    lines.append(f"gravity {np.round(g, 2).tolist()}")

    name = _os.path.splitext(_os.path.basename(frame.image_path))[0].replace("#", "_")
    return frame_sheet(
        frame.image, frame.depth, pix, planes,
        {"title": f"{room.room_id} / {name}", "lines": lines},
        _os.path.join(debug_dir, f"{room.room_id}_{name}.png"), stride=2)


def measure_room(room: RoomCapture, depth_backend=None, cache: bool = True,
                 work_px: int = 1024, debug_dir: Optional[str] = None,
                 photo_mode: str = "per_frame", drift_correction: bool = True) -> dict:
    """Measure one RoomCapture. Runs depth first where the tier does not supply it.

    `photo_mode` selects between the two photo-tier approaches, both runnable on the same raw
    photos and neither replacing the other:

      per_frame   each photo measured in its own camera frame, results aggregated by median.
                  The shipped default. Cannot produce a room polygon: unposed frames share no
                  coordinate system.

      multiview   photos are registered into one frame first (`capture/multiview.py`), which
                  populates `T_wc` and hands them to the same fused path LiDAR and video
                  already use. If registration cannot be verified, this falls back to
                  per_frame and says so rather than measuring a reconstruction it does not
                  trust.

    The mode only ever decides whether poses get *attempted*. Everything downstream still
    branches on whether a frame actually carries one.
    """
    t0 = time.time()
    frames = room.frames
    result: dict = {"room_id": room.room_id, "tier": room.tier, "n_frames": len(frames)}

    # LiDAR arrives with depth; photo and video need it inferred.
    if any(f.depth is None for f in frames):
        if depth_backend is None:
            result["error"] = "frames lack depth and no depth backend was supplied"
            return result
        from pipeline.capture.depth_cache import infer_cached
        for f in frames:
            if f.depth is not None:
                continue
            if f.image is None:
                continue
            if cache and not f.image_path.startswith(("", None)) and "#" not in f.image_path:
                f.depth, _ = infer_cached(depth_backend, f.image_path, f.image, f.K,
                                          work_px, use_cache=True)
            else:
                f.depth = depth_backend.infer(f.image, f.K).depth

    # Photo multiview: recover poses. Registration runs after depth because it aligns
    # 3D-to-3D and needs both frames' depth maps.
    result["approach"] = {"photo": f"photo_{photo_mode}"}.get(room.tier, room.tier)
    # Gated on the REQUESTED MODE and on the absence of poses, never on the tier name. A video
    # whose odometry failed arrives here unposed exactly like a photo folder does, and if the
    # caller asked for multiview it should get the same treatment - the tier label is not what
    # makes registration applicable, the missing poses are.
    if photo_mode.startswith("multiview") and all(f.T_wc is None for f in frames):
        from pipeline.capture.multiview import apply_poses
        usable = [f for f in frames if f.depth is not None]
        t_reg = time.time()

        if photo_mode == "multiview_unvalidated":
            # The original path, unchanged: maximum-support spanning tree, every edge trusted.
            # Kept as a real runnable baseline rather than a remembered number, so the fix
            # loop can show per-frame -> naive multiview -> gated multiview as three runs.
            from pipeline.capture.register import match_all, register
            reg = register(usable, match_all(usable))
            result["registration"] = {
                "validated": False, "n_frames": len(usable),
                "n_registered": reg.n_registered, "reference": reg.reference,
                "residual_median_cm": (None if not np.isfinite(reg.residual_median_m)
                                       else round(reg.residual_median_m * 100, 2)),
                "residual_p90_cm": (None if not np.isfinite(reg.residual_p90_m)
                                    else round(reg.residual_p90_m * 100, 2)),
                "per_pair": reg.per_pair,
                "seconds": round(time.time() - t_reg, 1),
            }
            apply_poses(usable, reg.poses)
        else:
            from pipeline.capture.multiview import register_multiview
            reg = register_multiview(usable)
            result["registration"] = {"validated": True, **reg.to_json()}
            result["registration_report"] = reg.report_lines()
            if reg.trustworthy:
                apply_poses(usable, reg.poses)
            else:
                # Refusing the reconstruction is a result, not an error. Measuring a cloud
                # built on poses that contradict each other yields a confident number from
                # geometry that never existed - the failure this gate exists to catch.
                result["multiview_rejected"] = True
                result["multiview_reject_reason"] = (
                    f"registration not trustworthy ({reg.summary}); "
                    f"falling back to per-frame measurement")

    posed = [f for f in frames if f.T_wc is not None and f.depth is not None]
    result["posed_frames"] = len(posed)

    stitched = False
    if len(frames) >= 6 and room.tier in ("video", "lidar"):
        # A continuous video/lidar capture that walked through several rooms is not
        # detectable from the tier alone - our own captures are each one room, and there is
        # no flag for "this one has more". Segmentation answers it, in two stages:
        #
        # 1. DOORWAY CROSSINGS, on every sampled frame (posed or not). A room change is
        #    confirmed by the scene itself changing sharply - passing through a doorway - not
        #    inferred from where a successfully-posed camera happened to sit. This is the
        #    PRIMARY method because it survives exactly the case that breaks the fallback: on
        #    the real 60-keyframe capture only 14 of 60 frames posed at all, and 14 positions
        #    spread across two rooms never built enough camera-density in any one grid cell
        #    for the trajectory method below to see a room. Crossing detection needs no pose.
        #
        # 2. TRAJECTORY DENSITY (`segment_rooms`), only if (1) finds nothing. Kept rather than
        #    replaced: it is what is validated on the one real multi-room result this project
        #    has produced so far, and a capture with near-complete posing (LiDAR, most of the
        #    time) gains nothing from crossing detection that density clustering does not
        #    already give it more cheaply.
        #
        # A single-room walk triggers neither: no crossing is ever detected, and density
        # clustering finds one dense cluster - so this check is safe to run unconditionally.
        from pipeline.geometry.segment import segment_rooms, segment_rooms_by_doorway
        segments, seg_report = segment_rooms_by_doorway(
            frames, posed, room.tier, PLANE_THRESHOLD.get(room.tier, 0.03))
        result["doorway_segmentation"] = seg_report

        if segments is None and len(posed) >= 6:
            # A coarse, heavily-strided fuse purely to get a gravity direction for projecting
            # the trajectory onto the floor plane - discarded immediately after. It never
            # feeds into any reported measurement: if this capture turns out to be one room,
            # `_measure_cloud` below computes its own gravity from the properly-strided fused
            # cloud, exactly as it always has; if it is several rooms, `stitch_posed_capture`
            # re-estimates gravity itself, at the finer stride each room's measurement uses.
            quick = fuse_frames(posed, stride=8)
            quick_prior = None
            if room.tier == "lidar":
                from pipeline.capture.lidar import ARKIT_WORLD_UP
                quick_prior = ARKIT_WORLD_UP
            if len(quick.points):
                from pipeline.geometry.planes import estimate_gravity, extract_planes, \
                    merge_coplanar
                quick_planes = merge_coplanar(
                    extract_planes(quick.points, quick.normals,
                                   threshold=PLANE_THRESHOLD.get(room.tier, 0.03)),
                    quick.points)
                quick_gravity = estimate_gravity(quick_planes, prior=quick_prior)
                density_segments = segment_rooms(posed, quick_gravity)
                if len(density_segments) > 1:
                    segments = density_segments
                    result["doorway_segmentation"]["fallback"] = "trajectory density"

        # `segments` is only ever non-None with length > 1 here: `segment_rooms_by_doorway`
        # returns None below 2 confirmed rooms, and the density fallback only overwrites
        # `segments` when it found more than one cluster. So reaching here IS the split.
        if segments is not None:
            from pipeline.stitching.stitch import stitch_posed_capture
            stitch_result = stitch_posed_capture(posed, room, segments, room.tier,
                                                 drift_correction=drift_correction)
            if stitch_result is not None:
                result["mode"] = "stitched"
                result.update(stitch_result)
                stitched = True
            else:
                # Segmentation proposed a split; nothing confirmed it as real (no shared wall
                # between any pair - see find_adjacent_rooms). Falling through to the
                # single-room path below measures every posed frame as one room, which is
                # what this capture was until proven otherwise.
                result["segmentation_rejected"] = (
                    f"{len(segments)} segment(s) proposed, but no pair could be confirmed as "
                    f"separate rooms; measuring as one room")

    if stitched:
        pass                              # stitch_posed_capture already measured everything
    elif len(posed) >= 3:
        result["mode"] = "fused"
        fc = fuse_frames(posed, stride=2)
        result["fused"] = {"n_raw": fc.n_raw, "n_points": int(len(fc.points)),
                           "n_frames": fc.n_frames}
        # A fused cloud lives in a world frame with the camera somewhere inside it, so the
        # camera-at-origin prior does not apply and gravity comes from the reconstruction.
        prior, at_origin = None, False
        if room.tier == "lidar":
            from pipeline.capture.lidar import ARKIT_WORLD_UP
            prior = ARKIT_WORLD_UP
        elif room.tier == "photo":
            # Photo multiview has no external gravity reference, but its world frame IS the
            # reference camera's frame - poses[ref] is identity by construction. So the
            # hold-the-phone-level prior that guards the per-frame path applies here
            # unchanged, and the reference camera really is at the origin. Without this the
            # gravity vote runs unconstrained on a fused photo cloud, which is the exact
            # failure that made it classify walls as floors in the first place.
            prior, at_origin = CAMERA_UP, True
        result.update(_measure_cloud(fc.points, fc.normals, room.tier, prior,
                                     camera_at_origin=at_origin, want_polygon=True))
        if debug_dir and len(fc.points):
            from pipeline.output.debug import topdown_panel
            import os as _os
            g = np.asarray(result.get("gravity", [0, 1, 0]), dtype=float)
            result["debug_topdown"] = topdown_panel(
                fc.points, g, _os.path.join(debug_dir, f"{room.room_id}_topdown.png"))
    else:
        result["mode"] = "per_frame"
        per = []
        damage_all, surfaces_all, scope_all = [], [], []
        for f in frames:
            if f.depth is None or f.K is None:
                continue
            pts, nrm, pix = lift(f.depth, f.K, stride=2, return_pixels=True)
            if len(pts) < 2000:
                continue
            m = _measure_cloud(pts, nrm, room.tier, CAMERA_UP,
                               camera_at_origin=True, want_polygon=False,
                               pix=pix, image=f.image)
            m["frame"] = f.image_path
            if debug_dir:
                m["debug_sheet"] = _write_debug(f, pts, nrm, pix, m, room, debug_dir)
            # Damage is per frame with no cross-frame association yet, so ids are prefixed by
            # frame to keep them unique and every region is kept - a physical patch seen in
            # three photos appears three times, stated rather than silently deduplicated.
            fkey = f.image_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            for d in m.get("damage", []):
                d["id"] = f"{fkey}:{d['id']}"
                d["surface_id"] = f"{fkey}:{d['surface_id']}"
                d["frame"] = fkey
                damage_all.append(d)
            for s in m.get("surfaces", []):
                surfaces_all.append({**s, "id": f"{fkey}:{s['id']}"})
            for it in m.get("scope_items", []):
                it["id"] = f"{fkey}:{it['id']}"
                it["damage_id"] = f"{fkey}:{it['damage_id']}"
                it["surface_id"] = f"{fkey}:{it['surface_id']}"
                scope_all.append(it)
            per.append(m)
        result["per_frame"] = per
        result["damage"] = damage_all
        result["surfaces"] = surfaces_all
        result["scope_items"] = scope_all
        if damage_all:
            result["damage_note"] = ("first-pass detector, unfitted thresholds, no cross-frame "
                                     "association; out of scope for scoring in this submission")

        heights = [m["ceiling_height"] for m in per if m.get("ceiling_height")]
        result["reported_frames"] = len(heights)
        result["abstained_frames"] = len(per) - len(heights)
        if heights:
            h = np.array(heights)
            result["ceiling_height"] = float(np.median(h))     # median: robust to one bad frame
            result["ceiling_height_sd"] = float(h.std())
        result["polygon"] = None
        result["polygon_reason"] = (
            "unposed frames share no coordinate system; a polygon would be fabricated")

    # Confidence intervals on whatever survived.
    scale = room.scale or Scale()
    if result.get("ceiling_height"):
        result["ceiling_height_measurement"] = length_measurement(
            result["ceiling_height"], room.tier, scale, "floor-to-ceiling plane separation"
        ).to_json()
    poly = result.get("polygon")
    if poly:
        result["wall_length_measurements"] = [
            length_measurement(L, room.tier, scale, "corner-to-corner").to_json()
            for L in poly["wall_lengths"]
        ]
        result["floor_area_measurement"] = area_measurement(
            poly["floor_area"], room.tier, scale, "polygon area").to_json()

    result["seconds"] = round(time.time() - t0, 1)
    return result
