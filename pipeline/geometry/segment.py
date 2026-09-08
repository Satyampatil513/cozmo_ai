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
