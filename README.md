# Cozmo AI case study

Handheld consumer capture to a dimensioned, stitched, damage-annotated property plan.
Three input tiers (photo, video, LiDAR), one output contract, calibrated intervals throughout.

## Status

Early. See `docs/COMPLIANCE_MATRIX.md` for what exists and what does not. Every row marked
NOT BUILT is genuinely not built; nothing here is stubbed to look finished.

## Setup, fresh machine

```
git clone <repo>
cd cozmo-ai
python -m venv .venv && . .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/fetch_weights.py                     # pre-fetch depth model weights
```

## Run one capture

Same command and same output contract for every tier; the tier is auto-detected when omitted.

```
python run.py benchmark/raw/photo              --tier photo --out out_photo
python run.py benchmark/raw/video/clip.MOV     --tier video --out out_video
python run.py benchmark/raw/lidar/scan.r3d     --tier lidar --out out_lidar
```

Writes `out/result.json` against `schemas/output.schema.json`. Rendered plans are NOT BUILT.

The tiers differ only in their loader. Everything from `pipeline/measure.py` down is shared,
and it dispatches on whether frames carry poses rather than on the tier name - so a video
whose odometry failed degrades to the per-frame path instead of pretending it has a
trajectory.

Benchmarks and reports:

```
python benchmark/scripts/report.py --run          # every tier, then one table
python benchmark/scripts/fix_loop_photo.py        # before/after + signal ablation
python benchmark/scripts/inspect_r3d.py           # what a .r3d actually contains
python tests/test_smoke.py                        # all three tiers, interface invariants
```

Depth inference is cached by content hash under `benchmark/cache/` (gitignored). Pass
`--no-cache` to force the live path.

See `OVERNIGHT_PROGRESS.md` for current measured accuracy, known failure modes and what is
blocked on missing capture data.

## Capture

Route 2, stock capture: native iOS Camera for the photo and video tiers, Record3D for the
LiDAR tier. No custom app to install.

- `docs/CAPTURE_PROTOCOL.md` - the one page an operator follows literally
- `docs/DEVICE_MATRIX.md` - which tier runs on which hardware, and what it delivers
- `docs/capture/` - the operator-facing shot lists we used for our own benchmark

**Nothing to print, nothing to place in the room.** Photo and video are scale-ambiguous -
a room and a scale model of it produce identical pixels - so metres come from a metric depth
model fused with the floor plane and one stated number, the operator's camera height. We
deliberately require no fiducial: the benchmark has to be captured the same way Cozmo will
capture at the walk-in test, or the benchmark predicts nothing.

LiDAR requires a Pro-class iPhone. On a non-Pro device that tier fails loudly rather than
falling back to the video path - reporting video-tier accuracy under a LiDAR-tier label
would be worse than refusing.

## Status

Working: all three tier loaders, multi-view fusion for posed captures, plane geometry,
ceiling height, wall-pair dimensions, a first-pass opening detector, and a regenerable fix
loop. NOT BUILT: multi-room stitching, damage detection, rendered plans. Accuracy today is
+3.8% ceiling on LiDAR, +7.2% on video, and -13% to +12% per room on photo - see
`docs/COMPLIANCE_MATRIX.md` for the row-by-row state.

## Design in one paragraph

Every tier resolves to the same `Scene`: a list of frames, each with optional depth and
optional pose, plus an explicit scale source. LiDAR arrives with depth, poses and metres.
Video and photos arrive with none of those and get geometry from a shared pointmap backbone
and metres from a metric depth model fused with the floor plane. Everything downstream of
`pipeline/capture/` is tier-agnostic and never branches on tier; the tiers differ only in how
wide their intervals end up, and those widths are fitted on our own benchmark residuals rather
than chosen.

## Layout

```
docs/         protocol, device matrix, compliance matrix, reports
schemas/      published output contract
pipeline/     capture -> geometry -> stitching -> damage -> confidence -> output
benchmark/    raw captures, tape ground truth, harness, results
```
