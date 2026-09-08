"""Project the room's computed 3D corners back into every photo, labelled C1, C2, ...

    python benchmark/scripts/show_corners.py --room "Room 1"

This is the verification that actually tests the geometry, as opposed to the feature matching.
A corner here is a physical wall junction, computed once in 3D from the fused planes:

    corner = wall_i  intersect  wall_j  intersect  floor

Because there is exactly one 3D position per corner, projecting it into several photos taken
from different places is a real consistency test. If C1 lands on the same physical junction in
photo A and photo B, the walls, the floor, the poses and the scale all agree. If C1 sits on a
wall junction in one photo and floats in mid-air in another, something upstream is wrong and
this says which.

It also tests something no residual can: whether the corner is in the *right place at all*.
Feature correspondence only proves two photos agree with each other; a corner drawn on the
actual junction proves they agree with the room.
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from pipeline.capture.depth import get_backend                       # noqa: E402
from pipeline.capture.depth_cache import infer_cached                # noqa: E402
from pipeline.capture.photo import WORK_PX, load                     # noqa: E402
from pipeline.capture.register import match_all, register            # noqa: E402
from pipeline.geometry.fuse import fuse_frames                       # noqa: E402
from pipeline.geometry.planes import fit_floor_ceiling               # noqa: E402

OUT = os.path.join(ROOT, "out_register", "corners")

# A corner further than this beyond the observed extent of both its walls is an extrapolation
# of two planes into space they were never seen occupying, not a junction of the room.
MAX_OVERSHOOT_M = 0.8
MIN_WALL_ANGLE_DEG = 25.0


def corners_from_planes(walls, floor, points, gravity):
    """Every wall-wall-floor intersection that both walls actually support."""
    g = gravity / np.linalg.norm(gravity)
    out = []
    min_cos = np.cos(np.radians(90.0 - MIN_WALL_ANGLE_DEG))
    for a in range(len(walls)):
        for b in range(a + 1, len(walls)):
            wa, wb = walls[a], walls[b]
            if abs(float(wa.normal @ wb.normal)) > min_cos:
                continue                                  # too close to parallel
            A = np.stack([wa.normal, wb.normal, floor.normal])
            if abs(float(np.linalg.det(A))) < 1e-8:
                continue
            p = np.linalg.solve(A, -np.array([wa.d, wb.d, floor.d]))

            ok = True
            for w in (wa, wb):
                q = points[w.inliers]
                if np.any(p < q.min(axis=0) - MAX_OVERSHOOT_M) or \
                   np.any(p > q.max(axis=0) + MAX_OVERSHOOT_M):
                    ok = False
                    break
            if ok:
                out.append((p, a, b))

    # Order around the room so the numbering runs consistently rather than by loop index.
    if len(out) > 2:
        c = np.mean([p for p, _, _ in out], axis=0)
        e1 = np.array([1.0, 0, 0]) - float(np.array([1.0, 0, 0]) @ g) * g
        e1 /= max(1e-9, np.linalg.norm(e1))
        e2 = np.cross(g, e1)
        out.sort(key=lambda t: np.arctan2((t[0] - c) @ e2, (t[0] - c) @ e1))
    return out


def project(P_world: np.ndarray, T_wc: np.ndarray, K: np.ndarray, shape):
    """World point -> pixel in this camera, or None if behind it / outside the frame."""
    R, t = T_wc[:3, :3], T_wc[:3, 3]
    # T_wc maps camera->world, so world->camera is its inverse. The rotation may carry a
    # similarity scale from registration, so invert properly rather than transposing.
    Pc = np.linalg.inv(T_wc) @ np.append(P_world, 1.0)
    if Pc[2] <= 0.15:
        return None
    uv = K @ Pc[:3]
    u, v = uv[0] / uv[2], uv[1] / uv[2]
    h, w = shape[:2]
    if not (-60 <= u <= w + 60 and -60 <= v <= h + 60):
        return None
    return int(round(u)), int(round(v)), float(Pc[2])


def draw(frames, poses, corners, room_id: str) -> str:
    panels = []
    for i, (f, T) in enumerate(zip(frames, poses)):
        if T is None:
            continue
        img = cv2.cvtColor(f.image, cv2.COLOR_RGB2BGR).copy()
        n_drawn = 0
        for ci, (P, _a, _b) in enumerate(corners, 1):
            got = project(P, T, f.K, img.shape)
            if got is None:
                continue
            u, v, z = got
            col = tuple(int(c) for c in cv2.applyColorMap(
                np.array([[(ci * 37) % 256]], np.uint8), cv2.COLORMAP_HSV)[0, 0])
            cv2.drawMarker(img, (u, v), col, cv2.MARKER_CROSS, 34, 3)
            cv2.circle(img, (u, v), 15, col, 2)
            label = f"C{ci} {z:.1f}m"
            cv2.putText(img, label, (u + 18, v - 10), cv2.FONT_HERSHEY_SIMPLEX,
                        0.62, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(img, label, (u + 18, v - 10), cv2.FONT_HERSHEY_SIMPLEX,
                        0.62, col, 2, cv2.LINE_AA)
            n_drawn += 1
        tag = f"{os.path.basename(f.image_path)}  {n_drawn} corners"
        cv2.rectangle(img, (0, 0), (img.shape[1], 34), (0, 0, 0), -1)
        cv2.putText(img, tag, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                    (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(img)

    if not panels:
        return ""
    h = max(p.shape[0] for p in panels)
    panels = [cv2.copyMakeBorder(p, 0, h - p.shape[0], 0, 6, cv2.BORDER_CONSTANT,
                                 value=(25, 25, 25)) for p in panels]
    sheet = np.hstack(panels)
    scale = min(1.0, 2400 / sheet.shape[1])
    sheet = cv2.resize(sheet, (int(sheet.shape[1] * scale), int(sheet.shape[0] * scale)),
                       interpolation=cv2.INTER_AREA)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{room_id}_corners.png")
    cv2.imwrite(path, sheet)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", default=None)
    ap.add_argument("--capture", default=os.path.join(ROOT, "benchmark", "raw", "photo"))
    args = ap.parse_args()

    scene = load(args.capture)
    be = get_backend("metric3d_v2")

    for room in scene.rooms:
        if args.room and room.room_id != args.room:
            continue
        for f in room.frames:
            if f.depth is None:
                f.depth, _ = infer_cached(be, f.image_path, f.image, f.K, WORK_PX, True)

        reg = register(room.frames, match_all(room.frames))
        posed = [f for f, T in zip(room.frames, reg.poses) if T is not None]
        if len(posed) < 2:
            print(f"{room.room_id}: only {len(posed)} frames registered, skipping")
            continue
        for f, T in zip(room.frames, reg.poses):
            f.T_wc = T

        fc = fuse_frames(posed, stride=2)
        got = fit_floor_ceiling(fc.points, fc.normals, threshold=0.05,
                                gravity_prior=np.array([0.0, -1.0, 0.0]),
                                camera_at_origin=False)
        if got is None:
            print(f"{room.room_id}: no planes")
            continue
        g, floor, ceil, walls, score = got
        if floor is None or not walls:
            print(f"{room.room_id}: no floor or no walls")
            continue

        corners = corners_from_planes(walls, floor, fc.points, g)
        print(f"\n{room.room_id}: {len(walls)} walls -> {len(corners)} corners "
              f"({reg.n_registered}/{len(room.frames)} frames registered)")
        for ci, (P, a, b) in enumerate(corners, 1):
            print(f"   C{ci}: world {np.round(P, 3).tolist()}  from walls {a},{b}")

        path = draw(room.frames, reg.poses, corners, room.room_id)
        print(f"   wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
