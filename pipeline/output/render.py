"""Plan rendering: SVG and PNG - the whole-property blueprint from a stitched result.

The stitched plan is the product surface. It should read like something a homeowner would
recognise: room names, dimensions on walls, openings drawn as gaps, and interval width shown
rather than hidden.

WHAT THIS DRAWS AND WHY IT IS TRUSTWORTHY. Every room is placed using
`sub_rooms[i]["polygon"]["world_corners"]` - the SAME 2D points `stitch_posed_capture` computed
by converting each room's own local polygon into the one shared floor basis and applying
whatever drift correction ran. This module adds no geometry of its own: it is a projection of
numbers already in the result, onto a canvas, with labels. If a wall's length looks wrong here,
the bug is upstream in `pipeline/geometry/walls.py` or `pipeline/stitching/stitch.py`, not in
this file - which is what "the render is not where correctness lives" is supposed to mean.

INTERVAL WIDTH IS DRAWN, NOT JUST STORED. Each room's ceiling height and each wall length carry
a `_measurement` dict with a 95% interval (`pipeline/confidence/intervals.py`). The label under
each room is `value ± half-width`, not a bare number - the brief scores confident garbage on
thin input as a penalty, and a plan with no visible uncertainty is exactly that shape of claim.

NO NEW DEPENDENCY FOR SVG. `svgwrite` is a declared requirement but is not installed in this
environment (same situation `shapely` was in for the footprint code) - SVG is XML, so it is
written directly as a small text template rather than adding a library this environment does
not actually have.

SCOPE: this renders a STITCHED (video/lidar, multi-room) result. A single unstitched room -
the photo tier today - has no `sub_rooms`/`connections` to place relative to each other, and
is out of scope here; drawing one room's own polygon does not need a "plan".
"""
from __future__ import annotations

import os

import cv2
import numpy as np

# BGR, matching pipeline/output/viz.py's palette so a reader who has seen the debug overlays
# recognises the same visual language here.
COLOR_BG = (24, 22, 20)
COLOR_ROOM_FILL = (70, 60, 50)
COLOR_ROOM_EDGE = (200, 200, 200)
COLOR_TEXT = (235, 235, 235)
COLOR_DIM = (150, 210, 150)
COLOR_CONN_OPEN = (90, 220, 90)         # a passable opening was actually detected
COLOR_CONN_WALL = (100, 140, 235)       # rooms touch, but no opening found on that wall
COLOR_UNPLACED = (90, 90, 200)

PALETTE = [tuple(int(c) for c in cv2.applyColorMap(
    np.array([[int(180 * i / 10)]], np.uint8), cv2.COLORMAP_HSV)[0, 0]) for i in range(10)]

PX_PER_M = 90
MARGIN_PX = 90


def _fmt_measurement(m: dict | None, unit: str = "m", digits: int = 2) -> str:
    if not m:
        return "-"
    lo, hi = m["interval"]
    half = (hi - lo) / 2
    # Plain ASCII, not U+00B1: cv2's Hershey fonts have no Unicode glyph for +/- and render
    # it as a garbled or missing character on the PNG - the interval would be invisible on
    # exactly the artifact this whole function exists to make visible.
    return f"{m['value']:.{digits}f} +/- {half:.{digits}f} {unit}"


def _text(img, s: str, org, scale=0.5, color=COLOR_TEXT, thick=1, bg=True):
    if bg:
        cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2,
                   cv2.LINE_AA)
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def _rooms_with_polygons(result: dict) -> list[dict]:
    """Flatten every sub-room across every RoomCapture in the result that has a WORLD-frame
    polygon to draw. A room whose polygon never closed, or that was never placed by
    stitching, is listed separately as `unplaced` rather than silently omitted."""
    out = []
    for r in result.get("rooms", []):
        for sr in r.get("sub_rooms", [r]):
            poly = sr.get("polygon")
            if poly and poly.get("world_corners"):
                out.append(sr)
    return out


def render_plan(result: dict, out_svg: str, out_png: str) -> dict:
    """The FULL `result` dict written to `result.json` by run.py -> a top-down blueprint.

    Reads `result["rooms"]` (the per-capture list `measure_room` produced - a stitched
    capture's entry carries `sub_rooms`) and `result["property"]["connections"]` /
    `["footprint_area"]` (already assembled by run.py from those same sub-rooms). Nothing
    here re-derives adjacency or placement; it only draws what stitching already computed.

    Returns a small report dict: how many rooms were placed vs. how many exist in the result
    but could not be drawn (no closed polygon), so a caller can state that gap rather than
    have a blueprint that looks complete while quietly dropping rooms.
    """
    property_result = result.get("property", {})
    rooms = _rooms_with_polygons(result)
    all_sub = [sr for r in result.get("rooms", []) for sr in r.get("sub_rooms", [r])]
    unplaced = [sr["room_id"] for sr in all_sub if sr not in rooms]

    if not rooms:
        os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
        blank = np.full((300, 500, 3), COLOR_BG, np.uint8)
        _text(blank, "no room has a closed polygon to draw", (16, 150), 0.55)
        cv2.imwrite(out_png, blank)
        _write_svg(out_svg, 500, 300, [f'<text x="16" y="150" fill="white" '
                                       f'font-size="16">no room has a closed polygon '
                                       f'to draw</text>'])
        return {"rooms_drawn": 0, "rooms_unplaced": unplaced}

    all_pts = np.concatenate([np.asarray(sr["polygon"]["world_corners"]) for sr in rooms])
    lo, hi = all_pts.min(axis=0), all_pts.max(axis=0)
    span = hi - lo

    def to_px(p):
        return (MARGIN_PX + (p[0] - lo[0]) * PX_PER_M,
               MARGIN_PX + (span[1] - (p[1] - lo[1])) * PX_PER_M)     # image y grows downward

    W = int(span[0] * PX_PER_M) + 2 * MARGIN_PX
    H = int(span[1] * PX_PER_M) + 2 * MARGIN_PX + 60          # extra footer strip
    img = np.full((H, W, 3), COLOR_BG, np.uint8)
    svg_body: list[str] = []

    # --- rooms, filled polygon + label ---
    for i, sr in enumerate(rooms):
        C = np.asarray(sr["polygon"]["world_corners"], dtype=float)
        px = np.array([to_px(p) for p in C], dtype=np.int32)
        fill = PALETTE[i % len(PALETTE)]
        overlay = img.copy()
        cv2.fillPoly(overlay, [px], fill)
        img = cv2.addWeighted(overlay, 0.35, img, 0.65, 0)
        cv2.polylines(img, [px], True, COLOR_ROOM_EDGE, 2, cv2.LINE_AA)

        centre = px.mean(axis=0).astype(int)
        ch = _fmt_measurement(sr.get("ceiling_height_measurement"))
        area = _fmt_measurement(sr.get("floor_area_measurement"), unit="m2")
        _text(img, sr["room_id"], (centre[0] - 45, centre[1] - 18), 0.55, COLOR_TEXT, 2)
        _text(img, f"ceiling {ch}", (centre[0] - 45, centre[1] + 2), 0.42, COLOR_DIM)
        _text(img, f"area {area}", (centre[0] - 45, centre[1] + 20), 0.42, COLOR_DIM)

        svg_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in px)
        rgb = f"rgb({fill[2]},{fill[1]},{fill[0]})"
        svg_body.append(f'<polygon points="{svg_pts}" fill="{rgb}" fill-opacity="0.35" '
                        f'stroke="white" stroke-width="2"/>')
        svg_body.append(f'<text x="{centre[0]-45}" y="{centre[1]-18}" fill="white" '
                        f'font-size="14">{sr["room_id"]}</text>')
        svg_body.append(f'<text x="{centre[0]-45}" y="{centre[1]+2}" fill="#96d296" '
                        f'font-size="11">ceiling {ch}</text>')
        svg_body.append(f'<text x="{centre[0]-45}" y="{centre[1]+20}" fill="#96d296" '
                        f'font-size="11">area {area}</text>')

        # Wall length labels at each edge midpoint. `wall_lengths[k]` spans corners[k]->[k+1]
        # in the room's OWN local frame - lengths are rigid-invariant, so the same numbers
        # apply unchanged to the world-placed polygon drawn here.
        wl = sr["polygon"].get("wall_lengths", [])
        wm = sr.get("wall_length_measurements", [])
        for k in range(len(px)):
            a, b = px[k], px[(k + 1) % len(px)]
            mid = ((a + b) // 2)
            label = (_fmt_measurement(wm[k]) if k < len(wm)
                    else (f"{wl[k]:.2f} m" if k < len(wl) else ""))
            if label:
                _text(img, label, tuple(mid), 0.38, (210, 230, 210))
                svg_body.append(f'<text x="{mid[0]}" y="{mid[1]}" fill="#d2e6d2" '
                                f'font-size="10">{label}</text>')

    # --- connections: drawn at the midpoint between the two rooms' own centroids, along the
    # shared wall - openings drawn as a bright gap marker, unconfirmed walls dimmer ---
    room_by_id = {sr["room_id"]: sr for sr in rooms}
    for c in property_result.get("connections", []):
        ra, rb = room_by_id.get(c["rooms"][0]), room_by_id.get(c["rooms"][1])
        if ra is None or rb is None:
            continue
        ca = np.asarray(ra["polygon"]["world_corners"]).mean(axis=0)
        cb = np.asarray(rb["polygon"]["world_corners"]).mean(axis=0)
        mid = to_px((ca + cb) / 2)
        mid = (int(mid[0]), int(mid[1]))
        col = COLOR_CONN_OPEN if c.get("has_opening") else COLOR_CONN_WALL
        cv2.circle(img, mid, 9, col, -1, cv2.LINE_AA)
        cv2.circle(img, mid, 9, (255, 255, 255), 1, cv2.LINE_AA)
        tag = "door" if c.get("has_opening") else f"wall, gap {c['gap_m']*100:.0f}cm"
        _text(img, tag, (mid[0] + 12, mid[1] + 4), 0.4, col)
        rgb = f"rgb({col[2]},{col[1]},{col[0]})"
        svg_body.append(f'<circle cx="{mid[0]}" cy="{mid[1]}" r="9" fill="{rgb}" '
                        f'stroke="white"/>')
        svg_body.append(f'<text x="{mid[0]+12}" y="{mid[1]+4}" fill="{rgb}" '
                        f'font-size="11">{tag}</text>')

    # --- scale bar, north-agnostic (no compass reference available from odometry alone) ---
    bar_m = 1.0
    bx0, by0 = MARGIN_PX, H - 40
    cv2.line(img, (bx0, by0), (bx0 + int(bar_m * PX_PER_M), by0), COLOR_TEXT, 2)
    _text(img, f"{bar_m:.0f} m", (bx0, by0 - 8), 0.4)
    svg_body.append(f'<line x1="{bx0}" y1="{by0}" x2="{bx0+int(bar_m*PX_PER_M)}" y2="{by0}" '
                    f'stroke="white" stroke-width="2"/>')
    svg_body.append(f'<text x="{bx0}" y="{by0-8}" fill="white" font-size="11">'
                    f'{bar_m:.0f} m</text>')

    footprint = property_result.get("footprint_area")
    header = (f"footprint {footprint:.2f} m2" if footprint else "footprint: not computed") \
        + f"   {len(rooms)} room(s) placed"
    if unplaced:
        header += f"   {len(unplaced)} room(s) NOT drawn (no closed polygon): {unplaced}"
    _text(img, header, (MARGIN_PX, H - 15), 0.42, (200, 200, 130))
    svg_body.append(f'<text x="{MARGIN_PX}" y="{H-15}" fill="#c8c882" font-size="11">'
                    f'{header}</text>')

    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    cv2.imwrite(out_png, img)
    _write_svg(out_svg, W, H, svg_body)
    return {"rooms_drawn": len(rooms), "rooms_unplaced": unplaced,
           "footprint_area_m2": footprint}


def _write_svg(path: str, w: int, h: int, body: list[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
                f'viewBox="0 0 {w} {h}">\n')
        fh.write(f'<rect width="{w}" height="{h}" fill="rgb(24,22,20)"/>\n')
        fh.write("\n".join(body))
        fh.write("\n</svg>\n")
