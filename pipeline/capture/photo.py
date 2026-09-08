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

# iPhones shoot HEIC by default. Our own benchmark happens to be JPEG because the operator set
# "Most Compatible", so nothing here ever exercised HEIC - but the walk-in test uses THEIR
# phone with THEIR settings, and a decoder we never installed is not a defensible way to fail
# in front of an examiner. Registered here if available; if it is not and a HEIC turns up, the
# loader says exactly which package to install rather than dying inside PIL.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:
    HEIF_OK = False

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
    if os.path.splitext(path)[1].lower() in {".heic", ".heif"} and not HEIF_OK:
        raise SystemExit(
            f"{os.path.basename(path)} is HEIC and no HEIC decoder is installed."
            f"  Fix: pip install pillow-heif."
            f"  Or on the phone: Settings > Camera > Formats > Most Compatible, and re-shoot.")
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


def _photo_files(d: str) -> list[str]:
    return sorted(os.path.join(d, f) for f in os.listdir(d)
                  if os.path.splitext(f)[1].lower() in PHOTO_EXT)


def load(capture_dir: str, work_px: int = WORK_PX) -> Scene:
    """Stills -> Scene. One RoomCapture per room folder, or one room for a flat folder.

    BOTH LAYOUTS ARE ACCEPTED, and that is not a convenience.

    The per-room layout (`capture/Kitchen/*.jpg`) is what our protocol asks for and what our
    own benchmark uses. But a flat folder of photos is the most natural thing a person hands
    you, and until now it produced ZERO rooms - silently, exit code 0, an empty result.json,
    and a console line reading "tier=photo 17.8s" that looks exactly like success. At a
    walk-in test where someone drops a folder of photos of one room on us, that is a total
    failure wearing the costume of a clean run.

    So: subfolders if any contain photos, otherwise the folder itself as a single room named
    after it. Nothing is guessed - the two cases are distinguished by where the files are.
    """
    rooms: list[RoomCapture] = []
    device = "unknown"

    if not os.path.isdir(capture_dir):
        raise SystemExit(f"not a directory: {capture_dir}")

    entries = sorted(os.listdir(capture_dir))
    subdirs = [e for e in entries if os.path.isdir(os.path.join(capture_dir, e))
               and _photo_files(os.path.join(capture_dir, e))]
    flat = _photo_files(capture_dir)

    if not subdirs and not flat:
        raise SystemExit(
            f"no photos found in {capture_dir}. "
            f"Expected either per-room subfolders (capture/Kitchen/*.jpg) or photos "
            f"directly in this folder. "
            f"Recognised extensions: {', '.join(sorted(PHOTO_EXT))}")

    # A flat folder is one room. Named after the folder so the result is not labelled "room".
    layout = [(e, os.path.join(capture_dir, e)) for e in subdirs] or [
        (os.path.basename(os.path.normpath(capture_dir)) or "room", capture_dir)]

    for entry, d in layout:
        files = _photo_files(d)
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

    if not rooms:
        raise SystemExit(f"found photo files under {capture_dir} but built no rooms from them")

    return Scene(tier="photo", device=device, rooms=rooms,
                 camera_height_m=read_camera_height(capture_dir))
