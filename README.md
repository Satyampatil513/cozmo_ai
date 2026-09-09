# Cozmo AI case study

Handheld consumer capture → a dimensioned, stitched property plan. Three input tiers
(photo, video, LiDAR), one output contract, calibrated intervals throughout.

One rule drives every design choice: **models supply structure, geometry computes
measurements.** A network is only ever asked for depth; every reported dimension is the
distance between planes fitted to tens of thousands of points.

## Setup

Python 3.14, CPU is enough (GPU only makes it faster).

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/fetch_weights.py                    # one-time, ~few hundred MB; then fully offline
```

## Run a capture

One command per capture. Same output contract from every tier. Tier is auto-detected from the
input if `--tier` is omitted.

```bash
python run.py benchmark/raw/photo                     --tier photo --out out_photo
python run.py benchmark/raw/video/IMG_0460.MOV        --tier video --out out_video
python run.py benchmark/raw/lidar/*.r3d               --tier lidar --out out_lidar
```

Writes `<out>/result.json`, validated against `schemas/output.schema.json`, plus a rendered
`blueprint.png`/`.svg`; the LiDAR tier also writes `raster.png` (the wall occupancy grid the
plan is built from). Photo input is one folder per room. `--no-cache` forces live depth
inference (no cached outputs).

The tiers differ only in their loader. Everything from `pipeline/measure.py` down is shared
and branches on whether frames carry poses, never on the tier name — so a video whose
odometry fails degrades to the per-frame path instead of faking a trajectory.

## Reproduce the numbers

```bash
python benchmark/scripts/report.py --run     # all 3 tiers → benchmark/results/benchmark_report.md
python benchmark/scripts/gates.py            # brief's gates vs laser survey → gates.md
python benchmark/scripts/fix_loop_photo.py   # fix-loop before/after + ablation → fix_loop_photo.md
python tests/test_smoke.py                   # all tiers, interface invariants
```

Full number-by-number map and fresh-machine steps: **`docs/REPRODUCTION.md`**.

## Capture route

Route 2, stock capture — native iOS Camera for photo/video, Record3D for LiDAR. No app to
install, **nothing to print or place in the room**. Photo and video are scale-ambiguous, so
metres come from a metric depth model fused with the floor plane and one stated number, the
operator's camera height. The benchmark is captured the same way the walk-in test will be, or
it predicts nothing.

LiDAR needs a Pro-class iPhone; on a non-Pro device that tier fails loudly rather than
silently falling back to video-tier accuracy under a LiDAR label.

- `docs/CAPTURE_PROTOCOL.md` — the one page an operator follows literally
- `docs/DEVICE_MATRIX.md` — which tier runs on which hardware, and what it delivers
- `docs/capture/` — the operator shot lists used for our own benchmark

## Status

Honest and row-by-row in **`docs/COMPLIANCE_MATRIX.md`**. Every `NOT BUILT` is genuinely not
built; nothing is stubbed to look finished.

**Working:** all three tier loaders, multi-view fusion for posed captures, plane geometry,
ceiling height, wall-pair dimensions, opening detection, photo-tier property stitching,
**LiDAR multi-room floor plan from the cloud (wall-line arrangement, §9)**, a regenerable fix
loop, per-tier interval calibration, an on/off drift ablation, first-pass damage detection
emitted in the schema output.
**Not passing:** ceiling and wall gates (depth-model scale bias, identified in the report);
repeatability — checked at the video tier, the two walkthroughs disagree by 18.8 cm.
**Out of scope (team-confirmed):** head-to-head vs an incumbent; damage scoring.
**Partial:** the LiDAR plan does not name the corridor/hall as its own region and leaves a
large open area as one flagged block; video multi-room stitching is synthetic-validated only.

Accuracy today: ceiling +3.8% LiDAR / +7.2% video / −13% to +12% per room photo.

## Documents

| File | What |
|---|---|
| `docs/TECHNICAL_REPORT.md` | architecture, tier design, error budget, fix loop, failure modes |
| `docs/REPRODUCTION.md` | regenerate every number from raw inputs |
| `docs/COMPLIANCE_MATRIX.md` | requirement → path → artifact → status |
| `OVERNIGHT_PROGRESS.md` | measured accuracy, known failure modes, what's blocked on data |

## Layout

```
docs/        protocol, device matrix, compliance matrix, reports
schemas/     published output contract
pipeline/    capture → geometry → stitching → damage → confidence → output
benchmark/   raw captures, laser ground truth, harness, results
```
