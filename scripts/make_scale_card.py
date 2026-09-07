"""Generate the printable scale card the photo and video tiers depend on.

    python scripts/make_scale_card.py

Writes assets/scale_card_A4.pdf and assets/scale_card_A4.png (300 dpi).

Why this exists: photo and video captures are scale-ambiguous. Structure-from-motion
recovers shape up to an unknown similarity transform, so without a known-size object in
frame those tiers cannot honestly report metres. The card is that known object.

The marker is ArUco DICT_4X4_50 id 0 with a 150.0 mm outer side. That number is the one
constant the whole photo/video metric chain hangs off, so it lives in exactly one place:
MARKER_MM below, mirrored by pipeline/capture/scale_card.py.

Print at 100% / "Actual size". Do NOT use "Fit to page" - that silently rescales the
marker and every metre we report downstream is then wrong by the same factor. The ruler
printed along the bottom edge exists so a human can verify scale with a tape measure
before capturing.
"""
from __future__ import annotations

import json
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DPI = 300
MM = DPI / 25.4                      # pixels per millimetre
A4_MM = (210.0, 297.0)
MARKER_MM = 150.0                    # nominal outer side of the black marker square
BITS = 6                             # DICT_4X4_50: 4 data cells + 1 border cell each side
ARUCO_DICT = cv2.aruco.DICT_4X4_50
MARKER_ID = 0

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def mm(v: float) -> int:
    return int(round(v * MM))


def _font(size_mm: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, mm(size_mm))
        except OSError:
            continue
    return ImageFont.load_default()


def marker_side_px() -> int:
    """Snap the marker to a whole number of bit cells.

    OpenCV renders each of the 6 cells at floor(side/6) px, so an arbitrary pixel side
    silently shrinks the marker by up to 5 px. At 300 dpi that is a ~0.1% scale bias
    baked into every photo- and video-tier measurement, in one direction, forever. Cheap
    to avoid: pick the nearest multiple of BITS and report the exact millimetres it lands
    on rather than the millimetres we asked for.
    """
    return int(round(mm(MARKER_MM) / BITS)) * BITS


def marker_true_mm() -> float:
    """The size the printed marker actually is. This is what the pipeline must use."""
    return marker_side_px() / MM


def marker_image() -> Image.Image:
    d = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    img = cv2.aruco.generateImageMarker(d, MARKER_ID, marker_side_px())
    return Image.fromarray(img).convert("L")


def draw_ruler(dr: ImageDraw.ImageDraw, x0: int, y: int, length_mm: float, font) -> None:
    """A 100 mm ruler. If this measures 100 mm with a tape, the print scale is right."""
    dr.line([(x0, y), (x0 + mm(length_mm), y)], fill=0, width=mm(0.4))
    for i in range(int(length_mm) + 1):
        if i % 10 == 0:
            h, w = 5.0, 0.5
        elif i % 5 == 0:
            h, w = 3.0, 0.35
        else:
            h, w = 1.8, 0.25
        x = x0 + mm(i)
        dr.line([(x, y), (x, y - mm(h))], fill=0, width=mm(w))
        if i % 10 == 0:
            dr.text((x + mm(0.8), y - mm(9.5)), str(i), fill=0, font=font)


def build() -> Image.Image:
    page = Image.new("L", (mm(A4_MM[0]), mm(A4_MM[1])), 255)
    dr = ImageDraw.Draw(page)
    f_title, f_body, f_small = _font(6.0), _font(3.8), _font(2.6)

    dr.text((mm(20), mm(13)), "COZMO AI - SCALE CARD", fill=0, font=f_title)
    dr.text((mm(20), mm(21)), "Print at 100% / Actual size. Never 'Fit to page'.",
            fill=0, font=f_body)

    # The marker is fixed at 150 mm, which sets the whole vertical budget on A4.
    # Everything else is laid out around it, not the other way round.
    marker = marker_image()
    mx, my = mm(30), mm(30)
    page.paste(marker, (mx, my))

    dy = my + marker.size[1] + mm(5)
    dr.line([(mx, dy), (mx + marker.size[0], dy)], fill=0, width=mm(0.4))
    for ex in (mx, mx + marker.size[0]):
        dr.line([(ex, dy - mm(2.5)), (ex, dy + mm(2.5))], fill=0, width=mm(0.4))
    dr.text((mx + marker.size[0] // 2 - mm(11), dy + mm(2.5)),
            f"{marker_true_mm():.2f} mm", fill=0, font=f_body)

    y = mm(197)
    dr.text((mm(20), y), "Check the print scale before you capture:", fill=0, font=f_body)
    draw_ruler(dr, mm(20), y + mm(19), 100.0, f_small)
    dr.text((mm(20), y + mm(21.5)),
            "This ruler must measure 100 mm on a tape. If it does not, reprint at 100%:",
            fill=0, font=f_small)
    dr.text((mm(20), y + mm(25.5)),
            "a 4% print error is a 4% error on every wall length we report.",
            fill=0, font=f_small)

    y = mm(230)
    for line in (
        "Tape flat to a wall at chest height. Flat, not folded, not curled.",
        "Matte wall only - never glass, a mirror, or glossy tile.",
        "Leave it in place for the photo, video and LiDAR passes of this room.",
    ):
        dr.text((mm(20), y), "- " + line, fill=0, font=f_body)
        y += mm(6.5)

    y = mm(256)
    dr.text((mm(20), y), "ROOM:", fill=0, font=f_body)
    dr.line([(mm(38), y + mm(5)), (mm(118), y + mm(5))], fill=0, width=mm(0.3))
    dr.text((mm(124), y), "PASS:", fill=0, font=f_body)
    dr.line([(mm(142), y + mm(5)), (mm(190), y + mm(5))], fill=0, width=mm(0.3))

    y = mm(268)
    dr.text((mm(20), y), "DATE:", fill=0, font=f_body)
    dr.line([(mm(38), y + mm(5)), (mm(118), y + mm(5))], fill=0, width=mm(0.3))
    dr.text((mm(124), y), "TIER:", fill=0, font=f_body)
    dr.line([(mm(142), y + mm(5)), (mm(190), y + mm(5))], fill=0, width=mm(0.3))

    dr.text((mm(20), mm(285)),
            f"ArUco {ARUCO_DICT_NAME} id={MARKER_ID}  outer side "
            f"{marker_true_mm():.3f} mm  rendered at {DPI} dpi",
            fill=0, font=f_small)
    return page


ARUCO_DICT_NAME = "DICT_4X4_50"


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    page = build()
    png = os.path.join(OUT_DIR, "scale_card_A4.png")
    pdf = os.path.join(OUT_DIR, "scale_card_A4.pdf")
    sidecar = os.path.join(OUT_DIR, "scale_card.json")
    page.save(png, dpi=(DPI, DPI))
    page.convert("RGB").save(pdf, "PDF", resolution=DPI)

    # The pipeline reads this, not the nominal constant. One source of truth for the
    # number the entire photo/video metric chain is divided by.
    with open(sidecar, "w") as fh:
        json.dump({
            "dictionary": ARUCO_DICT_NAME,
            "marker_id": MARKER_ID,
            "marker_side_mm": round(marker_true_mm(), 4),
            "nominal_side_mm": MARKER_MM,
            "render_dpi": DPI,
            "note": "marker_side_mm is authoritative; assumes the sheet was printed at 100%",
        }, fh, indent=2)

    print(f"wrote {png}")
    print(f"wrote {pdf}")
    print(f"wrote {sidecar}")
    print(f"marker: ArUco {ARUCO_DICT_NAME} id={MARKER_ID}, "
          f"true outer side {marker_true_mm():.4f} mm (nominal {MARKER_MM})")


if __name__ == "__main__":
    main()
