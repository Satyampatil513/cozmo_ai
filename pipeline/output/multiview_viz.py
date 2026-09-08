"""Visual evidence for the multi-view photo path.

A registration that "ran" tells you nothing. A fused cloud built on wrong poses still looks
like a reconstruction, and every number downstream of it will be stated with the same
confidence as a correct one. So each stage renders what it actually did:

  A  matches        two photos side by side, the same feature numbered identically in both,
                    so "corner 7 in photo A is corner 7 in photo B" is checkable by eye
  B  overlap graph  every candidate edge, drawn in the colour of its verdict, labelled with
                    the number that produced the verdict
  C  cameras        estimated camera positions and viewing directions, top-down
  D  clouds         the transformed point clouds, coloured by which photo each point came
                    from - the direct test of whether the alignment is real
  E  geometry       planes, corners and wall dimensions from the shared reconstruction, via
                    the existing `pipeline/output/viz.py` renderers

D is the one that decides it. If the poses are right, the coloured clouds interleave and the
walls are one surface in several colours. If they are wrong, each colour is its own slab and
the picture says so immediately - no metric required.
"""
from __future__ import annotations

import os

import cv2
import numpy as np

# Verdict colours, BGR. Deliberately not a gradient: these are categories, not degrees.
COL_KEPT = (110, 200, 110)        # green   - survived every gate
COL_TREE = (90, 230, 250)         # yellow  - survived and used to compose poses
COL_CYCLE = (70, 70, 235)         # red     - cut by cycle consistency
COL_PAIR = (150, 130, 130)        # grey    - failed its own pairwise fit
COL_IMPL = (60, 150, 245)         # orange  - dropped: camera at an impossible height
COL_NONE = (60, 60, 60)           # dark    - no feature overlap; not a failure

# A distinct hue per source frame for the cloud overlay. Eight is the protocol's photo cap.
FRAME_COLOURS = [(80, 120, 240), (240, 160, 60), (90, 210, 120), (200, 100, 220),
                 (70, 220, 230), (230, 110, 150), (150, 200, 90), (120, 120, 240)]


def _label(img, text, org, color=(255, 255, 255), scale=0.42, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2,
                cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def save_compact(img, out_dir: str, stem: str, max_width: int = 1100) -> str:
    """Write one sheet downscaled, as JPEG.

    `viz.save_pair` writes full-resolution PNG, which is right for a single frame sheet a
    reviewer zooms into. This path renders the SAME world geometry onto every registered
    photo, so a room emits dozens of near-identical sheets and full-resolution PNG costs
    hundreds of megabytes for pictures whose job is to be compared at a glance.
    """
    if img.shape[1] > max_width:
        k = max_width / img.shape[1]
        img = cv2.resize(img, (max_width, int(img.shape[0] * k)),
                         interpolation=cv2.INTER_AREA)
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, f"{stem}.jpg")
    cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 86])
    return p


def _edge_colour(e) -> tuple:
    if e.status == "cycle":
        return COL_CYCLE
    if e.status == "implausible":
        return COL_IMPL
    if e.status == "pairwise":
        return COL_PAIR
    if e.status == "no_overlap":
        return COL_NONE
    return COL_TREE if e.in_tree else COL_KEPT


# ------------------------------------------------------------------------------- A: matches

def viz_matches(frame_a, frame_b, pm, out_path: str, max_pts: int = 24,
                max_width: int = 1500) -> str:
    """Two photos side by side, matched inliers numbered identically in both.

    Thinned on purpose: all 400 inliers drawn would be an unreadable smear and would prove
    nothing a labelled sample does not.
    """
    if pm.px_i is None or not len(pm.px_i):
        return ""
    a = cv2.cvtColor(frame_a.image, cv2.COLOR_RGB2BGR)
    b = cv2.cvtColor(frame_b.image, cv2.COLOR_RGB2BGR)
    h = max(a.shape[0], b.shape[0])
    canvas = np.zeros((h + 34, a.shape[1] + b.shape[1], 3), np.uint8)
    canvas[34:34 + a.shape[0], :a.shape[1]] = a
    canvas[34:34 + b.shape[0], a.shape[1]:] = b

    step = max(1, len(pm.px_i) // max_pts)
    for n, k in enumerate(range(0, len(pm.px_i), step)):
        pa = (int(pm.px_i[k][0]), int(pm.px_i[k][1]) + 34)
        pb = (int(pm.px_j[k][0]) + a.shape[1], int(pm.px_j[k][1]) + 34)
        col = FRAME_COLOURS[n % len(FRAME_COLOURS)]
        cv2.circle(canvas, pa, 5, col, 2, cv2.LINE_AA)
        cv2.circle(canvas, pb, 5, col, 2, cv2.LINE_AA)
        cv2.line(canvas, pa, pb, col, 1, cv2.LINE_AA)
        _label(canvas, str(n), (pa[0] + 7, pa[1] - 6), col)
        _label(canvas, str(n), (pb[0] + 7, pb[1] - 6), col)

    verdict = {"cycle": "CUT by cycle consistency", "pairwise": "CUT by pairwise fit",
               "kept": "KEPT"}.get(getattr(pm, "status", "kept"), "KEPT")
    _label(canvas, f"frames {pm.i} <-> {pm.j}   {pm.n_inliers} inliers   "
                   f"pairwise residual {pm.residual_m * 100:.1f} cm   "
                   f"depth-scale ratio {pm.scale:.3f}   {verdict}", (8, 22), scale=0.5)
    # Downscaled and written as JPEG: this is a side-by-side of two full photos, and at
    # working resolution each sheet is ~2 MB. A room produces ten of them, which is a lot of
    # disk for a picture whose whole job is to be glanced at. 1500 px keeps the numbered
    # markers legible.
    if canvas.shape[1] > max_width:
        k = max_width / canvas.shape[1]
        canvas = cv2.resize(canvas, (max_width, int(canvas.shape[0] * k)),
                            interpolation=cv2.INTER_AREA)
    out_path = os.path.splitext(out_path)[0] + ".jpg"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cv2.imwrite(out_path, canvas, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out_path


# -------------------------------------------------------------------------- B: overlap graph

def viz_overlap_graph(reg, out_path: str, size: int = 760) -> str:
    """The overlap graph with each edge drawn in the colour of its verdict.

    Frames sit on a circle rather than at their estimated positions: this picture is about
    which constraints were trusted, and a spatial layout would collide unrelated nodes and
    imply a geometry the graph does not carry. Camera positions get their own panel.
    """
    panel = 430          # wide enough for the report lines; they are the point of the figure
    img = np.full((size, size + panel, 3), 26, np.uint8)
    n = len(reg.poses)
    cx, cy, r = size // 2, size // 2 + 14, size // 2 - 74
    pos = {i: (int(cx + r * np.cos(-np.pi / 2 + 2 * np.pi * i / n)),
               int(cy + r * np.sin(-np.pi / 2 + 2 * np.pi * i / n))) for i in range(n)}

    # Rejected edges first so a kept edge is never hidden underneath one that was cut.
    for e in sorted(reg.edges, key=lambda e: e.status == "kept"):
        if e.status == "no_overlap":
            continue
        pa, pb = pos[e.edge[0]], pos[e.edge[1]]
        col = _edge_colour(e)
        thick = 3 if e.in_tree else 2
        if e.status in ("cycle", "pairwise", "implausible"):
            # Dashed: a rejected edge is a constraint that was available and refused, which
            # is different from one that never existed.
            for t in np.arange(0, 1, 0.06):
                if int(t / 0.06) % 2:
                    continue
                p0 = (int(pa[0] + (pb[0] - pa[0]) * t), int(pa[1] + (pb[1] - pa[1]) * t))
                p1 = (int(pa[0] + (pb[0] - pa[0]) * (t + 0.06)),
                      int(pa[1] + (pb[1] - pa[1]) * (t + 0.06)))
                cv2.line(img, p0, p1, col, thick, cv2.LINE_AA)
        else:
            cv2.line(img, pa, pb, col, thick, cv2.LINE_AA)

        mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)
        if e.status == "cycle" and np.isfinite(e.cycle_error_m):
            _label(img, f"{e.cycle_error_m * 100:.0f}cm", (mid[0] - 16, mid[1]), col, 0.4)
        elif e.status == "kept" and np.isfinite(e.world_residual_m):
            _label(img, f"{e.world_residual_m * 100:.1f}", (mid[0] - 12, mid[1]), col, 0.38)

    for i, p in pos.items():
        placed = reg.poses[i] is not None
        cv2.circle(img, p, 26, (245, 245, 245) if placed else (70, 70, 70), -1, cv2.LINE_AA)
        cv2.circle(img, p, 26, (255, 255, 255) if i == reg.reference else (140, 140, 140),
                   3 if i == reg.reference else 1, cv2.LINE_AA)
        _label(img, str(i), (p[0] - 7, p[1] + 7), (20, 20, 20) if placed else (170, 170, 170),
               0.7, 2)
        if i == reg.reference:
            _label(img, "ref", (p[0] - 11, p[1] + 44), (255, 255, 255), 0.42)
        elif not placed:
            _label(img, "unplaced", (p[0] - 27, p[1] + 44), (150, 150, 150), 0.4)

    x = size + 12
    _label(img, "OVERLAP GRAPH", (x, 30), (255, 255, 255), 0.55, 2)
    for k, (txt, col) in enumerate([
            ("tree edge (pose path)", COL_TREE), ("kept, independent cross-check", COL_KEPT),
            ("cut: cycle consistency", COL_CYCLE), ("cut: pairwise fit", COL_PAIR),
            ("cut: implausible camera height", COL_IMPL)]):
        cv2.line(img, (x, 54 + k * 22), (x + 26, 54 + k * 22), col, 3, cv2.LINE_AA)
        _label(img, txt, (x + 33, 58 + k * 22), col, 0.4)
    # Wrapped, not truncated: a clipped "Rejected by cycle consistency: 3 (trian" loses the
    # number the whole figure exists to communicate.
    y = 186
    for line in reg.report_lines():
        while line:
            _label(img, line[:52], (x, y), (225, 225, 225), 0.4)
            line, y = line[52:], y + 18
        y += 3

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cv2.imwrite(out_path, img)
    return out_path


def graph_ascii(reg) -> str:
    """The same graph as text, for the terminal and the written report.

    A matrix rather than a node-link drawing: at six frames the matrix is unambiguous about
    every pair, including the pairs that have no edge, which a drawing shows only by absence.
    """
    n = len(reg.poses)
    by_key = {e.edge: e for e in reg.edges}
    sym = {"kept": "ok", "cycle": "CUT", "pairwise": "px", "no_overlap": " .",
           "implausible": "IMP"}
    head = "     " + "".join(f"{j:>5}" for j in range(n))
    rows = [head, "     " + "-" * (5 * n)]
    for i in range(n):
        mark = "*" if i == reg.reference else (" " if reg.poses[i] is not None else "x")
        cells = []
        for j in range(n):
            if i == j:
                cells.append("    -")
                continue
            e = by_key.get((i, j)) or by_key.get((j, i))
            s = sym.get(e.status, "?") if e else " ."
            if e and e.status == "kept" and e.in_tree:
                s = "TREE"
            cells.append(f"{s:>5}")
        rows.append(f"{i}{mark} |" + "".join(cells))
    rows += ["",
             "  TREE = used to compose poses    ok = kept, used as cross-check",
             "  CUT  = cut by cycle consistency px = failed its own pairwise fit",
             "   .   = no feature overlap      IMP = cut: implausible camera height",
             "   * = reference frame          x = unregistered"]
    return "\n".join(rows)


# ------------------------------------------------------------------------------ C: cameras

def _camera_positions(reg) -> dict[int, np.ndarray]:
    return {i: T[:3, 3] for i, T in enumerate(reg.poses) if T is not None}


def viz_cameras(reg, out_path: str, size: int = 620) -> str:
    """Estimated camera positions and viewing directions, viewed down the world up-axis.

    The first sanity check on any reconstruction, and it needs no ground truth: photos of one
    room were taken from a few metres apart, so positions spanning tens of metres, or all
    collapsed onto one point, are wrong on their face.
    """
    img = np.full((size, size, 3), 26, np.uint8)
    cams = _camera_positions(reg)
    if len(cams) < 2:
        _label(img, "fewer than 2 cameras placed", (16, 30), (200, 200, 200), 0.5)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        cv2.imwrite(out_path, img)
        return out_path

    # World frame is the reference camera's frame, so the room floor plane is roughly xz.
    P = np.array([cams[i] for i in sorted(cams)])
    xz = P[:, [0, 2]]
    lo, hi = xz.min(axis=0), xz.max(axis=0)
    span = max(float(np.max(hi - lo)), 1.0)
    pad = 0.18 * span

    def to_px(p):
        u = (p[0] - lo[0] + pad) / (span + 2 * pad)
        v = (p[1] - lo[1] + pad) / (span + 2 * pad)
        return int(40 + u * (size - 80)), int(size - 40 - v * (size - 80))

    for e in reg.edges:
        if not (e.status == "kept" and e.in_tree):
            continue
        a, b = e.edge
        if a in cams and b in cams:
            cv2.line(img, to_px(cams[a][[0, 2]]), to_px(cams[b][[0, 2]]), COL_TREE, 1,
                     cv2.LINE_AA)

    for i in sorted(cams):
        p = to_px(cams[i][[0, 2]])
        # Optical axis is +z in camera coordinates; project it to show where the photo looked.
        d = reg.poses[i][:3, 2]
        tip = to_px((cams[i] + 0.35 * span * d / (np.linalg.norm(d) or 1))[[0, 2]])
        cv2.arrowedLine(img, p, tip, (200, 200, 200), 1, cv2.LINE_AA, tipLength=0.25)
        col = (255, 255, 255) if i == reg.reference else FRAME_COLOURS[i % len(FRAME_COLOURS)]
        cv2.circle(img, p, 8, col, -1, cv2.LINE_AA)
        _label(img, str(i), (p[0] + 11, p[1] + 5), col, 0.5, 2)

    d = np.linalg.norm(P[:, None] - P[None], axis=-1)
    _label(img, "CAMERA POSITIONS (top-down, world = reference camera frame)", (14, 24),
           (255, 255, 255), 0.45)
    _label(img, f"{len(cams)} placed   baseline max {d.max():.2f} m   "
                f"median {np.median(d[d > 0]):.2f} m", (14, size - 16), (210, 210, 210), 0.44)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cv2.imwrite(out_path, img)
    return out_path


# -------------------------------------------------------------------------------- D: clouds

def viz_clouds_by_source(frames, reg, out_path: str, size: int = 620,
                         stride: int = 6) -> str:
    """Transformed point clouds, one colour per source photo: top-down and elevation.

    The decisive picture. If the poses are right the colours interleave and a wall is one
    surface seen in several colours; if they are wrong each photo becomes its own slab. That
    reads instantly and needs no threshold, which is exactly what a residual number does not.
    """
    from pipeline.geometry.lift import lift

    clouds = []
    for i, f in enumerate(frames):
        if f.depth is None or f.K is None or reg.poses[i] is None:
            continue
        pts, _ = lift(f.depth, f.K, T_wc=reg.poses[i], stride=stride, max_points=9000)
        if len(pts):
            clouds.append((i, pts))
    img = np.full((size, 2 * size, 3), 22, np.uint8)
    if not clouds:
        _label(img, "no posed frames to draw", (16, 30), (200, 200, 200), 0.5)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        cv2.imwrite(out_path, img)
        return out_path

    allp = np.concatenate([c for _, c in clouds])
    # Robust bounds: a handful of flying pixels at a depth discontinuity would otherwise set
    # the scale and squeeze the room into a few pixels.
    lo, hi = np.percentile(allp, 1, axis=0), np.percentile(allp, 99, axis=0)

    def panel(ax_u, ax_v, x0, title, flip_v=True):
        span_u = max(hi[ax_u] - lo[ax_u], 1e-3)
        span_v = max(hi[ax_v] - lo[ax_v], 1e-3)
        for i, pts in clouds:
            u = (pts[:, ax_u] - lo[ax_u]) / span_u
            v = (pts[:, ax_v] - lo[ax_v]) / span_v
            keep = (u > -0.05) & (u < 1.05) & (v > -0.05) & (v < 1.05)
            px = (34 + np.clip(u[keep], 0, 1) * (size - 68)).astype(int) + x0
            py = ((size - 34 - np.clip(v[keep], 0, 1) * (size - 68)) if flip_v
                  else (34 + np.clip(v[keep], 0, 1) * (size - 68))).astype(int)
            img[np.clip(py, 0, size - 1), np.clip(px, x0, x0 + size - 1)] = \
                FRAME_COLOURS[i % len(FRAME_COLOURS)]
        _label(img, title, (x0 + 14, 24), (255, 255, 255), 0.45)

    panel(0, 2, 0, "TOP-DOWN (x-z)")
    panel(0, 1, size, "ELEVATION (x-y), y is camera-up")
    for k, (i, _) in enumerate(clouds):
        c = FRAME_COLOURS[i % len(FRAME_COLOURS)]
        cv2.circle(img, (18, size - 18 - k * 20), 6, c, -1, cv2.LINE_AA)
        _label(img, f"photo {i}" + ("  (ref)" if i == reg.reference else ""),
               (30, size - 13 - k * 20), c, 0.42)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cv2.imwrite(out_path, img)
    return out_path


# ------------------------------------------------------------------------------ E: geometry

def viz_geometry(frames, reg, fused, out_dir: str, stem_prefix: str = "",
                 max_frames: int = 3) -> list[str]:
    """Planes, corners, openings and room geometry from the SHARED reconstruction.

    Reuses the single-view renderers in `pipeline/output/viz.py` unchanged. Each registered
    photo gets its own sheet, but the geometry drawn on all of them is the one model fitted to
    the fused cloud - so the same wall, the same corner and the same ceiling appear in every
    view, which is the visual claim multi-view is making and the one worth checking.

    Planes are re-fitted here rather than passed in because `_measure_cloud` discards the
    plane objects once it has the numbers. RANSAC is seeded deterministically, so this
    reproduces the planes that produced those numbers rather than a second opinion of them.
    """
    from pipeline.geometry.lift import lift
    from pipeline.geometry.openings import detect_openings
    from pipeline.geometry.planes import (CAMERA_UP, classify, estimate_gravity,
                                          extract_planes, merge_coplanar, regularize)
    from pipeline.geometry.walls import corners_with_status, extract_walls
    from pipeline.output import viz

    if fused is None or not len(fused.points):
        return []
    pts, nrm = fused.points, fused.normals
    planes = extract_planes(pts, nrm, threshold=0.05)
    if not planes:
        return []
    planes = merge_coplanar(planes, pts)
    g = estimate_gravity(planes, prior=CAMERA_UP)
    planes, g, _score = classify(planes, g, pts, True)
    planes = regularize(planes, g, pts)

    floor = next((p for p in planes if p.kind == "floor"), None)
    ceiling = next((p for p in planes if p.kind == "ceiling"), None)
    walls = [p for p in planes if p.kind == "wall"]
    corners = corners_with_status(walls, floor, pts, g)
    try:
        openings = detect_openings(walls, pts, g, floor)
    except Exception:
        openings = []
    geo = extract_walls(pts, floor, ceiling, walls=walls, gravity=g) if floor is not None else None
    ch = None
    if floor is not None and ceiling is not None:
        from pipeline.geometry.walls import ceiling_height
        h, _ = ceiling_height(floor, ceiling, pts)
        ch = float(h) if np.isfinite(h) else None

    # The same world geometry is drawn on every registered photo, so rendering all of them
    # multiplies disk without adding evidence. The reference frame comes first because it is
    # the world origin, then the next few registered views.
    order = ([reg.reference] + [i for i in range(len(frames)) if i != reg.reference])[:max_frames + 1]
    out: list[str] = []
    for i in order:
        f = frames[i]
        T = reg.poses[i]
        if T is None or f.depth is None or f.K is None:
            continue
        # `pix` and `world` must stay parallel: viz_planes tests each world point against a
        # plane and paints the pixel it came from, so both have to index the same lift.
        p, _n, pix = lift(f.depth, f.K, stride=2, return_pixels=True)
        world = (T[:3, :3] @ p.T).T + T[:3, 3]
        stem = f"{stem_prefix}{os.path.splitext(os.path.basename(f.image_path))[0]}"
        # Only the overlay is kept here; `viz` also returns a clean render on black, which is
        # the more useful of the two when inspecting ONE frame's own fit but is redundant
        # across a dozen views of one shared model.
        for tag, (ov, _clean) in [
                ("A_planes", viz.viz_planes(f.image, pix, planes, world_points=world)),
                ("B_corners", viz.viz_corners(f.image, corners, T, f.K, walls, floor, ceiling)),
                ("C_openings", viz.viz_openings(f.image, openings, walls, T, f.K, g)),
                ("D_room", viz.viz_room(f.image, geo, T, f.K, g, ceiling_height=ch))]:
            out.append(save_compact(ov, out_dir, f"{stem}_{tag}"))
    return out
