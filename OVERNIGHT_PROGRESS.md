# Overnight progress

All three tiers now run end to end through one command and produce the same output contract.
Everything below was measured on the real captures in `benchmark/raw/`; no number here is
estimated or carried over from a previous run.

---

## What works

| | Status | Evidence |
|---|---|---|
| Photo tier | runs, per-frame mode | `out_photo/result.json`, 5 rooms |
| Video tier | runs, fused with RGB-D odometry | `out_video/result.json` |
| LiDAR `.r3d` tier | runs, fused with Record3D poses | `out_lidar/result.json` |
| Shared measurement path | all three reach `pipeline/measure.py` | `tests/test_smoke.py` |
| Multi-view fusion | posed captures only | `pipeline/geometry/fuse.py` |
| Opening detection | first pass, geometric | door height +1.9% vs tape |
| Photo fix loop | before/after regenerable + ablation | `benchmark/results/fix_loop_photo.md` |
| Smoke tests | all pass, skip cleanly without data | `python tests/test_smoke.py` |

### Headline numbers

**Ceiling height** (truth: 2.64 m in the 3BHK by tape; 2.6 m for the `.r3d` room, from its
own `measurements.md`):

| Tier | Result | Error | Time |
|---|---|---|---|
| LiDAR, fused | 2.700 m | **+3.8%** | 62 s |
| Video, fused | 2.830 m | **+7.2%** | 358 s |
| Photo, per-frame | 2.288–2.952 m by room | **−13.3% to +11.8%** | 608 s |

**Wall dimensions**, from opposite-pair plane separation:

| Tier | Measured | Truth | Error |
|---|---|---|---|
| LiDAR pair 1 | 3.603 m | 3.54 m | **+1.8%** |
| LiDAR pair 2 | 3.111 m | 3.77 m | −17.5% (probably not the room's second dimension) |
| Video pairs | 3.717 m, 3.565 m | — | no ground truth for that room yet |

**Openings**, LiDAR tier, against the friend's tape:

| Detected | Width | Height | Sill | Truth |
|---|---|---|---|---|
| door | 0.90 m | 2.15 m | 0.03 m | door 2.11 m → **+1.9%** |
| window | 0.90 m | 1.10 m | 0.95 m | window 1.17 m (which dimension is unclear) |
| door (false positive) | 1.30 m | 0.85 m | 0.03 m | — |

**Photo fix loop**, baseline vs shipped joint selector, both regenerable:

| | Mean | SD | Catastrophic | Abstained |
|---|---|---|---|---|
| before (two largest planes) | −12.2% | 24.1% | 3 | 0 |
| after (joint scoring) | **+1.8%** | **10.6%** | **0** | 3 |

---

## Approach

The organising principle, and the reason every design decision below falls out of it:

> **Models supply structure. Geometry computes measurements.**

No network is ever asked "how wide is this room". A network is asked for depth and surface
orientation; a room dimension is then the distance between two planes fitted to hundreds of
thousands of points. This is both more accurate and defensible line by line, and the
measurements confirm it: the deterministic half is exact on clean synthetic input (0.00% wall
error, ceiling to 0.001 m) and contributes ~0.011% at 5 cm of per-point noise, while the
learned half contributes essentially the entire error budget.

```
                    photo folders        video clip           .r3d
                          |                  |                  |
   capture/          load + EXIF K      sample + odometry    unzip + poses
                          |                  |                  |
                    metric depth        metric depth        LiDAR depth
                          \                  |                  /
                           `--------- lift to points + normals -'
                                             |
                                    [poses?] --- yes ---> fuse to one world cloud
                                             |
   geometry/                         RANSAC planes -> merge coplanar
                                             |
                                     gravity (prior-constrained)
                                             |
                              joint floor/ceiling selection (or abstain)
                                             |
                                 conservative Manhattan regularisation
                                             |
                    +------------------------+------------------------+
                    |                        |                        |
              ceiling height          wall-pair spans          openings as holes
                                             |
   output/                          intervals -> JSON
```

Everything from `pipeline/measure.py` down is shared by all three tiers, and it branches on
**whether frames carry poses**, never on the tier name — so a video whose odometry failed
degrades honestly to the per-frame path instead of pretending it has a trajectory.

---

### 1. Scale: where metres come from

Photos contain no absolute size. A room and a scale model of it produce identical pixels, so
metres must enter from outside the images.

**Chosen:** a monocular *metric* depth model, currently **Metric3D v2 ViT-Small**.

**Why over Depth-Anything V2's metric head**, which we shipped first and replaced: Metric3D
consumes intrinsics. It rescales the image so its effective focal length matches a canonical
1000 px, predicts in that space, then undoes the transform — so the metric result is
conditioned on the real optics rather than on whatever field of view the training
distribution implied. Measured effect: mean ceiling error **+26.1% → −12.2%**, and the
Hallway, where the old model implied the camera floated 2.4 m above its own floor, went
**+58.9% → +8.6%**.

**Rejected — a printed scale card.** Shipped, then deliberately removed. It would have given
~1% scale error against ~10% today, but the brief calls the photo tier "any picture in,
results out", the incumbents we are benchmarked against need no props, and decisively: our
benchmark must be captured the same way Cozmo will capture at the walk-in test, or the
benchmark predicts nothing about the 30% of the score that runs cold on their capture.

**Rejected as primary — floor plane + operator camera height.** Designed as a second,
uncorrelated scale source. Testing killed it as a corrector: assuming a constant true camera
height and solving per room gives 1.13 m to 1.63 m, so the depth field is non-uniformly
distorted rather than merely scaled, and one multiplicative correction cannot fix it. It
survives as a **detector** — a frame implying a 2.4 m camera height is self-evidently wrong —
which is exactly how it found the Hallway failure before any tape existed.

### 2. Lifting depth to an oriented point cloud

`P = K⁻¹ · [u,v,1] · depth`, with a normal per point from central differences on the point
grid.

**Normals are computed from the depth, not predicted separately.** They are then guaranteed
consistent with the very points the planes get fitted to; a separately predicted normal field
can disagree with its own depth map and quietly poison a RANSAC that trusts it. It also means
every tier gets normals the same way, LiDAR included.

**Depth is treated as z along the optical axis, not ray length.** Confusing the two stretches
the scene radially — the image centre stays correct and the corners push outward — so it
survives casual inspection. Verified exact against synthetic tilted planes: recovered tilt
matches truth to 0.00° across 0–45°, lifted points planar to 1e-14 m.

Normals straddling a depth discontinuity are discarded; at a doorway edge the cross product
connects two unrelated surfaces.

### 3. Plane fitting

Sequential RANSAC, written directly in numpy.

**Why not Open3D:** it publishes no wheels for the Python here, and a RANSAC plane fit is
fifty lines. The dependency would buy nothing and cost an explanation at the defense.

**One-point hypotheses when normals exist.** A point plus its normal already defines a plane,
so the minimal sample drops from 3 to 1. That raises the probability a hypothesis is
outlier-free from p³ to p, and it lets inliers be rejected on *orientation* as well as
distance — a point lying on the wall plane but belonging to a table edge is excluded because
its surface faces the wrong way.

**Merging coplanar planes** (`merge_coplanar`) was added after the LiDAR run. Sequential
extraction removes each plane's inliers, so one real wall routinely emerges as two or three:
the first fit claims the well-observed middle, and the fringes get picked up later. Eleven
planes appeared for a four-wall room, three sharing a normal. Merging is by normal agreement
**and** mutual offset — never normal alone, or opposite walls of a room would merge — and the
survivors are refitted over their combined inliers. 8 walls → 5.

### 4. Gravity

A Manhattan-frame vote: every plane normal is a candidate up-axis, scored by the inlier mass
of all planes either parallel or perpendicular to it.

**The vote alone is not enough, and this was a measured failure, not a hypothetical.** In a
single photo of a room the largest plane is usually a wall, so the unconstrained vote returned
normals like `[0.73, 0.07, −0.68]`, then classified two walls as floor and ceiling and
reported ceiling heights of 2–22 cm. The vote is a good tiebreaker and a bad primary.

So a **prior** is applied: the protocol asks the operator to hold the phone level, which puts
world-up within a cone of the camera's −y axis, and unlike scene content that prior does not
depend on which wall happens to dominate the frame. Candidates outside the cone are rejected;
if none survive, the prior itself is returned rather than a confidently wrong answer. Gravity
now lands 2–8° off vertical on every real photo frame.

For LiDAR the prior is ARKit's Y-up, confirmed empirically (trajectory spans 2.3 m and 4.8 m
horizontally, 0.26 m vertically).

### 5. Joint floor/ceiling selection

The largest single accuracy win in the project, and the subject of the fix loop.

**Was:** the two largest horizontal planes. **Failure:** in a frame looking through an open
doorway those can be a near floor and a *door head*, so their separation gets reported as a
ceiling height — 0.44 m, 0.60 m, 0.97 m on three of 28 real frames.

**A separation range alone would not fix it.** Two large horizontals sitting 2–4 m apart — a
table top and the ceiling, a bed and the ceiling — pass a range filter and are still wrong. A
range test *constrains* the answer without *identifying* it.

**Shipped:** score every candidate pair on six signals and take the argmax —
`support × parallelism × horizontality × separation × bounding × camera`. Multiplicative, not
additive, because these are conditions that must hold *together* and a weighted sum lets one
very large plane outvote a hard geometric contradiction.

**Measured contribution** (`benchmark/results/fix_loop_photo.md`): scoring every pair
uniformly reproduces the baseline *exactly*, so the exhaustive search buys nothing on its own
— the improvement is the scoring. `separation` is the load-bearing term (−2.1%, 1
catastrophic frame alone). **`bounding` is not**, contrary to what the design rationale
originally claimed. Leave-one-out is degenerate because the survivors agree on the same
argmax, so the other terms are retained for robustness on captures unlike this one, and that
is now stated as the reason rather than implied to be demonstrated.

**Abstention.** Below a selection score the pipeline returns no ceiling and says why. The
brief penalises confident garbage on thin input; a frame that cannot see a room is not a
measurement of one, and 0.435 m dressed as a measurement is worse than silence.

### 6. Manhattan regularisation — all or nothing

Planes are snapped to vertical/horizontal and a shared right-angle grid **only if the whole
room qualifies**.

**Judging it per wall is wrong, and measurably so.** The reference frame is fitted as a
compromise across the walls, so in a room skewed by θ each wall sits only θ/2 from that
compromise. Judged individually, both ends of a 6.8° skew looked "within 5° of square", both
snapped, and a room whose real corners were 83° and 97° was reported as a perfect rectangle.
Orthogonality is a property of the room, not of any single wall — so if any wall's residual
exceeds tolerance, nothing snaps. That case is now a regression test.

This matters beyond tidiness: most rooms *are* boxes, so hard Manhattan forcing would improve
almost every number in our own benchmark and then produce a confidently wrong plan for the
first bay window Cozmo walks us into.

### 7. Which planes are walls

A wall must have **nothing behind it** and **run floor to ceiling**.

Fitting large vertical planes is not enough: a wardrobe front, a bookcase back and a
floor-length curtain are all large, vertical and planar, and RANSAC likes them. Accepting one
measured **14% wall-length error even at LiDAR-grade noise** — the largest single error source
found anywhere in the geometry stage, dwarfing depth quality.

The "nothing behind it" test is decisive and needs no per-scene tuning: a room-bounding plane
has the whole cloud on one side, while a wardrobe 60 cm off the wall has the real wall, and
slices of floor and ceiling, behind it. Its tolerance band is derived from the cloud's own
residual scatter — a fixed 5 cm band silently assumes LiDAR-grade depth and rejects every
genuine wall at photo-tier noise, because 16% of a wall's own points fall more than 5 cm
behind it when σ = 5 cm.

### 8. Dimensions: wall pairs, and why not only the polygon

A closed polygon is the richer output but the fragile one — it needs every bounding wall
found, ordered and successfully intersected, and returns *nothing* when a capture spills into
an adjoining space (which is exactly what happens on both real fused captures).

**Wall-pair separation** needs none of that: it is the distance between two parallel planes,
each fitted to tens of thousands of points, available whenever both walls were seen. That
makes it the most reliable metric measurement in the pipeline and a cross-check on any polygon
we do produce. On the LiDAR room: **3.603 m against 3.54 m tape, +1.8%**.

(First version required *antiparallel* normals and found zero pairs on data with two obvious
ones — SVD gives normals arbitrary sign, so the test had to be sign-free with separation
distinguishing a genuine pair from a merged one.)

### 9. Openings as holes, not boxes

An opening is not primarily a visual category, it is a **hole**. Where a wall exists the
sensor returns points on it; where a door is, it returns points far behind or nothing at all.
So the wall plane's own support is rasterised into wall-local coordinates and connected empty
regions bounded by wall on all sides are the openings.

**Why not lift a 2D segmentation box:** the gate is 2 cm, and a box edge sits exactly where
depth is least reliable — discontinuities are where a depth model is worst. A hole boundary is
measured in the plane's own metric coordinates against a plane fitted to tens of thousands of
points. It is also the same "geometry not AI" logic applied consistently.

Holes touching the raster edge are rejected: that is the end of the observed wall, not a hole
in it. Without that, every wall reports phantom openings at both ends — and the brief scores a
phantom exactly as harshly as a miss.

First run against tape: **door height 2.15 m vs 2.11 m (+1.9%)**, sill 0.03 m so classified a
door; a window on a 0.95 m sill. One false positive.

**Known limitation:** a closed door flush with the wall returns depth like the wall and
produces no hole. This finds openings, not door panels — which is why the protocol asks for
interior doors to be left open.

### 10. Multi-view fusion

Posed frames are lifted into a common world frame, voxel-averaged, then fitted once.

**Voxel averaging** bounds memory and averages the range noise of every frame that saw a
voxel. **Sparse-voxel removal** is the part that matters: real surfaces are observed from many
frames, while flying pixels at depth edges and stray returns off glass are seen once. That is
a multi-view outlier test no single frame can perform, and it is the main reason fusion
improves plane fitting rather than merely enlarging the cloud. 1.53 M raw points → 304 k.

**What fusion does and does not fix, stated because it is easy to overclaim:** it reduces the
random per-frame component as 1/√N and it is the only route to a room polygon or a stitched
plan. It does **not** remove a bias shared by every frame of a scene — if depth is 8% long
everywhere in one room, the average of forty such frames is still 8% long. Measured on the
photo tier: within-room SD 4–6%, between-room bias 20–60%.

**And on the real LiDAR room it did not win outright**: single-frame 2.646 ± 0.108 m (+1.8%),
fused 2.700 m (+3.8%). Fusion collapsed the spread but moved the mean further from truth.
Reported rather than buried.

### 11. Video poses: RGB-D odometry, not SfM

We already run metric depth per keyframe, so every matched feature's 3D position is known in
its own camera frame. That turns pose recovery into **PnP** — a mature, well-conditioned
OpenCV solver — instead of a structure-from-motion problem that would recover the trajectory
only up to scale, requiring the depth model to be bolted back on afterwards to fix it. This
way the trajectory is metric from the first frame.

**The tradeoff, stated because it decides where the error comes from:** the poses inherit the
depth model's error. If depth is 5% long in a room, the baselines through it are 5% long too.
Frame-to-frame chaining also drifts, with no loop closure yet.

**A failed link leaves every later frame unposed** rather than inserting an identity pose. An
identity pose at a break would stack two parts of the flat on top of each other and still look
like a reconstruction.

Two bugs found on the real clip: blur rejection using an absolute threshold borrowed from
24 MP stills rejected **75 of 92 frames** (video is inherently softer — rolling shutter,
inter-frame compression), now judged relative to each clip's own sharpness distribution; and
frames fetched by `CAP_PROP_POS_FRAMES` seek forced a decode from the nearest keyframe every
time, 44 s for 92 grabs, now read sequentially.

### 12. Reproducibility

Depth inference is cached by a **content hash** of (image bytes, intrinsics, backend,
working resolution) — the file's bytes, not its path, so a capture copied elsewhere hits the
same entry and an edited file misses. `--no-cache` runs the live path, which is what the brief
requires of a cache: deterministic replay *and* a working live path.

Cached as **float32, not float16**. float16 resolves ~2 mm at 3 m, which looked far finer than
anything worth chasing — but it measurably moved the benchmark, shifting the baseline selector
−12.2% → −15.0% between a live run and its own cached replay. Nothing changed but 2 mm of
quantisation, and an argmax over plane sizes flipped. Worth knowing about the baseline; not
acceptable in a measurement harness.

The legacy selector is kept as `selector="largest"` so the fix loop's "before" is a real run
that regenerates from raw inputs, not a number remembered in a report.

---

## Reproduce

Clean environment:

```bash
pip install -r requirements.txt
```

Run each tier — same contract from all three:

```bash
python run.py benchmark/raw/photo                --tier photo --out out_photo
python run.py benchmark/raw/video/IMG_0460.MOV   --tier video --out out_video --max-frames 24
python run.py benchmark/raw/lidar/*.r3d          --tier lidar --out out_lidar
```

Benchmarks and reports:

```bash
python benchmark/scripts/report.py --run              # everything, then tabulate
python benchmark/scripts/fix_loop_photo.py            # before/after + signal ablation
python benchmark/scripts/fix_loop_photo.py --no-cache # same, live depth, no cache
python benchmark/scripts/lidar_fusion.py benchmark/raw/lidar/*.r3d
python benchmark/scripts/inspect_r3d.py               # what a capture actually contains
python benchmark/scripts/depth_diagnostic.py benchmark/raw/photo
```

Tests:

```bash
python tests/test_smoke.py          # all three tiers, interface invariants
python tests/test_geometry.py       # geometry against synthetic rooms with known answers
python tests/test_photo_loader.py   # EXIF, intrinsics, orientation
```

Depth is cached by content hash under `benchmark/cache/` (gitignored, safe to delete).
`--no-cache` forces the live path.

---

## What was tested, and what that means

- **Geometry against synthetic rooms with exactly known dimensions.** Exact on clean input;
  0.011% wall error at 5 cm per-point noise; robust to furniture, outliers and 23° rotation;
  correctly reports a 6.8° skewed room as skewed rather than forcing it square.
- **Loader invariants on real files.** EXIF orientation, intrinsics derivation (verified FOV
  79.52° against a 79.52° reference), K scaling with resize, `.r3d` pose validity (rotation
  matrices orthonormal, det = 1.000000).
- **The fix loop, regenerably**, with a leave-one-out and one-signal-at-a-time ablation.

Not tested: multi-room stitching, damage detection, rendered plans — none are built.

---

## Blocked on missing capture data

1. **Same rooms at all three tiers.** The `.r3d` is a different property from the photos and
   video, so no cross-tier comparison on identical rooms is possible. This is a benchmark
   requirement, not a nicety.
2. **Repeatability gate.** Needs one room captured *twice*. The report currently shows
   frame-to-frame spread and says explicitly that this is not the same thing.
3. **Wall/opening ground truth for the 3BHK.** Only ceiling height (2.64 m) is measured.
   Wall lengths, door and window widths for the photographed rooms are unmeasured, so the
   ±8% wall gate cannot be scored at all on the photo tier.
4. **Head-to-head vs Polycam/magicplan.** Not captured.
5. **Which dimension "Window 1.17m" refers to** in the `.r3d` room's measurements — width or
   height. Currently unresolvable, so that comparison is left blank.

---

## Known failure modes

**The floor plane lands on the bed when no real floor is visible.** Found with the diagnostic
overlays (`--debug`), invisible in every table before that. `Room 2 / IMG_0447` reports 2.288 m
against 2.64 m - 35 cm low, a bed height - with the floor plane painted over the whole bed.
The `bounding` signal cannot reject it because the true floor was never observed, so the bed
genuinely *is* the lowest surface in that cloud.

Consequence: **the photo tier's +1.8% mean is two errors cancelling**, not accuracy.
Bedrooms average -2.2% with SD 11.2% and are bimodal (-13% to -19% where the bed is taken as
floor, +10% to +19% where real floor shows); hallway and kitchen average +8.7% with SD 4.5%
from depth scale alone, with visibly correct geometry. Full write-up in
`benchmark/results/debug_findings.md`, including the retraction of an earlier claim that
furniture was *not* the cause.

**Video odometry breaks and does not recover.** On the real clip it failed at link 15 (7
inliers) and every later keyframe stayed unposed — 15 of 30 posed. There is no relocalisation
or loop closure. A break is reported rather than filled with an identity pose, which would
stack two parts of the flat on top of each other and still look like a reconstruction.

**Room polygon fails on captures that spill into the next space.** `select_room_walls`
requires a wall to have nothing behind it; in a cloud spanning a doorway no wall qualifies, so
the polygon returns `None`. The wall-pair measurement was added precisely because it does not
need a closed polygon. Both LiDAR and video currently return no polygon.

**Fusion did not improve ceiling height on the LiDAR room.** Single-frame gave 2.646 ± 0.108 m
(+1.8%), fused gave 2.700 m (+3.8%). Fusion collapsed the spread and is the only route to a
polygon, but it moved the mean further from truth, and the fused ceiling plane carries 7.5 cm
of spread. Two candidate causes, not yet separated: pose drift over 74 s and 16.6 m of
walking, and a cloud spanning 6.1 m in Z for a room measured at 3.77 m.

**Closed doors are invisible to the opening detector.** A door flush with the wall returns
depth like the wall does. This finds openings, not door panels.

**Video intrinsics are assumed.** iPhone video is cropped for stabilisation relative to
stills, so the 26 mm-equivalent focal length is approximate. Labelled `assumed_26mm_equiv_video`
in every result. This is the largest unquantified error in that tier.

**Photo tier per-room spread is large** — 10.9 cm to 37.1 cm ceiling SD. Against a 1.5 cm gate
that is an order of magnitude out.

**CPU only.** ~13 s/frame for depth. Fine for benchmarking, far too slow for the live
walk-in test.

---

## Recommended next three tasks

1. **Get wall and opening ground truth for the 3BHK**, and capture one room twice. Without
   these, the ±8% wall gate and the repeatability gate cannot be scored at all — and they are
   worth more than any further accuracy work on ceiling height. This is the cheapest large
   gain available and it needs a tape, not code.

2. **Install the CUDA torch build.** ~13 s/frame becomes ~1 s. That turns a 10-minute photo
   run into under a minute, which changes how many experiments are affordable per hour and is
   a hard requirement for the live defense. Everything else in this list gets faster too.

3. **Make the room polygon work on captures that spill into adjoining space.** Segment the
   fused cloud by connected floor region before fitting walls, so each room is measured within
   its own footprint. This unblocks polygons, floor area, and multi-room stitching — which is
   the single largest remaining gap in the output contract.

Deliberately *not* recommending more depth-model work. The error budget says per-surface bias
dominates, and the ablation says the geometry stage is already accurate on clean input; the
remaining wins are in data and in coverage, not in a better monocular estimator.
