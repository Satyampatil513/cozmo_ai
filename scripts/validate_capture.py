"""Check a capture is usable before trusting it - and while a re-shoot is still cheap.

    python scripts/validate_capture.py benchmark/raw/photo
    python scripts/validate_capture.py benchmark/raw/video --tier video

Why this exists, and why it exists early: the expensive failure on this project is not a
wrong number, it is discovering three weeks later that the lens flipped to 0.5x halfway
through a room, or that no doorway pair was ever shot between the hall and bedroom three,
or that a room has three usable stills. None of those are recoverable after the fact and
all of them are invisible at capture time. Every one is detectable in seconds from the
files themselves.

So this runs against raw folders and needs none of the reconstruction pipeline. It answers
one question: what do I have to re-shoot, and which room is it in.

FAIL means a gate in the brief cannot be met with these files. WARN means it will work but
worse than it should. Exit code is non-zero if anything FAILed.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ExifTags

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXT = {".mov", ".mp4", ".m4v"}

CAMERA_HEIGHT_RANGE_CM = (110.0, 190.0)
MIN_STILLS = 2                 # the brief's floor: "2 to 8 stills per room"
GOOD_STILLS = 6                # below this the intervals widen for no good reason
MIN_VIDEO_S = 20.0             # shorter than this cannot cover a room at walking pace
FRAME_BLUR_FLOOR = 60.0        # Laplacian variance, measured at BLUR_CANONICAL_W
BLUR_CANONICAL_W = 1200        # every frame is resized to this width before measuring
VIDEO_SAMPLE_FRAMES = 40

# Overlap check. Two views are "linked" if they survive a fundamental-matrix RANSAC with at
# least this many inliers - roughly the floor for a usable two-view geometry.
OVERLAP_MIN_INLIERS = 30
OVERLAP_WORK_PX = 1000         # long edge; SIFT on 24 MP frames is pointlessly slow
OVERLAP_NEAR_DUPLICATE = 400   # above this the two shots are essentially the same viewpoint

# Matches doorway_to_<room>.jpg and the older doorway_to_<room>_a/_b.jpg pair form.
# <room> is matched against the actual folder names rather than a fixed pattern, so the
# operator can call their rooms whatever they like.
DOORWAY_RE = re.compile(r"doorway_to_(.+?)(?:[_-]([ab]))?$", re.I)


@dataclass
class Finding:
    level: str        # FAIL | WARN | OK
    scope: str
    message: str


class Report:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def fail(self, scope: str, msg: str) -> None:
        self.findings.append(Finding("FAIL", scope, msg))

    def warn(self, scope: str, msg: str) -> None:
        self.findings.append(Finding("WARN", scope, msg))

    def ok(self, scope: str, msg: str) -> None:
        self.findings.append(Finding("OK", scope, msg))

    def n(self, level: str) -> int:
        return sum(1 for f in self.findings if f.level == level)


def _imread(path: str) -> np.ndarray | None:
    """Read an image, tolerating HEIC absence rather than crashing the whole run."""
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is not None:
        return img
    try:
        with Image.open(path) as im:
            return np.array(im.convert("L"))
    except Exception:
        return None


def _exif(path: str) -> dict:
    """Flatten the top-level IFD and the EXIF sub-IFD into one dict.

    getexif() alone returns only the top-level IFD, which carries Make/Model/Orientation but
    NOT FocalLength or FocalLengthIn35mmFilm - those live in the EXIF sub-IFD behind tag
    0x8769. Reading only the top level made every real iPhone capture report "no EXIF focal
    length", which silently disabled the lens-consistency check on exactly the files it
    exists to protect.
    """
    try:
        with Image.open(path) as im:
            raw = im.getexif()
            if not raw:
                return {}
            out = {ExifTags.TAGS.get(k, k): v for k, v in raw.items()}
            try:
                sub = raw.get_ifd(0x8769)
            except Exception:
                sub = {}
            out.update({ExifTags.TAGS.get(k, k): v for k, v in sub.items()})
            return out
    except Exception:
        return {}


def _blur(img: np.ndarray) -> float:
    """Laplacian variance at a fixed width, so one threshold means one thing.

    Laplacian variance counts edge energy per pixel, so it falls as resolution rises: the
    same sharp frame scores 39 at 4284 px wide and 307 at 1200 px. A fixed threshold tuned at
    one resolution therefore condemns every high-megapixel capture as blurred - which is
    what a 24 MP iPhone frame did here. Resizing first makes the number comparable across
    devices and camera settings.
    """
    if img.shape[1] > BLUR_CANONICAL_W:
        h = int(img.shape[0] * BLUR_CANONICAL_W / img.shape[1])
        img = cv2.resize(img, (BLUR_CANONICAL_W, h), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def _focal35(path: str) -> float | None:
    e = _exif(path)
    v = e.get("FocalLengthIn35mmFilm") or e.get("FocalLength")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check_photo_room(room: str, files: list[str], rep: Report) -> None:
    scope = room

    if len(files) < MIN_STILLS:
        rep.fail(scope, f"{len(files)} still(s); the brief's floor is {MIN_STILLS}")
        return
    if len(files) < GOOD_STILLS:
        rep.warn(scope, f"{len(files)} stills; {GOOD_STILLS}-8 is the working set")

    # --- decode and resolution consistency -----------------------------------
    # Mixed resolutions in one room usually means a mode changed mid-shoot (a crop, a
    # screenshot, a shot pulled from a different app). The reconstruction assumes one
    # camera, so it is worth knowing before rather than after.
    shapes: dict[tuple[int, int], int] = defaultdict(int)
    for f in files:
        img = _imread(f)
        if img is None:
            rep.warn(scope, f"could not decode {os.path.basename(f)}")
            continue
        shapes[img.shape[:2]] += 1
    if len(shapes) > 1:
        detail = ", ".join(f"{w}x{h} x{n}" for (h, w), n in sorted(shapes.items()))
        rep.warn(scope, f"mixed resolutions ({detail}) - all frames should come "
                        f"straight from the camera at one setting")
    elif shapes:
        (h, w), n = next(iter(shapes.items()))
        rep.ok(scope, f"{n} frames, {w}x{h}")

    # --- lens consistency ---------------------------------------------------
    # Switching between 0.5x, 1x and 3x mid-room changes the intrinsics. The reconstruction
    # does not fail loudly on this; it just quietly gets worse, which is the dangerous kind.
    focals = {}
    for f in files:
        fl = _focal35(f)
        if fl is not None:
            focals.setdefault(round(fl), []).append(os.path.basename(f))
    if len(focals) > 1:
        detail = ", ".join(f"{k}mm x{len(v)}" for k, v in sorted(focals.items()))
        rep.fail(scope, f"mixed focal lengths ({detail}) - the lens was switched mid-room; "
                        f"reshoot at 1x only")
    elif not focals:
        rep.warn(scope, "no EXIF focal length - cannot verify a single lens was used")

    # --- blur ---------------------------------------------------------------
    blurry = []
    for f in files:
        img = _imread(f)
        if img is None:
            continue
        if _blur(img) < FRAME_BLUR_FLOOR:
            blurry.append(os.path.basename(f))
    if blurry:
        rep.warn(scope, f"soft/blurred frames: {', '.join(sorted(blurry))}")

    # --- format -------------------------------------------------------------
    heic = [os.path.basename(f) for f in files if os.path.splitext(f)[1].lower() in {".heic", ".heif"}]
    if heic:
        rep.warn(scope, f"{len(heic)} HEIC file(s) - set Camera > Formats > Most Compatible")


def check_video_room(room: str, files: list[str], rep: Report) -> None:
    scope = room
    if not files:
        rep.fail(scope, "no video clip")
        return
    for f in files:
        name = os.path.basename(f)
        cap = cv2.VideoCapture(f)
        if not cap.isOpened():
            rep.fail(scope, f"{name}: cannot open")
            continue
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        dur = n / fps if fps > 0 else 0.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if dur <= 0:
            rep.warn(scope, f"{name}: could not read duration")
        elif dur < MIN_VIDEO_S:
            rep.warn(scope, f"{name}: only {dur:.0f}s - too short to cover a room at "
                            f"walking pace")

        # Sample across the clip for motion blur.
        blurry = 0
        idx = np.linspace(0, max(0.0, n - 1), VIDEO_SAMPLE_FRAMES).astype(int) if n > 1 else [0]
        for i in idx:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
            ok, frame = cap.read()
            if not ok:
                continue
            if _blur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)) < FRAME_BLUR_FLOOR:
                blurry += 1
        cap.release()

        rep.ok(scope, f"{name}: {dur:.0f}s {w}x{h} @{fps:.0f}fps")
        if blurry > len(idx) * 0.4:
            rep.warn(scope, f"{name}: {blurry}/{len(idx)} sampled frames soft - walk slower")


def _sift_features(path: str):
    img = _imread(path)
    if img is None:
        return None
    s = OVERLAP_WORK_PX / max(img.shape)
    if s < 1.0:
        img = cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)),
                         interpolation=cv2.INTER_AREA)
    return cv2.SIFT_create(nfeatures=4000).detectAndCompute(img, None)


def _match_inliers(a, b) -> int:
    if a is None or b is None or a[1] is None or b[1] is None:
        return 0
    good = [m for m, n in cv2.BFMatcher().knnMatch(a[1], b[1], k=2)
            if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return 0
    p1 = np.float32([a[0][m.queryIdx].pt for m in good])
    p2 = np.float32([b[0][m.trainIdx].pt for m in good])
    _, mask = cv2.findFundamentalMat(p1, p2, cv2.FM_RANSAC, 3.0, 0.99)
    return int(mask.sum()) if mask is not None else 0


def check_overlap(room: str, files: list[str], rep: Report) -> None:
    """Do these photos actually see enough of each other to reconstruct?

    This is the check that most directly predicts whether the photo tier will work at all,
    and it is the one thing you cannot judge by eye: a set of frames can each be sharp,
    well-exposed and correctly framed while sharing almost no common surface, and the failure
    only shows up weeks later as a reconstruction that will not converge.

    Method is a classical two-view geometry test - SIFT, ratio test, fundamental-matrix
    RANSAC - and the result is the connectivity of the graph it induces. A split graph means
    the room was photographed as two or more islands with nothing tying them together.

    Read this as a LOWER bound, not a verdict. SIFT is weak exactly where indoor rooms are
    hard: painted walls with no texture, and wide baselines between viewpoints. The
    feed-forward models we intend to use (VGGT / DUSt3R class) are built for sparse overlap
    and routinely succeed where SIFT returns nothing. So a split graph here is a warning to
    look at, not proof the capture is unusable - which is why it is a WARN and never a FAIL.
    """
    if len(files) < 2:
        return
    feats = [_sift_features(f) for f in files]
    n = len(files)
    M = np.zeros((n, n), dtype=int)
    for i in range(n):
        for j in range(i + 1, n):
            M[i, j] = M[j, i] = _match_inliers(feats[i], feats[j])

    adj = M >= OVERLAP_MIN_INLIERS
    seen, stack = {0}, [0]
    while stack:
        c = stack.pop()
        for k in np.flatnonzero(adj[c]):
            if int(k) not in seen:
                seen.add(int(k))
                stack.append(int(k))

    names = [os.path.basename(f) for f in files]
    if len(seen) < n:
        stranded = sorted(names[i] for i in range(n) if i not in seen)
        rep.warn(room, f"overlap graph splits: {len(seen)}/{n} frames linked, "
                       f"not reachable: {', '.join(stranded)} - shots are too far apart to "
                       f"match classically")
    else:
        rep.ok(room, f"overlap graph connected, all {n} frames linked")

    dupes = [(names[i], names[j]) for i in range(n) for j in range(i + 1, n)
             if M[i, j] > OVERLAP_NEAR_DUPLICATE]
    for a, b in dupes:
        rep.warn(room, f"{a} and {b} are near-duplicate viewpoints - one of the 2-8 stills "
                       f"is spent twice on the same view")


def check_adjacency(rooms: dict[str, list[str]], rep: Report) -> None:
    """Doorway shots are the only adjacency evidence photo folders carry.

    The brief fails a photo path that handles single rooms only, and per-room folders
    contain nothing that says two rooms touch. If this graph is not connected, the
    whole-property stitch gate cannot be met no matter how good the per-room geometry is.
    One photo taken from the doorway into the next room is enough to make the edge.
    """
    edges: set[tuple[str, str]] = set()

    for room, files in rooms.items():
        for f in files:
            stem = os.path.splitext(os.path.basename(f))[0]
            m = DOORWAY_RE.search(stem)
            if not m:
                continue
            named = m.group(1).lower().strip("_-")
            # Accept an exact folder name, or either name being a prefix of the other, so
            # "doorway_to_hall" finds room_05_hall and vice versa.
            target = next(
                (r for r in rooms
                 if r.lower() == named
                 or r.lower().endswith(named)
                 or named.startswith(r.lower())),
                None,
            )
            if target is None:
                rep.warn(room, f"doorway shot names '{named}', which is not a room folder")
                continue
            if target != room:
                edges.add(tuple(sorted((room, target))))

    if not edges:
        rep.fail("adjacency", "no doorway shots anywhere - the rooms cannot be placed "
                              "relative to each other and the whole-property stitch gate fails")
        return

    # Connectivity over the discovered rooms.
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    start = next(iter(rooms))
    seen, stack = {start}, [start]
    while stack:
        cur = stack.pop()
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    missing = sorted(set(rooms) - seen)
    if missing:
        rep.fail("adjacency", f"not reachable through any doorway shot: {', '.join(missing)} "
                              f"- shoot through the doorway connecting each to the rest")
    else:
        rep.ok("adjacency", f"all {len(rooms)} rooms connected via {len(edges)} doorway shot(s)")


def check_camera_height(root: str, rep: Report) -> None:
    """The one number the protocol asks the operator for.

    Photo and video are scale-ambiguous, and the protocol deliberately places nothing in
    the room. Metres come from a metric depth model, with the floor plane plus this height
    as the second, independent estimate - and it is their disagreement that gives every
    interval its width. Missing it does not break the run; it costs us the cross-check and
    widens everything, which is worth saying out loud while it can still be measured.
    """
    for cand in (os.path.join(root, "capture_info.txt"),
                 os.path.join(os.path.dirname(root.rstrip("/\\")), "capture_info.txt")):
        if not os.path.isfile(cand):
            continue
        txt = open(cand, encoding="utf-8", errors="replace").read()
        m = re.search(r"camera_height_cm\s*[:=]\s*([0-9.]+)", txt, re.I)
        if not m:
            rep.warn("capture", f"{os.path.basename(cand)} has no camera_height_cm line")
            return
        h = float(m.group(1))
        lo, hi = CAMERA_HEIGHT_RANGE_CM
        if not (lo <= h <= hi):
            rep.warn("capture", f"camera_height_cm = {h:g}, outside the plausible "
                                f"{lo:g}-{hi:g} cm range - is it in centimetres?")
        else:
            rep.ok("capture", f"camera height {h:g} cm")
        return
    rep.warn("capture", "no capture_info.txt with camera_height_cm - the floor-plane scale "
                        "cross-check is unavailable and all intervals widen")


def collect(root: str, exts: set[str]) -> dict[str, list[str]]:
    rooms: dict[str, list[str]] = {}
    for entry in sorted(os.listdir(root)):
        d = os.path.join(root, entry)
        if not os.path.isdir(d):
            continue
        files = sorted(
            os.path.join(d, f) for f in os.listdir(d)
            if os.path.splitext(f)[1].lower() in exts
        )
        rooms[entry] = files
    return rooms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("capture_dir")
    ap.add_argument("--tier", choices=["photo", "video"], default=None,
                    help="default: inferred from the files present")
    ap.add_argument("--fast", action="store_true",
                    help="skip the SIFT overlap check (the slow part)")
    args = ap.parse_args()

    root = args.capture_dir
    if not os.path.isdir(root):
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    tier = args.tier
    if tier is None:
        vids = collect(root, VIDEO_EXT)
        tier = "video" if any(v for v in vids.values()) else "photo"

    rooms = collect(root, VIDEO_EXT if tier == "video" else PHOTO_EXT)
    rooms = {k: v for k, v in rooms.items() if v} or rooms

    rep = Report()
    if not rooms:
        rep.fail("capture", f"no room folders with {tier} files under {root}")
    else:
        for room, files in rooms.items():
            if tier == "photo":
                check_photo_room(room, files, rep)
                if not args.fast:
                    check_overlap(room, files, rep)
            else:
                check_video_room(room, files, rep)
        if tier == "photo":
            check_adjacency(rooms, rep)
        check_camera_height(root, rep)

    width = max((len(f.scope) for f in rep.findings), default=10)
    print(f"\ncapture: {root}   tier: {tier}   rooms: {len(rooms)}\n")
    for level in ("FAIL", "WARN", "OK"):
        for f in rep.findings:
            if f.level == level:
                print(f"  {f.level:4}  {f.scope:<{width}}  {f.message}")
    print(f"\n  {rep.n('FAIL')} fail, {rep.n('WARN')} warn, {rep.n('OK')} ok\n")

    if rep.n("FAIL"):
        print("  Re-shoot the FAIL items. They are gates, not preferences.\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
