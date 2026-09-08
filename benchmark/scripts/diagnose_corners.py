"""Why is a visually obvious corner missing from the 3D reconstruction?

    python benchmark/scripts/diagnose_corners.py --room "Room 1"

Answers one question per corner, and refuses to answer it with "the capture lacks the corner"
until the plane representation has been ruled out.

Per frame it produces four panels:

    1  original
    2  EVERY fitted plane, not only the ones selected as room walls, labelled by kind
    3  every wall-wall plane intersection, projected into the image as a line, plus the
       wall-wall-floor / wall-wall-ceiling points
    4  visual corner candidates from a 2D line detector, coloured by whether a 3D plane
       intersection explains them

Visual corners come from long Hough segments intersected pairwise where they are close to
perpendicular and the intersection sits near both segments. That finds structural corners -
wall meets wall, wall meets ceiling - and largely ignores texture corners, which a Harris or
Shi-Tomasi detector would flood the image with.

Each unmatched visual corner is then classified into one of the failure modes by inspecting
the geometry around its back-projected 3D position, rather than guessed at.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
import warnings

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from pipeline.capture.depth import get_backend                        # noqa: E402
from pipeline.capture.depth_cache import infer_cached                 # noqa: E402
from pipeline.capture.photo import WORK_PX, load                      # noqa: E402
from pipeline.capture.register import match_all, register             # noqa: E402
from pipeline.geometry.fuse import fuse_frames                        # noqa: E402
from pipeline.geometry.lift import lift                               # noqa: E402
from pipeline.geometry.planes import (                                # noqa: E402
    AXIS_TOL_DEG, classify, estimate_gravity, extract_planes, merge_coplanar, regularize,
)

OUT = os.path.join(ROOT, "out_diagnose")

# --- visual corner detection -------------------------------------------------------------
CANNY_LO, CANNY_HI = 60, 160
HOUGH_THRESH = 80
MIN_SEG_LEN = 90                 # px, at the 768x1024 working size
MAX_SEG_GAP = 12
PERP_MIN_DEG = 55.0              # two segments must be at least this far from parallel
NEAR_SEG_PX = 45                 # intersection must sit this close to both segments
CLUSTER_PX = 30                  # merge duplicate corner candidates

# --- matching a visual corner to a 3D plane intersection ---------------------------------
MATCH_PX = 55
PLANE_NEAR_M = 0.12              # a plane counts as "present at" a 3D point within this
NEAR_PARALLEL_DEG = 20.0         # two planes closer than this are one surface, fragmented


def _seg_angle(s):
    return np.degrees(np.arctan2(s[3] - s[1], s[2] - s[0])) % 180.0


def _seg_intersect(a, b):
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-6:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    return np.array([px, py])


def _dist_point_seg(p, s):
    a, b = np.array([s[0], s[1]], float), np.array([s[2], s[3]], float)
    ab = b - a
    t = np.clip(np.dot(p - a, ab) / max(1e-9, np.dot(ab, ab)), 0, 1)
    return float(np.linalg.norm(p - (a + t * ab)))


def visual_corners(gray: np.ndarray):
    """Structural corner candidates: intersections of long, near-perpendicular line pairs."""
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), CANNY_LO, CANNY_HI)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, HOUGH_THRESH,
                            minLineLength=MIN_SEG_LEN, maxLineGap=MAX_SEG_GAP)
    if lines is None or len(lines) == 0:
        return [], []
    # OpenCV 4 returns (N,1,4); OpenCV 5 returns (N,4). Normalise rather than assume.
    segs = [tuple(int(v) for v in row) for row in np.asarray(lines).reshape(-1, 4)]

    cands = []
    h, w = gray.shape[:2]
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            da = abs(_seg_angle(segs[i]) - _seg_angle(segs[j]))
            da = min(da, 180 - da)
            if da < PERP_MIN_DEG:
                continue
            p = _seg_intersect(segs[i], segs[j])
            if p is None or not (0 <= p[0] < w and 0 <= p[1] < h):
                continue
            if _dist_point_seg(p, segs[i]) > NEAR_SEG_PX or \
               _dist_point_seg(p, segs[j]) > NEAR_SEG_PX:
                continue
            cands.append(p)

    merged = []
    for p in cands:
        hit = next((m for m in merged if np.linalg.norm(m[0] - p) < CLUSTER_PX), None)
        if hit:
            hit[1].append(p)
        else:
            merged.append([p, [p]])
    return [np.mean(m[1], axis=0) for m in merged], segs


# --- 3D side ------------------------------------------------------------------------------
def plane_pair_lines(planes):
    """Every pair of non-parallel planes, as (point, direction, plane_a, plane_b)."""
    out = []
    cos_par = np.cos(np.radians(NEAR_PARALLEL_DEG))
    for i in range(len(planes)):
        for j in range(i + 1, len(planes)):
            a, b = planes[i], planes[j]
            if abs(float(a.normal @ b.normal)) > cos_par:
                continue
            d = np.cross(a.normal, b.normal)
            nd = np.linalg.norm(d)
            if nd < 1e-8:
                continue
            d = d / nd
            # Point on both planes closest to the origin.
            A = np.stack([a.normal, b.normal, d])
            p = np.linalg.solve(A, np.array([-a.d, -b.d, 0.0]))
            out.append((p, d, i, j))
    return out


def project_pts(P: np.ndarray, T_wc, K, shape):
    """World points -> pixels, with a mask for in-front-and-in-frame."""
    Pw = np.atleast_2d(P)
    Pc = (np.linalg.inv(T_wc) @ np.hstack([Pw, np.ones((len(Pw), 1))]).T).T[:, :3]
    ok = Pc[:, 2] > 0.15
    uv = (K @ Pc.T).T
    with np.errstate(invalid="ignore", divide="ignore"):
        uv = uv[:, :2] / uv[:, 2:3]
    h, w = shape[:2]
    ok &= (uv[:, 0] > -200) & (uv[:, 0] < w + 200) & (uv[:, 1] > -200) & (uv[:, 1] < h + 200)
    return uv, ok


def classify_failure(p3: np.ndarray, planes, gravity, points) -> str:
    """Why is there no 3D corner at this back-projected position?"""
    if p3 is None:
        return "depth sparsity/occlusion (no valid depth at the corner)"

    near = [(k, pl) for k, pl in enumerate(planes)
            if abs(float(pl.normal @ p3) + pl.d) < PLANE_NEAR_M]
    if len(near) == 0:
        return "missing plane (no fitted plane passes through this point)"
    if len(near) == 1:
        return "missing plane (only one surface fitted here, need two to make a corner)"

    cos_par = np.cos(np.radians(NEAR_PARALLEL_DEG))
    perp_pairs = [(near[i][1], near[j][1])
                  for i in range(len(near)) for j in range(i + 1, len(near))
                  if abs(float(near[i][1].normal @ near[j][1].normal)) <= cos_par]
    if not perp_pairs:
        return "fragmented plane (surfaces here are near-parallel: one wall fitted twice)"

    # Two non-parallel planes do pass through it, so the intersection exists in 3D and
    # something downstream discarded it.
    return "corner filtering (intersection exists but was rejected by extent/overshoot tests)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", default="Room 1")
    ap.add_argument("--capture", default=os.path.join(ROOT, "benchmark", "raw", "photo"))
    args = ap.parse_args()

    scene = load(args.capture)
    room = next(r for r in scene.rooms if r.room_id == args.room)
    be = get_backend("metric3d_v2")
    for f in room.frames:
        if f.depth is None:
            f.depth, _ = infer_cached(be, f.image_path, f.image, f.K, WORK_PX, True)

    reg = register(room.frames, match_all(room.frames))
    for f, T in zip(room.frames, reg.poses):
        f.T_wc = T
    posed = [f for f in room.frames if f.T_wc is not None]
    print(f"{args.room}: {len(posed)}/{len(room.frames)} registered, "
          f"residual {reg.residual_median_m*100:.1f} cm")

    fc = fuse_frames(posed, stride=2)
    planes = merge_coplanar(extract_planes(fc.points, fc.normals, threshold=0.05), fc.points)
    g = estimate_gravity(planes, prior=np.array([0.0, -1.0, 0.0]))
    planes, g, score = classify(planes, g, fc.points, camera_at_origin=False)
    planes = regularize(planes, g, fc.points)
    print(f"fused {len(fc.points):,} pts -> {len(planes)} planes "
          f"({collections.Counter(p.kind for p in planes)})")
    for k, p in enumerate(planes):
        print(f"   P{k}: {p.kind:<11} n={np.round(p.normal,2)} d={p.d:+.3f} "
              f"inliers={p.n_inliers}")

    lines = plane_pair_lines(planes)
    print(f"{len(lines)} non-parallel plane pairs -> candidate intersection lines\n")

    palette = [tuple(int(c) for c in cv2.applyColorMap(
        np.array([[int(180 * i / 12)]], np.uint8), cv2.COLORMAP_HSV)[0, 0]) for i in range(12)]

    modes = collections.Counter()
    os.makedirs(OUT, exist_ok=True)
    sheets = []

    for f in room.frames:
        name = os.path.basename(f.image_path)
        if f.T_wc is None:
            print(f"{name}: unregistered, skipped")
            continue
        rgb = cv2.cvtColor(f.image, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(f.image, cv2.COLOR_RGB2GRAY)
        h, w = gray.shape[:2]

        # --- panel 2: every plane, from this frame's own points -------------------------
        pts_f, nrm_f, pix_f = lift(f.depth, f.K, stride=2, return_pixels=True)
        world = (f.T_wc[:3, :3] @ pts_f.T).T + f.T_wc[:3, 3]
        p_planes = rgb.copy()
        overlay = np.zeros_like(rgb)
        for k, pl in enumerate(planes):
            dist = np.abs(world @ pl.normal + pl.d)
            m = dist < 0.06
            if m.sum() < 50:
                continue
            rc = pix_f[m]
            mask = np.zeros((h, w), np.uint8)
            mask[np.clip(rc[:, 0], 0, h-1), np.clip(rc[:, 1], 0, w-1)] = 1
            overlay[cv2.dilate(mask, np.ones((7, 7), np.uint8)) > 0] = palette[k % 12]
        p_planes = cv2.addWeighted(p_planes, 0.45, overlay, 0.55, 0)

        # --- panel 3: projected plane-pair intersection lines and corner points ---------
        p_inter = rgb.copy()
        proj_corners = []
        for (p0, d, ia, ib) in lines:
            ts = np.linspace(-8, 8, 220)
            P = p0[None, :] + ts[:, None] * d[None, :]
            uv, ok = project_pts(P, f.T_wc, f.K, gray.shape)
            uv = uv[ok]
            if len(uv) < 2:
                continue
            col = palette[(ia * 3 + ib) % 12]
            for a, b in zip(uv[:-1], uv[1:]):
                if np.linalg.norm(a - b) < 80:
                    cv2.line(p_inter, tuple(a.astype(int)), tuple(b.astype(int)), col, 2)
            # corner points: this line meeting any third plane
            for kc, pc in enumerate(planes):
                if kc in (ia, ib):
                    continue
                denom = float(pc.normal @ d)
                if abs(denom) < 1e-6:
                    continue
                t = -(float(pc.normal @ p0) + pc.d) / denom
                X = p0 + t * d
                uvp, okp = project_pts(X, f.T_wc, f.K, gray.shape)
                if okp[0] and 0 <= uvp[0, 0] < w and 0 <= uvp[0, 1] < h:
                    proj_corners.append((uvp[0], X))
        for uvp, _X in proj_corners:
            cv2.drawMarker(p_inter, tuple(uvp.astype(int)), (0, 255, 255),
                           cv2.MARKER_TILTED_CROSS, 22, 2)

        # --- panel 4: visual corners, matched vs unmatched -------------------------------
        vc, segs = visual_corners(gray)
        p_vis = rgb.copy()
        for s in segs:
            cv2.line(p_vis, (s[0], s[1]), (s[2], s[3]), (70, 70, 70), 1)
        n_match = 0
        for p in vc:
            matched = any(np.linalg.norm(p - uvp) < MATCH_PX for uvp, _ in proj_corners)
            if matched:
                n_match += 1
                cv2.circle(p_vis, tuple(p.astype(int)), 13, (90, 230, 90), 2)
            else:
                cv2.circle(p_vis, tuple(p.astype(int)), 13, (60, 60, 255), 2)
                cv2.drawMarker(p_vis, tuple(p.astype(int)), (60, 60, 255),
                               cv2.MARKER_CROSS, 20, 2)
                # back-project through depth to classify the failure
                u, v = int(round(p[0])), int(round(p[1]))
                z = f.depth[min(max(v, 0), h-1), min(max(u, 0), w-1)]
                p3 = None
                if np.isfinite(z) and z > 0.2:
                    K = f.K
                    cam = np.array([(p[0]-K[0,2])*z/K[0,0], (p[1]-K[1,2])*z/K[1,1], z])
                    p3 = f.T_wc[:3, :3] @ cam + f.T_wc[:3, 3]
                modes[classify_failure(p3, planes, g, fc.points)] += 1

        for img, label in ((rgb, f"{name}"), (p_planes, f"all {len(planes)} planes"),
                           (p_inter, f"{len(proj_corners)} projected 3D corners"),
                           (p_vis, f"visual corners {n_match}/{len(vc)} matched")):
            cv2.rectangle(img, (0, 0), (img.shape[1], 30), (0, 0, 0), -1)
            cv2.putText(img, label, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (255, 255, 255), 1, cv2.LINE_AA)
        row = np.hstack([rgb, p_planes, p_inter, p_vis])
        sheets.append(row)
        print(f"{name}: {len(vc)} visual corners, {n_match} matched, "
              f"{len(proj_corners)} projected 3D corners")

    if sheets:
        wmax = max(s.shape[1] for s in sheets)
        sheet = np.vstack([cv2.copyMakeBorder(s, 0, 8, 0, wmax - s.shape[1],
                                              cv2.BORDER_CONSTANT, value=(25, 25, 25))
                           for s in sheets])
        sc = min(1.0, 2600 / sheet.shape[1])
        sheet = cv2.resize(sheet, (int(sheet.shape[1]*sc), int(sheet.shape[0]*sc)),
                           interpolation=cv2.INTER_AREA)
        path = os.path.join(OUT, f"{args.room}_corner_diagnosis.png")
        cv2.imwrite(path, sheet)
        print(f"\nwrote {path}")

    print("\n=== dominant failure modes (unmatched visual corners) ===")
    tot = sum(modes.values()) or 1
    for m, c in modes.most_common():
        print(f"  {c:4d}  ({c/tot*100:4.1f}%)  {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
