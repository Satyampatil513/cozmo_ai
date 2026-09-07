# Compliance matrix

Requirement -> path -> artifact -> status. Status is DONE, PARTIAL, or NOT BUILT.
"NOT BUILT" is an honest answer and still scores on coverage. A silently missing row does not.

## Part 1: capture

Capture route is **Route 2, stock capture**: native iOS Camera for photo and video, Record3D
for LiDAR, driven by a one-page protocol. No custom iOS app, so there is no TestFlight build
to install and install time is whatever the App Store takes.

Hardware is a base iPhone 17 (no LiDAR - photo and video tiers) plus a borrowed Pro-class
device for the LiDAR tier, both capturing the same rooms of one 3BHK.

| # | Requirement | Path | Artifact | Status |
|---|---|---|---|---|
| 1.1 | Capture route declared (Route 2) | docs/CAPTURE_PROTOCOL.md | one-page protocol v0.3 | DONE |
| 1.1a | Capture route requires no props | docs/CAPTURE_PROTOCOL.md v0.4 | protocol | DONE |
| 1.1b | Operator-facing shot lists | docs/capture/ | 3 briefs | DONE |
| 1.1c | Capture validator, pre-reconstruction | scripts/validate_capture.py | CLI + exit code | DONE |
| 1.1d | Photo/video field guide | docs/capture/PHOTO_VIDEO_FIELD_GUIDE.md | procedure | DONE |
| 1.1e | Remote dev capture brief | docs/capture/REMOTE_DEV_CAPTURE.md | brief | DONE |
| 1.2 | Device matrix | docs/DEVICE_MATRIX.md | table | PARTIAL - accuracy columns unmeasured |
| 1.2a | LiDAR tier fails loudly on non-Pro device | pipeline/capture/lidar.py | guard + test | NOT BUILT |
| 1.3 | Photo tier, 2 to 8 stills, no depth, no poses | pipeline/capture/photo.py | loader | DONE |
| 1.3a | Monocular metric depth | pipeline/capture/depth.py | backend + registry | PARTIAL - runs, +26% scale error |
| 1.3b | Depth to oriented point cloud | pipeline/geometry/lift.py | lift() | DONE |
| 1.3c | Fusion of the two, sigma from their disagreement | pipeline/capture/scale.py | fused Scale | NOT BUILT |
| 1.4 | Video tier | pipeline/capture/video.py | loader | NOT BUILT |
| 1.5 | LiDAR tier, depth + poses + intrinsics | pipeline/capture/lidar.py | loader | NOT BUILT |

## Part 2: output contract

| # | Requirement | Path | Artifact | Status |
|---|---|---|---|---|
| 2.1 | Walls | pipeline/geometry/walls.py | room polygon | DONE - untested on real data |
| 2.2 | Ceiling height | pipeline/geometry/planes.py | measurement | PARTIAL - +26% vs tape, see benchmark/results/baseline_ceiling_height.md |
| 2.3 | Floor area | pipeline/geometry/walls.py | measurement | DONE - untested on real data |
| 2.4 | Openings | pipeline/geometry/openings.py | list | NOT BUILT |
| 2.5 | Stitched plan with adjacency | pipeline/stitching/stitch.py | property plan | NOT BUILT |
| 2.6 | Damage regions, class + metric extent | pipeline/damage/detect.py | list | NOT BUILT |
| 2.7 | Concealed-damage flags with rule fired | pipeline/damage/rules.py | list | NOT BUILT |
| 2.8 | Scope line items keyed to surfaces | pipeline/damage/scope.py | list | NOT BUILT |
| 2.9 | Confidence interval on every measurement | pipeline/confidence/intervals.py | Measurement | PARTIAL |
| 2.10 | One command per capture | run.py | CLI | PARTIAL |
| 2.11 | JSON to published schema | schemas/output.schema.json | schema | PARTIAL |
| 2.12 | Rendered plan | pipeline/output/render.py | SVG + PNG | NOT BUILT |

## Open interpretation: which gates loosen at the photo and video tiers

The brief says "Photo-tier and video-tier gates are looser (photo: wall lengths within +/-8%
with calibrated intervals; video: +/-3%)". The parenthetical names **wall lengths only**. It
does not say whether the opening-width gate (<=2 cm on >=85%) and the ceiling gate (<=1.5 cm)
also loosen at those tiers, or hold at the LiDAR value throughout.

It matters: <=2 cm on a 0.9 m door is +/-2.2%, four times tighter than the +/-8% allowed on a
wall in the same room. Reading the opening gate as tier-invariant makes the photo tier
dramatically harder than its own wall gate implies.

We do not resolve this by assuming. The benchmark report states opening and ceiling error at
every tier against the strict thresholds, and says plainly that we read them as
tier-invariant. If the intended reading was looser, we are reporting against a harder bar
than required, which costs nothing. The reverse assumption would have cost the row.

## Part 2: benchmark and gates

| # | Requirement | Path | Artifact | Status |
|---|---|---|---|---|
| 2.13 | 3+ rooms plus connector | benchmark/raw/ | raw data | NOT BUILT |
| 2.14 | Furnished room, staged damage, 2 classes | benchmark/raw/ | raw data | NOT BUILT |
| 2.15 | Same rooms at all 3 tiers | benchmark/raw/{photo,video,lidar}/ | raw data | NOT BUILT - blocked on a LiDAR device in the 3BHK |
| 2.16 | One room captured twice, same tier | benchmark/raw/ | raw data | NOT BUILT |
| 2.17 | Tape/laser ground truth | benchmark/ground_truth/ | 4 CSVs, unfilled | PARTIAL - sheets ready, values pending capture |
| 2.17a | Cross-room spans, for scoring the drift ablation | benchmark/ground_truth/spans.csv | CSV | PARTIAL - sheet ready |
| 2.18 | Gate: opening widths, detection scored | benchmark/scripts/gates.py | report row | NOT BUILT |
| 2.19 | Gate: ceiling height + spread | benchmark/scripts/gates.py | report row | NOT BUILT |
| 2.20 | Gate: repeatability | benchmark/scripts/gates.py | report row | NOT BUILT |
| 2.21 | Gate: drift, with on/off ablation | benchmark/scripts/ablation.py | two footprints | NOT BUILT |
| 2.22 | Gate: photo-tier whole-property stitch | benchmark/scripts/gates.py | report row | NOT BUILT |
| 2.23 | Calibration scored at every tier | pipeline/confidence/calibrate.py | coverage curve | NOT BUILT |

## Parts 3 to 5, deliverables

| # | Requirement | Path | Artifact | Status |
|---|---|---|---|---|
| 3.1 | Head-to-head, 2 rooms, LiDAR tier | benchmark/results/head_to_head.md | table | NOT BUILT |
| 4.1 | Fix declaration, one page | docs/FIX_DECLARATION.md | page | NOT BUILT |
| 4.2 | Before run, regenerable | benchmark/results/before/ | outputs | NOT BUILT |
| 4.3 | After run, regenerable | benchmark/results/after/ | outputs | NOT BUILT |
| 4.4 | Readable diff | benchmark/results/fix_diff.md | diff | NOT BUILT |
| 5.1 | Commit history | .git | log | IN PROGRESS |
| D.0 | Capture sessions executed | benchmark/raw/ | media | NOT BUILT - blocked on the shoot |
| D.3 | README, fresh machine to running in 15 min | README.md | doc | PARTIAL |
| D.4 | Reproduction bundle | docs/REPRODUCTION.md | doc + script | NOT BUILT |
| D.7 | Technical report, max 6 pages | docs/TECHNICAL_REPORT.md | doc | NOT BUILT |
| D.8 | Raw benchmark data | benchmark/raw/ | data | NOT BUILT |
