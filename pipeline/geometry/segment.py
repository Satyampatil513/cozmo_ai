"""Split ONE continuous posed capture into rooms, from the camera trajectory alone.

WHY THIS EXISTS. `select_room_walls` (walls.py) requires a wall to have nothing behind it.
That test is correct for a single room and fails by design the moment a capture spills into
the next space: a doorway lets the sensor see past what should be a bounding wall, so nothing
qualifies and the polygon returns `None`. This was flagged as a known limitation before any
multi-room capture existed (`OVERNIGHT_PROGRESS.md`, "Room polygon fails on captures that
spill into the next space") and is the reason video/lidar stitching could not be built until
the cloud is split into per-room pieces FIRST.

WHY THE CAMERA TRAJECTORY, NOT THE POINT CLOUD. The point cloud is where furniture, glass and
the far side of a doorway all live, and splitting it directly requires deciding which walls
are real before any room boundary exists to test them against - circular. The camera
trajectory has no such problem: a person capturing a room walks around inside it and lingers,
so camera positions cluster densely; crossing to the next room is a single quick transit, so
the cluster of one room and the cluster of the next are joined by a thin, sparse bridge. That
bridge is the doorway, found without looking at a single 3D point.

CALIBRATION, STATED PLAINLY. Every threshold below is derived from the CAPTURE PROTOCOL - a
person walks a room at a normal pace and a doorway is ~0.9 m wide - not fitted to real
multi-room data, because none exists yet: our own video and LiDAR captures are each of ONE
room (`benchmark/raw/{video,lidar}/README.md`), and the team's LiDAR capture is a single scan
whose raw file is no longer on disk. This module is validated against SYNTHETIC ground truth
(`tests/test_stitching.py`) with the same posture as `tests/test_geometry.py` uses for the
single-room pipeline, and is flagged untested on real data until a multi-room capture exists.

SAFE BY CONSTRUCTION ON EVERY CAPTURE WE HAVE TODAY. A capture that never leaves one room
produces one dense cluster and no bridge, so `segment_rooms` returns a single segment holding
every frame. Every existing single-room video/lidar result is therefore unaffected - not
because of a special case, but because there is nothing for the algorithm to split.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pipeline.geometry.planes import _floor_basis
from pipeline.types import Frame

CELL_M = 0.6                     # grid cell for camera-density rasterisation
MIN_CORE_DENSITY = 3             # a cell this dense was lingered in, not walked through once
MIN_ROOM_FRAMES = 3              # matches measure.py's own floor for attempting a fused fit


@dataclass
class RoomSegment:
    frame_indices: list[int]     # positions into the posed-frame list that was segmented
    is_bridge: bool = False      # this segment is a corridor/doorway crossing, not a room


def _cell_of(xy: np.ndarray, cell_m: float) -> np.ndarray:
    return np.floor(xy / cell_m).astype(int)


def _connected_components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """4-connected components over a set of integer grid cells. No scipy/sklearn dependency -
    the graph is a few hundred nodes at most, a plain BFS is simpler to audit than pulling in
    a labelling library for it."""
    remaining = set(cells)
    comps = []
    while remaining:
        seed = next(iter(remaining))
        stack, comp = [seed], set()
        while stack:
            c = stack.pop()
            if c in comp or c not in remaining:
                continue
            comp.add(c)
            cx, cy = c
            stack += [(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)]
        remaining -= comp
        comps.append(comp)
    return comps


def segment_rooms(posed_frames: list[Frame], gravity: np.ndarray,
                  cell_m: float = CELL_M, min_core_density: int = MIN_CORE_DENSITY,
                  min_room_frames: int = MIN_ROOM_FRAMES) -> list[list[int]]:
    """Camera positions -> room membership. Returns frame-index lists, in walk order.

    Method: rasterise camera positions onto the floor plane; a cell visited `min_core_density`
    times or more is a room "core" (lingering coverage), a component of core cells is one
    room, and every other cell - including a doorway's bridge cells - is grown into whichever
    room reaches it first by flood fill, so every frame ends up in exactly one room.
    """
    n = len(posed_frames)
    if n < 2 * min_room_frames:
        return [list(range(n))]           # too few frames to be more than one room

    centres = np.array([f.T_wc[:3, 3] for f in posed_frames])
    e1, e2 = _floor_basis(gravity)
    xy = np.stack([centres @ e1, centres @ e2], axis=1)
    cells = _cell_of(xy, cell_m)

    density: dict[tuple[int, int], int] = {}
    cell_frames: dict[tuple[int, int], list[int]] = {}
    for i, c in enumerate(map(tuple, cells)):
        density[c] = density.get(c, 0) + 1
        cell_frames.setdefault(c, []).append(i)

    core = {c for c, d in density.items() if d >= min_core_density}
    comps = _connected_components(core)
    if len(comps) <= 1:
        return [list(range(n))]           # one room, or too sparse to say otherwise

    # Assign every cell (core and bridge alike) to a room by nearest-centre flood fill, so a
    # doorway's bridge cells - low density by definition - still end up somewhere rather than
    # being dropped. BFS from every core cell simultaneously; a cell is claimed by whichever
    # room's frontier reaches it first, ties broken by Euclidean distance to that room's
    # nearest already-claimed cell.
    label = {c: k for k, comp in enumerate(comps) for c in comp}
    all_cells = set(density.keys())
    frontier = set(label.keys())
    while frontier:
        nxt: dict[tuple[int, int], tuple[int, float]] = {}
        for (cx, cy) in frontier:
            k = label[(cx, cy)]
            for nb in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if nb in label or nb not in all_cells:
                    continue
                d = float(np.hypot(nb[0] - cx, nb[1] - cy))
                if nb not in nxt or d < nxt[nb][1]:
                    nxt[nb] = (k, d)
        if not nxt:
            break
        for c, (k, _d) in nxt.items():
            label[c] = k
        frontier = set(nxt.keys())

    groups: dict[int, list[int]] = {}
    for c, k in label.items():
        groups.setdefault(k, []).extend(cell_frames.get(c, []))

    # A room with too few frames to fuse (measure.py's own floor of 3) is not a room on its
    # own - fold it into whichever neighbour it shares the most boundary cells with, which is
    # almost always the room a brief pause near a doorway actually belongs to.
    tiny = [k for k, idx in groups.items() if len(idx) < min_room_frames]
    for k in tiny:
        if k not in groups:
            continue
        idx = groups.pop(k)
        best, best_n = None, -1
        for c, lk in label.items():
            if lk == k:
                continue
            for nb in ((c[0] + 1, c[1]), (c[0] - 1, c[1]), (c[0], c[1] + 1), (c[0], c[1] - 1)):
                if label.get(nb) == k:
                    best_n_count = sum(1 for cc, ll in label.items() if ll == lk
                                       and any(label.get((cc[0] + dx, cc[1] + dy)) == k
                                              for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))))
                    if best_n_count > best_n:
                        best, best_n = lk, best_n_count
        if best is not None and best in groups:
            groups[best].extend(idx)
        elif groups:
            next(iter(groups.values())).extend(idx)

    if len(groups) <= 1:
        return [list(range(n))]

    # Walk order: each room ordered by the earliest frame index it contains, so the property
    # reads in the sequence someone would actually walk it rather than by cluster size.
    ordered = sorted(groups.values(), key=lambda idx: min(idx))
    return [sorted(idx) for idx in ordered]


# ================================================================================================
# Doorway-crossing segmentation: a room change is confirmed by leaving the room's own scene
# content, not by inferring it from camera trajectory density.
#
# WHY THIS EXISTS ALONGSIDE THE DENSITY METHOD ABOVE, RATHER THAN INSTEAD OF IT. Density
# clustering only ever looks at POSED frames, and pose is exactly the resource that runs out on
# a hard walk: on the real 60-keyframe capture only 14 posed at all (odometry coverage falling
# as the walk gets longer and crosses more transitions), and 14 positions spread across two
# rooms and a hallway never accumulate enough density in any one grid cell to register as a
# room. Density clustering was starved of the one input it needs, on the exact capture where a
# room change genuinely happened.
#
# A doorway crossing needs neither a pose nor a depth map to detect - just two RGB frames and
# a feature match, which survives every frame that failed full PnP odometry. So this runs on
# the FULL raw sampled sequence, in TIME order, independent of which frames later succeeded at
# posing, and gives segmentation a signal that does not disappear exactly when it is needed.
#
# THE METHOD, matching the shape of the idea directly: a person walking through one room and
# into the next does not gradually replace the scene - they walk through a doorway, and the
# view changes sharply and briefly around that one transit, while it stays highly self-similar
# on either side of it ("keep building the local map" describes exactly this: consecutive
# frames INSIDE a room share most of their scene). So a transition is a LOCAL DROP in
# frame-to-frame feature similarity, judged against the sequence's own distribution - the same
# "judged against this clip's own distribution" principle `video.py` already uses for blur,
# because a fixed absolute threshold cannot tell "this clip has fast transitions" from "this
# pair happens to be a transition".
# ================================================================================================

FEATURES_PER_FRAME = 2000
MATCH_RATIO_TEST = 0.75
# A transition pair's similarity must fall this many MADs below the sequence's own median to
# be flagged - not an absolute count, because how many features two frames share depends on
# the scene (a cluttered room matches richly; a plain hallway barely matches at all even
# frame-to-frame), and only the RELATIVE dip at a transition is informative.
TRANSITION_MAD_K = 2.5
MIN_SEGMENT_LEN = 3              # matches measure.py's own floor for attempting a fused fit


def _sift_features(gray: np.ndarray):
    import cv2
    return cv2.SIFT_create(nfeatures=FEATURES_PER_FRAME).detectAndCompute(gray, None)


def _match_similarity(desc_a, kp_a, desc_b, kp_b) -> float:
    """Good matches / the smaller of the two feature counts - a scene-complexity-normalised
    similarity, not a raw match count, so a plain wall (few features anywhere) and a cluttered
    room (thousands of features everywhere) are judged on the same scale."""
    import cv2
    if desc_a is None or desc_b is None or len(kp_a) < 8 or len(kp_b) < 8:
        return 0.0
    good = [m for m, n in cv2.BFMatcher().knnMatch(desc_a, desc_b, k=2)
           if m.distance < MATCH_RATIO_TEST * n.distance]
    return len(good) / max(1, min(len(kp_a), len(kp_b)))


def detect_transitions(frames: list) -> list[dict]:
    """Frames in TIME order -> the pairwise similarity trace and every flagged transition.

    Returns a list of `{"index": i, "similarity": s, "is_transition": bool}` for every
    consecutive pair (i, i+1) - the full trace, not just the flagged points, so a caller (or a
    person debugging a bad split) can see the whole signal rather than a single boolean per
    cut. Needs only `frame.image`; poses and depth are never touched.
    """
    import cv2
    n = len(frames)
    if n < 2:
        return []
    if any(f.image is None for f in frames):
        # The .r3d container carries an RGB frame per depth frame, but the LiDAR loader does
        # not currently decode it into `Frame.image` (only depth and pose are read) - decoding
        # every JPEG in a capture that is usually a single room would cost real time for a
        # signal that tier does not need: ARKit poses nearly every frame, so the density
        # method below already segments it correctly without ever looking at an image. Empty
        # trace here reads as "no transitions", which correctly routes the caller to that
        # fallback rather than crashing on a cv2 call over `None`.
        return []

    gray = [cv2.cvtColor(f.image, cv2.COLOR_RGB2GRAY) for f in frames]
    feats = [_sift_features(g) for g in gray]

    sims = [_match_similarity(feats[i][1], feats[i][0], feats[i + 1][1], feats[i + 1][0])
           for i in range(n - 1)]
    if not sims:
        return []

    arr = np.array(sims)
    med = float(np.median(arr))
    mad = 1.4826 * float(np.median(np.abs(arr - med))) or 1e-6      # MAD -> sigma, robust
    thresh = med - TRANSITION_MAD_K * mad

    return [{"index": i, "similarity": sims[i], "is_transition": sims[i] < thresh}
           for i in range(n - 1)]


def segment_by_doorway_crossings(frames: list) -> tuple[list[list[int]], list[dict]]:
    """The primary segmenter: cut the TIME-ordered frame sequence at detected doorway
    crossings, then re-identify any segment that is really a return to an earlier room.

    Cutting first, re-identifying second, mirrors the two-step idea directly: "confirm someone
    changed rooms" (the cut), then "after getting out from the same door, we know we are back
    at the previous room" (the merge, done separately by `reidentify_rooms` below once each
    segment has fitted walls to compare). A segment is judged by its own fitted walls, not its
    raw points, which is what makes "the same room, revisited" a well-posed geometric question.

    Returns (frame-index groups in walk order, the transition trace) - the trace travels with
    the groups so a caller can report exactly which pairs triggered a cut, not a bare room
    count with no evidence behind it.
    """
    n = len(frames)
    trace = detect_transitions(frames)
    cuts = [t["index"] for t in trace if t["is_transition"]]
    if not cuts:
        return [list(range(n))], trace

    bounds = [0] + [c + 1 for c in cuts] + [n]
    groups = [list(range(bounds[i], bounds[i + 1])) for i in range(len(bounds) - 1)]
    # A cut too close to the start or end of the walk (a brief stumble, not a real transit)
    # produces a sliver segment with nothing fusable in it - folded into its only neighbour
    # rather than kept as a room no measurement could ever be attempted on.
    i = 0
    while i < len(groups):
        if len(groups[i]) < MIN_SEGMENT_LEN:
            if i == 0 and len(groups) > 1:
                groups[1] = groups[i] + groups[1]
                groups.pop(i)
            elif i > 0:
                groups[i - 1] = groups[i - 1] + groups[i]
                groups.pop(i)
            else:
                i += 1
        else:
            i += 1
    return groups, trace


def reidentify_rooms(groups: list[list[int]], wall_lists: list[list],
                     centroids: list[np.ndarray | None],
                     angle_tol_deg: float = 12.0, offset_tol_m: float = 0.20,
                     centroid_tol_m: float = 1.5) -> tuple[list[list[int]], list[int]]:
    """Merge a later segment into an earlier one if their fitted walls COINCIDE AND they sit
    in roughly the same PLACE - "walked back into a room already mapped", not "a room next
    door that happens to share a wall line".

    THE PLACE CHECK IS NOT OPTIONAL, AND THIS WAS A MEASURED FAILURE OF THE FIRST VERSION.
    Two rooms sitting side by side in an ordinary row - the common case in a real flat - share
    the LINE their front and back walls sit on, even though they are different rooms: room A
    spanning x in [0,4] and room B spanning x in [4,7.5], both 3 m deep, both have a wall at
    y=0 and a wall at y=3 - the SAME two infinite planes, extended past where either room's
    own wall actually ends. Matching on (normal, offset) alone read that shared corridor line
    as "half of room B's walls coincide with room A's", which is exactly the `>= 2 of 4`
    threshold the original version required, and it merged two genuinely different rooms.

    So a candidate must ALSO have a point-cloud centroid within `centroid_tol_m` of the
    earlier segment's - "the same place", not merely "a matching pair of infinite lines". This
    is deliberately a tighter test than `find_adjacent_rooms`'s shared-wall match: adjacency
    wants two DIFFERENT rooms whose walls sit close but on OPPOSITE sides (a party wall) at
    DIFFERENT locations; re-identification wants the SAME room seen twice, so it requires wall
    coincidence AND spatial coincidence together, with no opposite-sides test at all - it is
    not looking for a boundary, it is looking for an identity.

    Returns (merged groups, `owner` - for each original group index, which output group index
    it was folded into). A segment identified as a revisit contributes its frames to the
    ORIGINAL room's reconstruction, which is exactly how "keep building the local map" should
    treat a second visit: more evidence for the same room, not a duplicate one.
    """
    cos_tol = np.cos(np.radians(angle_tol_deg))
    owner = list(range(len(groups)))       # each group starts owning itself

    for later in range(1, len(groups)):
        walls_later, c_later = wall_lists[later], centroids[later]
        if not walls_later or c_later is None:
            continue
        best_match, best_count = None, 0
        for earlier in range(later):
            if owner[earlier] != earlier:      # only match against a root, not another alias
                continue
            walls_earlier, c_earlier = wall_lists[earlier], centroids[earlier]
            if not walls_earlier or c_earlier is None:
                continue
            if float(np.linalg.norm(c_later - c_earlier)) > centroid_tol_m:
                continue        # not the same place - wall coincidence alone proves nothing
            matched = 0
            for wl in walls_later:
                for we in walls_earlier:
                    dot = float(wl.normal @ we.normal)
                    if abs(dot) < cos_tol:
                        continue
                    d_e = we.d if dot > 0 else -we.d
                    if abs(wl.d - d_e) <= offset_tol_m:
                        matched += 1
                        break
            if matched > best_count:
                best_match, best_count = earlier, matched
        # Require at least half of the later segment's own walls to coincide with the
        # candidate's - one or two incidentally-parallel walls is not enough evidence that two
        # segments are the same physical room, only that they are not obviously different ones.
        if best_match is not None and best_count >= max(2, len(walls_later) // 2):
            owner[later] = owner[best_match]

    merged: dict[int, list[int]] = {}
    for i, idx in enumerate(groups):
        merged.setdefault(owner[i], []).extend(idx)
    out_groups = [sorted(v) for v in merged.values()]
    return out_groups, owner


def segment_rooms_by_doorway(frames: list[Frame], posed: list[Frame], tier: str,
                             plane_threshold: float) -> tuple[list[list[int]] | None, dict]:
    """The full pipeline: detect crossings on ALL sampled frames, fit each candidate room's
    walls from its OWN posed subset, re-identify revisits, and return groups expressed as
    POSED-frame indices - the indexing `stitch_posed_capture` already expects.

    Runs on `frames` (every sampled frame, posed or not) rather than `posed` for the cut
    detection itself, because a frame that failed full PnP odometry still shows what the
    camera was looking at, and a doorway crossing is visible in that regardless. Posed frames
    are only needed afterward, to actually fuse and measure each side of a cut.

    Returns `(None, report)` when fewer than two rooms survive re-identification - the caller
    falls back to density clustering or the ordinary single-room path, and `report` states
    why (no transitions found, or every candidate room re-identified as the same one).
    """
    from pipeline.geometry.fuse import fuse_frames
    from pipeline.geometry.planes import estimate_gravity, extract_planes, fit_floor_ceiling, \
        merge_coplanar

    groups_full, trace = segment_by_doorway_crossings(frames)
    report = {"transitions_detected": sum(1 for t in trace if t["is_transition"]),
             "pairs_checked": len(trace), "candidate_rooms": len(groups_full)}
    if len(groups_full) <= 1:
        report["reason"] = "no doorway crossing detected in the frame sequence"
        return None, report

    # Map each candidate room's frames (in the FULL sequence) onto their position in `posed` -
    # the only frames `fuse_frames` can use. A candidate with too few posed frames to fuse is
    # dropped from re-identification rather than crashing it, and is folded into
    # `report["unfusable_candidates"]` so the gap is visible rather than silently absorbed.
    posed_index = {id(f): i for i, f in enumerate(posed)}
    groups_posed, unfusable = [], []
    for g in groups_full:
        idx = [posed_index[id(frames[i])] for i in g if id(frames[i]) in posed_index]
        if len(idx) >= MIN_SEGMENT_LEN:
            groups_posed.append(idx)
        elif idx:
            unfusable.append(len(idx))
    report["unfusable_candidates"] = unfusable
    if len(groups_posed) <= 1:
        report["reason"] = (f"{len(groups_full)} candidate room(s) found from crossings, but "
                            f"only {len(groups_posed)} had enough posed frames to fuse")
        return None, report

    prior = None
    if tier == "lidar":
        from pipeline.capture.lidar import ARKIT_WORLD_UP
        prior = ARKIT_WORLD_UP
    walls_per_group, centroids = [], []
    for idx in groups_posed:
        fc = fuse_frames([posed[i] for i in idx], stride=4)   # coarse: only walls are needed
        if not len(fc.points):
            walls_per_group.append([])
            centroids.append(None)
            continue
        centroids.append(fc.points.mean(axis=0))
        planes = merge_coplanar(extract_planes(fc.points, fc.normals,
                                               threshold=plane_threshold), fc.points)
        g = estimate_gravity(planes, prior=prior)
        got = fit_floor_ceiling(fc.points, fc.normals, threshold=plane_threshold,
                                gravity_prior=g, camera_at_origin=False)
        walls_per_group.append(got[3] if got else [])

    merged, owner = reidentify_rooms(groups_posed, walls_per_group, centroids)
    report["revisits_merged"] = sum(1 for i, o in enumerate(owner) if o != i)
    if len(merged) <= 1:
        report["reason"] = (f"{len(groups_posed)} candidate room(s) all re-identified as the "
                            f"same room")
        return None, report

    report["rooms_confirmed"] = len(merged)
    return merged, report
