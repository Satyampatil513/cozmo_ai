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
