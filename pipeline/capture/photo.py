"""Photo tier loader: per-room folders of 2 to 8 stills -> Scene.

No depth, no poses. Geometry comes from a metric depth model lifted through intrinsics;
absolute scale comes from that model cross-checked against the floor plane and the operator's
stated camera height. The protocol places nothing in the room.

Three things this module gets right that are easy to get silently wrong, and each of them
corrupts every downstream number rather than failing loudly:

1. **EXIF orientation.** cv2.imread applies it, PIL.Image.open does not, and the real capture
   here is Orientation 6 on all 28 frames - so the two libraries disagree about which axis is
   up on the same file. We apply it explicitly, exactly once, and everything downstream sees
   upright pixels.

2. **Intrinsics.** Lifting depth to 3D needs a focal length in pixels. Getting it wrong scales
   the room. It is derived here from EXIF and the derivation is recorded per frame, so the
   report can say where the number came from instead of asserting it.

3. **Downscaling K with the image.** Depth models want ~1k px, not 24 MP. Resizing pixels
   without resizing the intrinsics is the classic way to introduce a clean, invisible scale
   error, so the two always move together here.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import numpy as np
from PIL import Image, ExifTags, ImageOps

from pipeline.types import Frame, RoomCapture, Scale, Scene

WORK_PX = 1024                  # long edge fed to the depth model
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
DOORWAY_RE = re.compile(r"doorway_to_(.+?)(?:[_-]([ab]))?$", re.I)

# A 35mm frame is 36x24mm, diagonal 43.2666mm. "35mm equivalent focal length" is defined by
# matching diagonal field of view, so the diagonal is the correct axis for the conversion.
FILM_DIAGONAL_MM = 43.266615

# Fallback when EXIF carries no focal length at all. A modern phone main camera sits near a
# 26mm equivalent; assuming it is far better than assuming a 35mm-film default, but it is a
# guess and is labelled as one so the wider interval is traceable.
ASSUMED_EQUIV_MM = 26.0


def _exif(im: Image.Image) -> dict:
    """Top-level IFD merged with the EXIF sub-IFD.

    FocalLength and FocalLengthIn35mmFilm live in the sub-IFD behind tag 0x8769, not the top
    level, so reading only getexif() returns Make and Model but none of the optics.
    """
    raw = im.getexif()
    if not raw:
        return {}
    out = {ExifTags.TAGS.get(k, k): v for k, v in raw.items()}
    try:
        out.update({ExifTags.TAGS.get(k, k): v for k, v in raw.get_ifd(0x8769).items()})
    except Exception:
        pass
    return out


def intrinsics_from_exif(exif: dict, w: int, h: int) -> tuple[np.ndarray, str]:
    """Pinhole K for an image of w x h pixels, plus a note on how it was derived.

    The reliable EXIF route is the 35mm-equivalent focal length, because it already folds in
    the sensor size that phone EXIF almost never states directly - there is no
    FocalPlaneXResolution in these files, so actual millimetres alone cannot be converted to
    pixels without knowing how big the sensor is.

        f_px = f_35mm * image_diagonal_px / 43.2666mm

    Principal point is assumed central. That is close but not exact on phone cameras; it is
    also not worth guessing better, because the reconstruction can refine it and a wrong guess
    dressed up as precision is worse than a stated assumption.
    """
    diag_px = float(np.hypot(w, h))
    f35 = exif.get("FocalLengthIn35mmFilm")

    if f35:
        f_px = float(f35) * diag_px / FILM_DIAGONAL_MM
        src = f"exif_35mm_equiv({float(f35):.0f}mm)"
    else:
        f_px = ASSUMED_EQUIV_MM * diag_px / FILM_DIAGONAL_MM
        src = f"assumed_{ASSUMED_EQUIV_MM:.0f}mm_equiv"

    K = np.array([[f_px, 0.0, w / 2.0],
                  [0.0, f_px, h / 2.0],
                  [0.0, 0.0, 1.0]])
    return K, src


def load_frame(path: str, work_px: int = WORK_PX) -> Frame:
    """One image -> Frame with upright pixels and matching intrinsics."""
    with Image.open(path) as raw:
        exif = _exif(raw)
        im = ImageOps.exif_transpose(raw).convert("RGB")   # apply Orientation, once, here

    w0, h0 = im.size
    K, src = intrinsics_from_exif(exif, w0, h0)

    scale = min(1.0, work_px / max(w0, h0))
    if scale < 1.0:
        im = im.resize((max(1, round(w0 * scale)), max(1, round(h0 * scale))), Image.LANCZOS)
        # Intrinsics scale with the pixels. Resizing one without the other is a silent,
        # perfectly clean scale error on every dimension the pipeline later reports.
        K = K.copy()
        K[:2, :] *= scale

    tags: list[str] = []
    stem = os.path.splitext(os.path.basename(path))[0]
    m = DOORWAY_RE.search(stem)
    if m:
        tags += ["doorway", f"to:{m.group(1).lower().strip('_-')}"]

    return Frame(
        image_path=path,
        K=K,
        image=np.asarray(im),
        orig_size=(w0, h0),
        K_source=src,
        tags=tags,
    )


def read_camera_height(capture_dir: str) -> Optional[float]:
    """Operator-stated camera height in metres, if they supplied one. Optional by design."""
    for cand in (os.path.join(capture_dir, "capture_info.txt"),
                 os.path.join(os.path.dirname(os.path.normpath(capture_dir)), "capture_info.txt")):
        if not os.path.isfile(cand):
            continue
        txt = open(cand, encoding="utf-8", errors="replace").read()
        m = re.search(r"camera_height_cm\s*[:=]\s*([0-9.]+)", txt, re.I)
        if m:
            return float(m.group(1)) / 100.0
    return None


def load(capture_dir: str, work_px: int = WORK_PX) -> Scene:
    """Per-room folders of stills -> Scene. One RoomCapture per folder."""
    rooms: list[RoomCapture] = []
    device = "unknown"

    for entry in sorted(os.listdir(capture_dir)):
        d = os.path.join(capture_dir, entry)
        if not os.path.isdir(d):
            continue
        files = sorted(os.path.join(d, f) for f in os.listdir(d)
                       if os.path.splitext(f)[1].lower() in PHOTO_EXT)
        if not files:
            continue

        frames = [load_frame(f, work_px) for f in files]
        if device == "unknown":
            with Image.open(files[0]) as im:
                e = _exif(im)
            device = " ".join(str(e.get(k, "")).strip() for k in ("Make", "Model")).strip() or "unknown"

        # Scale is not resolved at load time: it comes from the depth model and the floor
        # plane, both downstream. Declaring "none" here keeps the tier honest if the scale
        # stage is skipped, rather than defaulting to a silent 1.0 metres-per-unit.
        rooms.append(RoomCapture(
            room_id=entry,
            frames=frames,
            scale=Scale(source="none", evidence="pending: metric depth + floor plane"),
            tier="photo",
            doorway_hints=[t[3:] for f in frames for t in f.tags if t.startswith("to:")],
        ))

    return Scene(tier="photo", device=device, rooms=rooms,
                 camera_height_m=read_camera_height(capture_dir))
