# Reproduction and input data

The Drive ZIP is input data only. It contains `benchmark/raw/` (photos, video, and the
benchmark `.r3d` scan) plus `benchmark/ground_truth/` (the laser survey and mappings). It
contains no source code, documentation, generated outputs, model weights, or depth cache.
The source code and reproduction commands below come from the GitHub repository.

The brief allows cached model outputs "when the cache replays deterministically and the live
path also runs." Both hold here: depth inference is content-hash cached, and every command
below takes `--no-cache` to force the live path. The walk-in test runs live; so does this
bundle if you pass that flag.

---

## 1. What the bundle is

| Part | Where | In git? |
|---|---|---|
| Code, schema, harness | this repo | yes |
| Raw captures (photos, video, `.r3d`) | `benchmark/raw/` in the Drive ZIP | **no** — lived-in home, kept out of a public repo (`.gitignore`) |
| Laser survey and mappings | `benchmark/ground_truth/` in the Drive ZIP | **no** — supplied input data |
| Depth model weights | fetched by script into `~/.cache` | no — `scripts/fetch_weights.py` |

The repo alone runs the test suite and re-scores the gates from committed ground truth. The
raw tree is required to regenerate the pipeline's own measurements.

### Unpacking the input ZIP

Clone the repository, then extract the Drive ZIP at the repository root, preserving the
`benchmark/raw/` and `benchmark/ground_truth/` paths exactly:

```bash
git clone <repo> && cd cozmo
# Extract cozmo_input_data.zip here; it must create benchmark/raw/ and benchmark/ground_truth/
```

The photo loader keys on one folder per room:

```
benchmark/raw/
  photo/
    Hallway/   6 .jpeg          Kitchen/  4 .jpeg
    Room 1/    6 .jpeg          Room 2/   6 .jpeg          Room 3/  6 .jpeg
  video/
    IMG_0460.MOV                first walkthrough — the reported video number
    IMG_0462.MOV                second walkthrough (longer) — the repeatability check (§4)
  lidar/
    2026-09-08--00-36-25.r3d    Record3D capture, different property (see §6)
    measurements.md             that property's own tape figures
```

Each `benchmark/raw/*/README.md` is committed, so the expected layout is visible even before
the media arrives.

---

## 2. Fresh machine setup

Tested on Python 3.14, CPU-only. No GPU required to reproduce; see §7 for the speed cost.

```bash
git clone <repo> && cd cozmo
python -m venv .venv
. .venv/bin/activate                      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install matplotlib                     # only for benchmark/scripts/damage_room1.py
python scripts/fetch_weights.py           # ~few hundred MB, one time, needs network
```

`fetch_weights.py` pulls the default depth backend (`metric3d_v2`) into `~/.cache/torch/hub`
and best-effort pulls the rejected alternate. After it succeeds, nothing below needs network.

---

## 3. Regenerate everything

```bash
python benchmark/scripts/report.py --run      # runs all 3 tiers, writes benchmark_report.md
python benchmark/scripts/gates.py             # scores the brief's gates, writes gates.md
python benchmark/scripts/fix_loop_photo.py    # fix-loop before/after, writes fix_loop_photo.md
python benchmark/scripts/ablation.py          # drift correction on/off, writes drift_ablation.md
python benchmark/scripts/calibrate.py         # interval coverage + refit, writes calibration.md
python benchmark/scripts/damage_room1.py      # first-pass damage detector, writes an asset PNG
```

Outputs land in `benchmark/results/` (`.md` + `.json`) and `docs/report_assets/`. All are
regenerable and idempotent. `ablation.py` and `calibrate.py` need no capture data — they run
on synthetic ground truth and on the committed `benchmark/ground_truth/` survey.

Runtime, CPU-only, cold cache: photo ~10 min, video ~6 min, LiDAR ~1 min. Warm cache: seconds.

---

## 4. Number-by-number

Every figure cited in `docs/TECHNICAL_REPORT.md` and `OVERNIGHT_PROGRESS.md`, the command
that produces it, and where it lands.

### Ceiling height

| Reported | Command | Output | Truth source |
|---|---|---|---|
| photo per-room, −13.3% to +11.8% | `report.py --run` | `benchmark/results/benchmark_report.md` | `benchmark/raw/photo/*/README.md` (2.64 m tape) |
| video fused, +7.2% (2.830 m) | `report.py --run` | same | same |
| LiDAR fused, +3.8% (2.700 m) | `report.py --run` | same | `benchmark/raw/lidar/measurements.md` (2.6 m) |
| gate scoring vs 2.74 m laser survey, all rooms FAIL | `gates.py` | `benchmark/results/gates.md` | `benchmark/ground_truth/rooms.csv` |

Note the two ceiling truths: `report.py` reads the per-capture `measurements.md`/`README.md`
(2.64 m early tape, 2.6 m for the `.r3d` room); `gates.py` reads the later full laser survey
(2.74 m) in `benchmark/ground_truth/`. The 10 cm gap is called out in `gates.py`'s docstring —
the survey supersedes the tape.

### Wall dimensions (opposite-pair separation)

| Reported | Command | Output |
|---|---|---|
| LiDAR pair 1, +1.8% (3.603 m vs 3.54 m) | `report.py --run` | `benchmark_report.md` |
| photo wall gate, worst 23.6%–37.6% per room, all FAIL/PARTIAL | `gates.py` | `gates.md` (per-span breakdown included) |
| gated multiview moves Room 3 wall error 37.6% → 10.2% | `gates.py` | `gates.md`, `multiview` column |

### Openings

| Reported | Command | Output |
|---|---|---|
| LiDAR door height +1.9% (2.15 m vs 2.11 m), 1 false positive | `report.py --run` | `benchmark_report.md` |
| opening-width gate scoreable, ≤2 cm on ≥85% | `gates.py` | `gates.md` |

### Fix loop (photo-tier ceiling selection)

| Reported | Command | Output |
|---|---|---|
| before (largest): mean −12.2%, SD 24.1%, 3 catastrophic, 0 abstained | `fix_loop_photo.py` | `benchmark/results/fix_loop_photo.md` |
| after (joint): mean +1.8%, SD 10.6%, 0 catastrophic, 3 abstained | `fix_loop_photo.py` | same |
| `separation` is load-bearing, `bounding` is not (leave-one-out) | `fix_loop_photo.py` | same, ablation table |
| live path matches cached replay (within float32) | `fix_loop_photo.py --no-cache` | same |

### Multi-view registration

| Reported | Command | Output |
|---|---|---|
| worst loop closure 204 cm → 4.3 cm after cycle gating | `python benchmark/scripts/register_photos.py` | `benchmark/results/register_photos.json` |
| 2 of 5 rooms correctly refused | same | same |

### Repeatability (video tier)

| Reported | Command | Output |
|---|---|---|
| two walkthroughs disagree: ceiling 2.830 m vs 3.018 m (18.8 cm, gate 1 cm) → unrepeatable | `python run.py benchmark/raw/video/IMG_0460.MOV --tier video --out out_video`<br>`python run.py benchmark/raw/video/IMG_0462.MOV --tier video --out out_video2` | `out_video/result.json`, `out_video2/result.json` |

### Drift ablation

| Reported | Command | Output |
|---|---|---|
| large injected drift: shared-wall gap 14.1 cm "poses as-is" → 0.0 cm corrected; control row a near no-op | `python benchmark/scripts/ablation.py` | `benchmark/results/drift_ablation.md` |

### Calibration

| Reported | Command | Output |
|---|---|---|
| photo interval coverage ~50% at nominal 95% (bias-dominated); per-tier residuals + refit | `python benchmark/scripts/calibrate.py` | `benchmark/results/calibration.md` |

### Timing

| Reported | Command | Output |
|---|---|---|
| photo 608 s / video 358 s / LiDAR 62 s, CPU-only | `report.py --run` | `benchmark_report.md`, "Processing time" |

### LiDAR floor-plan images

| Reported | Command | Output |
|---|---|---|
| Wall occupancy and camera-path raster | `python run.py <scan-or-directory> --tier lidar --out out_lidar` | `out_lidar/raster.png` |
| Wall-snapped room outlines and unresolved regions | same | `out_lidar/blueprint.png` |
| Report figures from the benchmark and team scans | `python benchmark/scripts/lidar_plan_figures.py` | `docs/report_assets/15_lidar_raster.png`, `docs/report_assets/16_lidar_3d.png`, `docs/report_assets/18_lidar_plan_scan.png` |

The LiDAR run always writes `raster.png` beside `blueprint.png` and `result.json`. The
figures script is a report-only renderer; it does not change the measurement JSON.

### Damage (first pass, out of scope)

| Reported | Command | Output |
|---|---|---|
| synthetic 2-class detection + concealed rules + scope, all pass | `python tests/test_damage.py` | stdout `ALL PASS` |
| real Room 1 detector overlay | `python benchmark/scripts/damage_room1.py` | `docs/report_assets/14_damage_room1.png` |

No damage was staged and damage scoring is out of scope for this submission, so no accuracy
number is claimed — the detector runs on unfitted default thresholds, labelled as such in
every detection's `notes`. Technical report §8.

---

## 5. Determinism and the cache

Depth inference is cached by a content hash of `(image bytes, intrinsics, backend, working
resolution)` under `benchmark/cache/` (git-ignored, safe to delete).

- **Deterministic replay:** same inputs → same cache key → identical depth → identical
  measurement, run to run.
- **Live path:** every command above accepts `--no-cache`. It re-runs inference and must
  produce the same result within float32 precision.
- **Why float32, not float16:** float16 quantisation at ~2 mm moved the baseline selector
  −12.2% → −15.0% between a live run and its own replay (an argmax over plane sizes flipped).
  The cache stores float32 for this reason. Detail in `OVERNIGHT_PROGRESS.md` §12.

To prove the live path from scratch:

```bash
rm -rf benchmark/cache/
python benchmark/scripts/fix_loop_photo.py --no-cache
```

---

## 6. What cannot be reproduced, and why

Stated so nobody rediscovers it mid-review.

| Gap | Consequence |
|---|---|
| The `.r3d` is a **different property** from the photographed/filmed 3BHK (team-waived, `COMPLIANCE_MATRIX.md` §2.15) | Every cross-tier number compares different rooms. Per-tier results against each capture's own truth stand; the tier-to-tier ranking does not. |
| No closed multi-room stitch on any real capture (§4/§7 of the technical report) | The drift on/off footprint ablation has no footprint to run against. |
| Damage and head-to-head are out of scope for this submission | No damage-accuracy or incumbent-comparison numbers to reproduce. |

---

## 7. Environment sensitivities

- **CPU vs CUDA.** All reported timings are CPU-only (~13 s/frame for depth). A CUDA torch
  build cuts that to ~1 s/frame and does not change any measurement — depth values are
  identical, only wall-clock differs.
- **Python 3.14.** `requirements.txt` pins are what this was built against
  (`torch>=2.14`, `transformers>=5.0`). Older stacks are untested.
- **HEIC.** iPhones shoot HEIC; `pillow-heif` is required or the photo loader refuses the
  files rather than guessing.
- **Weights offline.** After `fetch_weights.py`, set `HF_HUB_OFFLINE=1` /
  `TRANSFORMERS_OFFLINE=1` if you want to hard-prove no network is touched.

---

## 8. Test suite

Runs from the repo alone (no raw tree needed); skips cleanly where data is absent.

```bash
python tests/test_smoke.py           # all 3 tiers, interface invariants
python tests/test_geometry.py        # geometry vs synthetic rooms with known answers
python tests/test_photo_loader.py    # EXIF, intrinsics, K scaling, .r3d pose validity
python tests/test_damage.py          # synthetic 2-class damage, concealed rules, scope
python tests/test_output_schema.py   # every emitted result validates against schemas/output.schema.json
python tests/test_stitching.py       # cross-room registration accepts a real 3D-3D match
```
