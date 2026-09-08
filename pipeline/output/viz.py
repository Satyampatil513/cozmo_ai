"""Visual evidence for every geometry stage. Debugging tools, not product UI.

Each function returns TWO images:

    overlay   the original photo with the detection drawn on it, so a human can see whether
              the pipeline's idea of a wall sits on the actual wall
    clean     the same geometry on black, so the detection can be read without the photo
              competing with it

Four stages, matching the four things the pipeline claims to find:

    planes    every fitted plane, colour-coded by ID, floor/ceiling/wall labelled
    corners   wall-wall-floor intersections, ACCEPTED and REJECTED shown differently, with
              the rejection reason - a corner that is visually obvious but missing is either
              never computed or computed and thrown away, and those need different fixes
    openings  doors and windows with their jambs and measured width
    room      the final polygon, wall labels, dimensions and ceiling height

SCOPE: ordinary residential rooms with an approximately planar floor and ceiling and roughly
vertical walls. Staircases, split levels and multi-height ceilings are out of scope and will
render poorly; that is a documented limitation, not a bug to chase.
"""
from __future__ import annotations

import os

import cv2
import numpy as np

COLOR_FLOOR = (255, 160, 60)      # BGR
COLOR_CEILING = (80, 220, 100)
COLOR_WALL = (60, 150, 255)
COLOR_OTHER = (150, 150, 150)
COLOR_ACCEPT = (90, 240, 90)
COLOR_REJECT = (60, 60, 255)
COLOR_OPENING = (255, 220, 60)
BG = (18, 18, 18)

DILATE = 7
ALPHA = 0.5

PALETTE = [tuple(int(c) for c in cv2.applyColorMap(
    np.array([[int(180 * i / 12)]], np.uint8), cv2.COLORMAP_HSV)[0, 0]) for i in range(12)]


def _label(img, text, y=24, color=(255, 255, 255), scale=0.5, x=6):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    cv2.rectangle(img, (x - 2, y - th - 5), (x + tw + 4, y + 5), (0, 0, 0), -1)
    cv2.putText(img, text, (x + 1, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def project(P, T_wc, K, shape):
    """World points -> (pixels, valid mask). Valid means in front of the camera and near frame."""
    P = np.atleast_2d(np.asarray(P, dtype=float))
    Pc = (np.linalg.inv(T_wc) @ np.hstack([P, np.ones((len(P), 1))]).T).T[:, :3]
    ok = Pc[:, 2] > 0.15
    with np.errstate(invalid="ignore", divide="ignore"):
        uv = (K @ Pc.T).T
        uv = uv[:, :2] / uv[:, 2:3]
    h, w = shape[:2]
    ok &= np.isfinite(uv).all(axis=1)
    ok &= (uv[:, 0] > -400) & (uv[:, 0] < w + 400) & (uv[:, 1] > -400) & (uv[:, 1] < h + 400)
    return uv, ok


def _pair(rgb):
    """(overlay base, clean base) for one frame."""
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
    return bgr, np.full_like(bgr, BG)


# --- A. planes ----------------------------------------------------------------------------
def viz_planes(rgb, pix, planes, world_points=None, T_wc=None):
    """Every fitted plane painted back onto the frame it came from.

    `pix` maps each point of the cloud to its originating pixel (from lift(return_pixels=True)).
    When the cloud is a fused world cloud, pass `world_points` and `T_wc` so membership can be
    tested per frame instead.
    """
    over, clean = _pair(rgb)
    h, w = over.shape[:2]
    mask = np.full((h, w, 3), BG, np.uint8)
    kernel = np.ones((DILATE, DILATE), np.uint8)

    for k, pl in enumerate(planes):
        if world_points is not None:
            keep = np.abs(world_points @ pl.normal + pl.d) < 0.06
            rc = pix[keep]
        else:
            rc = pix[pl.inliers]
        if len(rc) < 40:
            continue
        m = np.zeros((h, w), np.uint8)
        m[np.clip(rc[:, 0], 0, h - 1), np.clip(rc[:, 1], 0, w - 1)] = 1
        m = cv2.dilate(m, kernel)
        col = {"floor": COLOR_FLOOR, "ceiling": COLOR_CEILING,
               "wall": PALETTE[k % 12]}.get(pl.kind, COLOR_OTHER)
        mask[m > 0] = col
        ys, xs = np.nonzero(m)
        if len(xs):
            cx, cy = int(xs.mean()), int(ys.mean())
            for img in (over, clean):
                _label(img, f"P{k} {pl.kind}", y=cy, x=max(4, cx - 40), scale=0.45)

    over = cv2.addWeighted(over, 1 - ALPHA, mask, ALPHA, 0)
    clean[mask.reshape(-1, 3).any(axis=1).reshape(h, w)] = mask[
        mask.reshape(-1, 3).any(axis=1).reshape(h, w)]
    for img, t in ((over, f"PLANES  {len(planes)} fitted"), (clean, "PLANES (clean)")):
        _label(img, t)
    return over, clean


# --- B. corners ---------------------------------------------------------------------------
def viz_corners(rgb, candidates, T_wc, K, walls=None, floor=None, ceiling=None):
    """Accepted and rejected corners, plus wall-floor and wall-ceiling intersection lines."""
    over, clean = _pair(rgb)
    shape = over.shape

    # wall-floor and wall-ceiling lines give the room's outline where corners are sparse
    for horiz, col, tag in ((floor, COLOR_FLOOR, "floor"), (ceiling, COLOR_CEILING, "ceil")):
        if horiz is None or not walls:
            continue
        for wi, w in enumerate(walls):
            d = np.cross(w.normal, horiz.normal)
            n = np.linalg.norm(d)
            if n < 1e-6:
                continue
            d = d / n
            A = np.stack([w.normal, horiz.normal, d])
            try:
                p0 = np.linalg.solve(A, np.array([-w.d, -horiz.d, 0.0]))
            except np.linalg.LinAlgError:
                continue
            ts = np.linspace(-6, 6, 160)
            uv, ok = project(p0[None, :] + ts[:, None] * d[None, :], T_wc, K, shape)
            uv = uv[ok]
            for a, b in zip(uv[:-1], uv[1:]):
                if np.linalg.norm(a - b) < 90:
                    for img in (over, clean):
                        cv2.line(img, tuple(a.astype(int)), tuple(b.astype(int)), col, 2)

    n_acc = n_rej = 0
    for ci, c in enumerate(candidates, 1):
        if not np.isfinite(c.point).all() or np.allclose(c.point, 0):
            continue
        uv, ok = project(c.point, T_wc, K, shape)
        if not ok[0]:
            continue
        p = tuple(uv[0].astype(int))
        if c.accepted:
            n_acc += 1
            for img in (over, clean):
                cv2.drawMarker(img, p, COLOR_ACCEPT, cv2.MARKER_CROSS, 30, 3)
                cv2.circle(img, p, 14, COLOR_ACCEPT, 2)
                _label(img, f"C{ci}", y=p[1] - 18, x=p[0] + 14, color=COLOR_ACCEPT, scale=0.55)
        else:
            n_rej += 1
            for img in (over, clean):
                cv2.drawMarker(img, p, COLOR_REJECT, cv2.MARKER_TILTED_CROSS, 22, 2)
                _label(img, f"x{ci} {c.reason[:26]}", y=p[1] - 14, x=p[0] + 12,
                       color=COLOR_REJECT, scale=0.4)

    for img, t in ((over, f"CORNERS  {n_acc} accepted, {n_rej} rejected"),
                   (clean, "CORNERS (clean)  green=accepted  red=rejected")):
        _label(img, t)
    return over, clean


# --- C. openings --------------------------------------------------------------------------
def viz_openings(rgb, openings, walls, T_wc, K, gravity):
    """Each detected opening as a rectangle on its wall, with jambs and measured width."""
    from pipeline.geometry.openings import _wall_frame

    over, clean = _pair(rgb)
    shape = over.shape
    g = gravity / np.linalg.norm(gravity)

    for oi, o in enumerate(openings, 1):
        wi = o["wall_index"] if isinstance(o, dict) else o.wall_index
        if wi >= len(walls):
            continue
        w = walls[wi]
        u, v = _wall_frame(w, g)
        cu, cv_ = (o["centre_uv"] if isinstance(o, dict) and "centre_uv" in o
                   else getattr(o, "centre_uv", (0.0, 0.0)))
        width = o["width_m"] if isinstance(o, dict) else o.width_m
        height = o["height_m"] if isinstance(o, dict) else o.height_m
        kind = o["kind"] if isinstance(o, dict) else o.kind
        conf = o["confidence"] if isinstance(o, dict) else o.confidence

        # A point on the wall plane at in-plane coords (a, b).
        base = -w.d * w.normal
        base = base - (float(base @ u)) * u - (float(base @ v)) * v

        def P(a, b):
            return base + a * u + b * v

        corners = [P(cu - width / 2, cv_ - height / 2), P(cu + width / 2, cv_ - height / 2),
                   P(cu + width / 2, cv_ + height / 2), P(cu - width / 2, cv_ + height / 2)]
        uv, ok = project(np.array(corners), T_wc, K, shape)
        if not ok.all():
            continue
        pts = uv.astype(int)
        col = COLOR_OPENING if kind != "window" else (200, 255, 120)
        for img in (over, clean):
            cv2.polylines(img, [pts.reshape(-1, 1, 2)], True, col, 3)
            # jambs, drawn heavier because the width gate is measured across them
            cv2.line(img, tuple(pts[0]), tuple(pts[3]), (0, 200, 255), 4)
            cv2.line(img, tuple(pts[1]), tuple(pts[2]), (0, 200, 255), 4)
            _label(img, f"O{oi} {kind} w={width:.2f}m h={height:.2f}m c={conf:.2f}",
                   y=int(pts[:, 1].min()) - 8, x=int(pts[:, 0].min()), color=col, scale=0.45)

    for img, t in ((over, f"OPENINGS  {len(openings)} detected  (orange = jambs)"),
                   (clean, "OPENINGS (clean)")):
        _label(img, t)
    return over, clean


# --- D. final room geometry ---------------------------------------------------------------
def viz_room(rgb, geo, T_wc, K, gravity, ceiling_height=None, origin=None, basis=None):
    """The final polygon projected into the frame, with wall labels and dimensions."""
    over, clean = _pair(rgb)
    shape = over.shape
    if geo is None:
        for img, t in ((over, "ROOM: no polygon"), (clean, "ROOM: no polygon")):
            _label(img, t)
        return over, clean

    g = gravity / np.linalg.norm(gravity)
    e1, e2 = basis if basis is not None else geo.basis
    o = origin if origin is not None else geo.origin
    C3 = np.array([o + c[0] * e1 + c[1] * e2 for c in geo.corners])

    uv, ok = project(C3, T_wc, K, shape)
    n = len(C3)
    for i in range(n):
        j = (i + 1) % n
        if not (ok[i] and ok[j]):
            continue
        a, b = tuple(uv[i].astype(int)), tuple(uv[j].astype(int))
        for img in (over, clean):
            cv2.line(img, a, b, COLOR_ACCEPT, 3)
        mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        for img in (over, clean):
            _label(img, f"W{i} {geo.wall_lengths[i]:.2f}m", y=mid[1], x=mid[0] - 30,
                   color=COLOR_ACCEPT, scale=0.5)
    for i in range(n):
        if ok[i]:
            for img in (over, clean):
                cv2.drawMarker(img, tuple(uv[i].astype(int)), (0, 255, 255),
                               cv2.MARKER_CROSS, 26, 3)

    txt = f"ROOM  area {geo.floor_area:.2f} m2"
    if ceiling_height:
        txt += f"  ceiling {ceiling_height:.3f} m"
    for img in (over, clean):
        _label(img, txt)
    return over, clean


def save_pair(overlay, clean, out_dir: str, stem: str) -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    a = os.path.join(out_dir, f"{stem}_overlay.png")
    b = os.path.join(out_dir, f"{stem}_clean.png")
    cv2.imwrite(a, overlay)
    cv2.imwrite(b, clean)
    return a, b
