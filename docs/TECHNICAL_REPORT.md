# Technical Report — Cozmo AI Case Study

Handheld capture (photo / video / LiDAR) → dimensioned property plan. One organising rule
drives every design choice below:

> **Models supply structure. Geometry computes measurements.**

A network is only ever asked for depth. Every dimension reported is the distance between
planes fitted to tens of thousands of points — auditable line by line, not a black-box guess.

---

## 1. Architecture

```mermaid
flowchart TD
    subgraph capture["capture/ (tier-specific ingest)"]
        P0[photo folders] --> P1[load + EXIF K]
        V0[video clip] --> V1[sample + odometry]
        L0[".r3d / stray zip"] --> L1[unzip + poses]
        P1 --> P2[metric depth]
        V1 --> V2[metric depth]
        L1 --> L2[LiDAR depth]
    end

    P2 --> LIFT[lift to points + normals]
    V2 --> LIFT
    L2 --> LIFT

    LIFT --> POSES{poses?}
    POSES -->|yes| FUSE[fuse to one world cloud]
    POSES -->|no| GEO
    FUSE --> GEO

    subgraph geometry["geometry/ (tier-agnostic)"]
        GEO["RANSAC planes → merge coplanar"] --> GRAV[gravity: prior-constrained]
        GRAV --> SEL[joint floor/ceiling selection, or abstain]
        SEL --> MAN[conservative Manhattan regularisation]
    end

    MAN --> CH[ceiling height]
    MAN --> WP[wall-pair spans]
    MAN --> OP[openings as holes]

    MAN --> SEG["video/lidar only: doorway-crossing segmentation → per-room walls"]
    SEG --> ADJ["adjacency + drift correction → blueprint"]

    CH --> OUT
    WP --> OUT
    OP --> OUT
    ADJ --> OUT

    subgraph output["output/"]
        OUT["intervals → JSON + PNG/SVG plan"]
    end
```

Everything below `capture/` is **tier-agnostic** — it branches on whether a frame carries a
pose, never on the tier name, so a video whose odometry fails degrades honestly to the
per-frame path instead of pretending it has a trajectory.

---

## 2. Tier design & device matrix

| Tier | Scale source | Poses | Hardware |
|---|---|---|---|
| Photo | Metric3D v2 depth, cross-checked against floor + camera height | None (per-frame) or recovered via cycle-gated multi-view registration | Any iPhone |
| Video | Metric3D v2 depth + RGB-D visual odometry (PnP, metric from frame 1) | Chained, with relocalisation on a broken link | Any iPhone |
| LiDAR | Sensor-native, ARKit Y-up | Every frame (ARKit) | Pro-class iPhone only — fails loudly on other devices |

---

## 3. Single-frame geometry

One photo, four things happen at once: metric depth, plane extraction, and a
floor/ceiling/wall classification driven by a gravity prior (the protocol asks the operator to
hold the phone level) — never by "the largest plane," which was tried and produced 2–22 cm
ceiling heights on real frames.

![Input, depth, planes, classification](report_assets/01_single_frame_geometry.png)

*Corners* come from intersecting adjacent wall planes with the floor, kept or rejected with a
stated reason — but a corner is only as correct as the floor it's intersected against, and
closing corners into a full room polygon is currently fragile: across every real capture this
session, exactly one room ever closed a polygon at all, and its own floor turned out to be a
bed (§7 — the same failure is not a separate bug, it is the same one shown twice). **The
dimension that is actually reliable is wall-pair separation** — the distance between two
opposite wall planes, needing no closed polygon and no correct floor: LiDAR wall pair
**3.60 m vs 3.54 m tape, +1.8%**. Corners and the polygon remain useful for area and adjacency
once the floor is right; they are not yet the load-bearing measurement.

*Openings* are found as holes in a wall's own point support — not a lifted 2D detection box,
which inherits depth error exactly where depth is worst. The door here is clean; the window's
height overshoots the true glass into the sill ledge below it, and the detector's own
confidence (0.69, against 0.81 for the door) already says so:

![Detected door (clean) and window (height overshoots into the sill), correctly projected onto the source photo](report_assets/05_openings.png)

---

## 4. Multi-view: registration, verification, fusion

Unposed photos of one room are registered into a shared frame via SIFT correspondences, then
**cycle-consistency gated** before any pose is trusted. A pairwise fit can look perfect (a few
centimetres of residual) while the *composed* pose is wrong by metres — only a closed loop
catches that, and a spanning tree has no loops by construction. Every edge the tree doesn't use
is exactly the evidence needed to audit the edges it does.

![Overlap graph: kept, cycle-cut, and implausible-height edges](report_assets/06_multiview_graph.png)

The result, when it passes: six independently-captured photos landing on the *same* wall
lines, not six disagreeing slabs — the direct visual test of whether registration is real.

![Fused cloud, coloured by source photo](report_assets/07_multiview_fusion.png)

Recovered camera positions are a free sanity check on their own — real handheld captures sit
within a metre or two of each other, never scattered across a room:

![Recovered camera positions, top-down](report_assets/08_camera_positions.png)

**Video/LiDAR stitching** (multi-room) reuses this same fused-cloud machinery per detected
room, then adds:
- **Doorway-crossing segmentation** — a room change is confirmed by the *scene* changing
  sharply (SIFT similarity dropping against the clip's own distribution), not by inferring it
  from camera density. This needs no pose, so it survives frames where odometry fails.
- **Re-identification** — a segment is merged back into an earlier one if its walls coincide
  *and* its centroid sits in the same place (wall-matching alone is fooled by two different
  rooms sharing a corridor wall-line — found and fixed as a real bug, not hypothesised).
- **Plane-anchored drift correction** — each room's *position* (not its own shape) is nudged
  vertically to a shared floor level and horizontally along matched walls. The on/off ablation
  the brief requires is a real, computed number, not a description.

**Photo-tier property stitching** turned out not to need a separate capture protocol. The
same cycle-gated registration above never assumed its photos all came from one room — that
was simply the only thing anyone had called it with. Running it on **every photo from every
room at once** lets any real cross-room overlap (a photo that incidentally sees through a
doorway) supply the correspondence a deliberate doorway-pair shot would have. Run on the real
5-room, 28-photo capture:

![Property-wide registration: one room fully connected (white), every cross-room edge correctly rejected](report_assets/12_property_wide_graph.png)

Only one room's photos ever connect; every cross-room edge is rejected — twice for an
implausible camera height, twice for a depth-scale ratio outside sanity (1.54×, 1.76×). This
capture was never shot with cross-room correspondence in mind, so refusal is the *correct*
answer, not a shortfall: the same gates that catch false positives on video catch this too.
That the mechanism itself works is checked separately — two synthetic photos in different
rooms given genuinely shared content produce a clean match (258 inliers, 0 cm residual,
depth-scale ratio 1.00) — so the gate is proven able to *accept* good cross-room evidence, not
only reject bad.

---

## 5. Error budget & calibration

Ground truth: full laser survey, 5 rooms, 20 walls, 5 doors.

| Room | Ceiling error (gate ≤1.5 cm) | Worst wall error (gate ±8%) |
|---|---|---|
| Hallway | FAIL, 11.4 cm | FAIL, 23.6% |
| Kitchen | FAIL, 21.5 cm | FAIL, 28.3% |
| Room 1 | abstained | PARTIAL, 23.8% (1 span inside gate) |
| Room 2 | abstained | FAIL, 26.3% |
| Room 3 | FAIL, 23.8 cm | FAIL, 10.2% |

No room passes either gate outright. Two facts explain most of it:

1. **Depth bias grows with distance.** Ceiling height (measured close-up, ~1.5 m) comes out
   long (+4–8%); wall spans (measured across the room, 2–5 m) come out short (−23 to −38%) —
   the *same* underlying bias, read at two different ranges.
2. **Multi-view reduces random error, not the shared bias.** Fusing several biased single-frame
   estimates narrows the spread; it cannot correct a bias every frame shares. That's why gated
   multi-view moves Room 3's wall error 37.6% → 10.2% (fix-loop territory) but never crosses
   ceiling's 1.5 cm bar.

---

## 6. The fix loop

**Worst gate:** photo-tier ceiling height. 28 frames, mean −12.2%, SD 24.1%, three frames off
by −63 to −84% (a bed, or a doorway's far side, mistaken for the floor).

**Root cause:** `classify()` picked the two largest horizontal planes as floor/ceiling. Looking
through an open doorway, that can be a near floor and a door head — a physically impossible
separation reported as a confident number.

**Fix:** score every candidate pair on six signals multiplied together (support, parallelism,
horizontality, separation, bounding, camera height) and abstain below threshold.

**Result:** catastrophic frames 3 → 0, SD 24.1% → 10.6%. Does not cross the 1.5 cm gate — the
report says why (§5): the remaining error is depth-model scale bias, not plane selection, and
no amount of better plane selection fixes a biased depth map.

---

## 7. Known failure modes

**The floor plane lands on the bed when no real floor is visible.** Not a hypothesis — found
with the diagnostic overlays. The `bounding` signal can't reject it because the true floor was
never in frame; the bed genuinely is the lowest surface in that cloud.

![Bed classified as floor: reported ceiling 2.29 m against 2.64 m true](report_assets/02_failure_bed_as_floor.png)

The same failure, not a different one, propagates downstream: the corner-finding and
room-polygon code are themselves correct — given a floor, they intersect walls against it and
close a polygon exactly as designed — but a corner intersected against the bed is a corner at
bed height, and a "closed polygon" over the bed reports a ceiling 33 cm short of true. (An
earlier draft of this report used these two images as a *success* example, on the strength of
the polygon actually closing, without checking which floor it had closed against. It hadn't
checked far enough — this is that correction.)

![The "accepted" corner sits at bed height, not floor height](report_assets/10_bed_as_floor_corners.jpg)
![The "closed" polygon: area and ceiling height both computed from the bed](report_assets/11_bed_as_floor_polygon.jpg)

**The room polygon fails when a capture spills into the next space.** `select_room_walls`
requires nothing behind a wall; a doorway lets the sensor see past it, so nothing qualifies.
Wall-pair separation exists specifically because it needs no closed polygon.

![No polygon closes on a capture that sees past its own walls](report_assets/09_lidar_room_failure.png)

**Doorway-crossing segmentation did not fire on the second real video capture**, despite a
confirmed multi-room walkthrough (checked by hand: living area → hallway → bedroom). Root
cause, measured rather than guessed: real handheld footage has a noisy, right-skewed
frame-similarity distribution (median 0.089, MAD 0.095) — the threshold
`median − 2.5·MAD` computes to **−0.148**, below zero, so no pair can ever be flagged. The
method is validated end-to-end on synthetic data (21/21 checks) and correctly declines to
fabricate rooms rather than guess wrong; it needs a threshold that can't go negative on a
noisy real trace, not a redesign.

**Video odometry coverage degrades on a hard walk.** 14 of 60 frames posed on the first
capture, 4 of 80 on the second (longer, more room changes). A broken link now retries against
the last posed frame for 15 frames before giving up — a real fix, verified — but does not
fully solve sparse coverage on a long multi-room walk.

**Trajectory-density segmentation over-segments a large or complex real space.** Re-running
the team-supplied LiDAR capture through the current pipeline (ARKit poses nearly every frame,
so doorway-crossing detection never applies — no RGB is decoded for that tier) stitched it
into **9 sub-rooms** with reported ceiling heights from **1.79 m to 3.08 m**, a 1.3 m spread.
These are substantial, well-populated clusters (5–88 frames each), not noise-level slivers:

![Camera trajectory branches into 9 clusters; ceiling height is not consistent across them](report_assets/13_lidar_oversegmentation.png)

A 1.3 m ceiling-height spread across "rooms" of one capture is not physically plausible for a
normal residential space — it is the signature of the bed-as-floor failure (§ above)
recurring on sub-regions of a large, cluttered, or open-plan space, not 9 verified rooms. No
ground truth exists for this property to confirm either reading. What IS clear: density-based
segmentation has no equivalent of the camera-height plausibility gate that already exists for
individual frame placement, and should — a ceiling-height-spread check across proposed
sub-rooms, analogous to `MAX_CAMERA_HEIGHT_DEV_M`, would catch this class of over-segmentation
before it reaches a confident 9-room stitch.

---

## 8. Damage detection: first pass

`pipeline/damage/` was three `NotImplementedError` stubs. It is now a working first pass —
detection, concealed-damage rules, scope line items — unit-tested on synthetic damage
(`tests/test_damage.py`, 8 checks). It is **not wired into `run.py`**: the
`detect(frame, surfaces)` entry point still raises, because no staged-damage capture exists to
validate a `measure.py` hook against (`benchmark/ground_truth/damage.csv` is placeholder rows).

**Method.** Detection runs in image space — a stain or a spalled patch is a colour anomaly,
not something depth shows — but every reported extent is metric. Each region is rasterised
onto its wall plane in the wall's own `(u, v)` frame, the same `_wall_frame` axes `openings.py`
uses to turn a hole into a width and a height. A cell is anomalous when its sampled colour
deviates from *that wall's own median colour* by more than `4·MAD` (floored at 8/255), so a
beige wall and a white wall are each judged on their own distribution rather than an absolute
RGB threshold. Class is aspect-ratio only: long/short ≥ 4 → `crack`, else `water_stain`. The
concealed-damage rules then fire at most one match per region and name it.

**Run against Room 1.** Room 1 carries no *staged* damage — but it turns out to have
extensive *real* damp damage: a band of blown, spalling plaster along the skirting on both
sides of the bathroom door, plus a water stain on the ceiling around the fan. Six photos,
unfitted thresholds, no ground truth:

![First-pass damage detector on Room 1: red = classified crack, blue = classified water_stain](report_assets/14_damage_room1.png)

| | |
|---|---|
| Regions flagged | 20 across 6 frames (14 `water_stain`, 6 `crack`) |
| Clean frames | 1 of 6 (IMG_0445 — no false positives) |
| Concealed-damage rules fired | 2, both `CONCEAL-WATER-02` (IMG_0444) |

What it got right:

- **IMG_0446** — two regions land squarely on the real spalled-plaster band at the base of
  the wall, both sides of the doorway. Right location, right "damage low on a wall" signal.
- That low-wall geometry is exactly what triggers `CONCEAL-WATER-02` ("substrate and skirting
  saturation behind the finish") — the correct rule for what is physically there.

What it got wrong:

- **Class is unreliable.** The same damp/spalled band is `crack` in IMG_0446 and IMG_0439,
  `water_stain` in IMG_0444 — the aspect ratio of an irregular real patch is noisy. In
  IMG_0439 the misclassification cost a flag directly: the band at floor level was labelled
  `crack`, so `CONCEAL-WATER-02` — a `water_stain` rule — never got to fire on it.
- **High false-positive load.** By eye, most of the 20 regions are the poster collage, the
  framed mirror, the guitar, the curtain edge, and the patterned bedsheet (IMG_0440's two
  "cracks" are both on the duvet) — any hard colour edge on a plane RANSAC accepted as a wall.
- **No cross-frame association** — the one physical damp band is counted 2–4 times.
- **Height-above-floor is unreliable on the per-frame path** — values run −0.02 m to 2.65 m;
  the 2.65 m "wall" stain is really the ceiling stain around the fan, assigned to a wall
  plane. The `max_height_m = 0.5` rule gate cannot defend itself on a floor estimate that loose.

**Before this pass is trustworthy** it needs: staged damage with tape-measure ground truth to
calibrate the colour threshold and set a real class boundary; a texture/edge filter to reject
posters, mirrors, and fabric; cross-frame region association; and correct surface assignment,
so a ceiling stain reaches `CONCEAL-WATER-01` instead of a wall rule.

---

## 9. Status against the brief

| | Status |
|---|---|
| All 3 tiers, one command, one output contract | Done |
| Photo multi-view registration, cycle-verified | Done |
| Video/LiDAR multi-room stitching + blueprint | Built, synthetic-validated; no real multi-room result has closed yet (§7) |
| Fix loop, declared and shipped | Done |
| Ceiling / wall gates | Fail — root cause identified, not a mystery |
| Repeatability gate | Two real video captures of one property exist; not yet cross-scored |
| Head-to-head vs incumbent | Not built |
| Damage detection | First pass built, synthetic-tested; run once against real Room 1 (§8). Not wired into `run.py` — no staged-damage capture to validate the hook |

Full row-by-row detail: `docs/COMPLIANCE_MATRIX.md`. Reproduction commands: `README.md`.
