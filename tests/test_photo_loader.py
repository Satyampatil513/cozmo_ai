"""Photo loader, against the real capture where one exists and synthetic EXIF otherwise.

Run: python tests/test_photo_loader.py

The loader's failure modes are all silent - a sideways image, a mis-scaled focal length, or
intrinsics that did not follow a resize all produce a plausible-looking reconstruction with
every dimension wrong by a constant. So these assert the invariants directly rather than
checking the code merely runs.
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.capture.photo import (                      # noqa: E402
    FILM_DIAGONAL_MM, intrinsics_from_exif, load, load_frame,
)

REAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "benchmark", "raw", "photo")
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def test_intrinsics_match_field_of_view():
    """f_px must reproduce the diagonal FOV that the 35mm-equivalent focal length encodes."""
    w, h = 768, 1024
    K, src = intrinsics_from_exif({"FocalLengthIn35mmFilm": 26}, w, h)
    fov = 2 * np.degrees(np.arctan(np.hypot(w, h) / (2 * K[0, 0])))
    ref = 2 * np.degrees(np.arctan(FILM_DIAGONAL_MM / (2 * 26.0)))
    check("26mm equiv reproduces its diagonal FOV", abs(fov - ref) < 0.1,
          f"{fov:.2f}deg vs {ref:.2f}deg")
    check("intrinsics source is recorded", "35mm_equiv" in src, src)
    check("principal point is centred", K[0, 2] == w / 2 and K[1, 2] == h / 2)


def test_intrinsics_scale_with_resolution():
    """Halving the image must halve f, cx and cy, or every reported dimension shifts."""
    K1, _ = intrinsics_from_exif({"FocalLengthIn35mmFilm": 26}, 2048, 1536)
    K2, _ = intrinsics_from_exif({"FocalLengthIn35mmFilm": 26}, 1024, 768)
    check("f scales linearly with image size", abs(K1[0, 0] / K2[0, 0] - 2.0) < 1e-9,
          f"{K1[0,0]:.1f} -> {K2[0,0]:.1f}")


def test_missing_exif_is_labelled_not_silent():
    K, src = intrinsics_from_exif({}, 1024, 768)
    check("no-EXIF path still returns usable K", K[0, 0] > 0)
    check("no-EXIF path is labelled as assumed", "assumed" in src, src)


def test_orientation_applied(tmp="_orient_test.jpg"):
    """Orientation 6 means the stored pixels are landscape but the photo is portrait."""
    arr = np.zeros((60, 100, 3), np.uint8)      # landscape as stored
    im = Image.fromarray(arr)
    ex = im.getexif()
    ex[274] = 6                                  # Orientation: rotate 90 CW to display
    im.save(tmp, "JPEG", exif=ex)
    try:
        f = load_frame(tmp, work_px=200)
        h, w = f.image.shape[:2]
        check("EXIF orientation applied at load", h > w, f"got {w}x{h}, expected portrait")
        check("K matches the oriented image", f.K[0, 2] == w / 2 and f.K[1, 2] == h / 2)
    finally:
        os.remove(tmp)


def test_doorway_tagging():
    arr = np.zeros((40, 30, 3), np.uint8)
    p = "doorway_to_Hallway.jpg"
    Image.fromarray(arr).save(p, "JPEG")
    try:
        f = load_frame(p, work_px=100)
        check("doorway frame tagged", "doorway" in f.tags, str(f.tags))
        check("doorway target captured", "to:hallway" in f.tags, str(f.tags))
    finally:
        os.remove(p)


def test_real_capture():
    if not os.path.isdir(REAL) or not any(os.scandir(REAL)):
        print("  SKIP  no real capture in benchmark/raw/photo")
        return
    sc = load(REAL)
    check("real capture loads rooms", len(sc.rooms) >= 3, f"{len(sc.rooms)} rooms")
    check("device identified from EXIF", "iPhone" in sc.device or sc.device != "unknown", sc.device)

    for r in sc.rooms:
        for f in r.frames:
            h, w = f.image.shape[:2]
            if h <= w:
                check(f"{r.room_id}/{os.path.basename(f.image_path)} upright", False,
                      f"{w}x{h}")
                return
            if not (0 < f.K[0, 0] < 10 * max(w, h)):
                check(f"{r.room_id} plausible focal", False, str(f.K[0, 0]))
                return
    check("every frame upright with plausible intrinsics", True,
          f"{len(sc.frames)} frames")

    fx = {round(float(f.K[0, 0]), 1) for f in sc.frames}
    check("one lens across the capture", len(fx) == 1, f"f_px values: {sorted(fx)}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    sys.exit(1 if FAILURES else 0)
