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
| 1.2a | LiDAR tier fails loudly on non-Pro device | pipeline/capture/lidar.py:199 | structural guard | PARTIAL - a capture with no depth/intrinsics (what a non-Pro export is) raises `ValueError` rather than degrading to the video path. No dedicated device-name check or test asserting the failure message |
| 1.3 | Photo tier, 2 to 8 stills, no depth, no poses | pipeline/capture/photo.py | loader | DONE |
| 1.3a | Monocular metric depth | pipeline/capture/depth.py | Metric3D v2 backend + registry | PARTIAL - runs; backend swap took mean ceiling error +26.1% -> -12.2%, see benchmark/results/depth_backend_comparison.md |
| 1.3b | Depth to oriented point cloud | pipeline/geometry/lift.py | lift() | DONE |
| 1.3c | Fusion of the two, sigma from their disagreement | pipeline/capture/scale.py | fused Scale | NOT BUILT |
| 1.4 | Video tier | pipeline/capture/video.py | loader + RGB-D odometry | DONE - 15/30 frames posed on real clip |
| 1.5 | LiDAR tier, depth + poses + intrinsics | pipeline/capture/lidar.py | .r3d + Stray loader | DONE |
| 1.6 | Photo multi-view registration | pipeline/capture/multiview.py | cycle-gated pose graph | DONE - 3 modes runnable; see benchmark/results/multiview_findings.md |
| 1.6a | Pose verification, not just reporting | pipeline/capture/multiview.py | triangle + fundamental-cycle gate | DONE - worst loop closure 204 cm -> 4.3 cm; 2 of 5 rooms correctly refused |

## Part 2: output contract

| # | Requirement | Path | Artifact | Status |
|---|---|---|---|---|
| 2.1 | Walls | pipeline/geometry/walls.py, pipeline/geometry/floorplan.py | room polygon + wall-pair spans | PARTIAL - wall-pair spans +1.8% vs tape (LiDAR). LiDAR tier now produces per-room wall polygons from a wall-line arrangement (floorplan.py, report §9): benchmark .r3d closes as one room, ceiling -1.5% vs tape; a multi-room scan resolves 4 rooms + 1 flagged unresolved region. Room dims unscored (no floor-plan ground truth). Photo/video polygon still rare |
| 2.2 | Ceiling height | pipeline/geometry/planes.py | measurement + abstention | PARTIAL - LiDAR +3.8%, video +7.2%, photo +1.9% to +11.9% by room. Abstains rather than guessing. Gate is <=1.5 cm; we are an order of magnitude out |
| 2.3 | Floor area | pipeline/geometry/walls.py | measurement | PARTIAL - 12.535 m2 on photo Room 2, the only real capture that closed a polygon. Unscored: no ground truth |
| 2.4 | Openings | pipeline/geometry/openings.py | list | PARTIAL - door height +1.9% vs tape, 1 false positive |
| 2.5 | Stitched plan with adjacency | pipeline/geometry/floorplan.py (LiDAR), pipeline/stitching/stitch.py (photo/video) | property plan | PARTIAL - LiDAR: a real multi-room scan produces a placed, connected, dimensioned plan from the wall-line arrangement (report §9); the corridor is absorbed into a neighbour rather than named, and a large open area stays one flagged block. Photo/video stitching is built and wired but no real capture has closed a multi-room stitch (cross-room evidence correctly rejected by the gates - §4/§7) |
| 2.6 | Damage regions, class + metric extent | pipeline/damage/detect.py, pipeline/measure.py | list in result.json | BUILT, wired on the per-frame path; emitted in the schema output (tests/test_output_schema.py). First pass, unfitted thresholds - OUT OF SCOPE for scoring (no staged damage). Technical report §8 |
| 2.7 | Concealed-damage flags with rule fired | pipeline/damage/rules.py | concealed_flag per region | BUILT, emitted with each damage region; synthetic-tested |
| 2.8 | Scope line items keyed to surfaces | pipeline/damage/scope.py | scope_items[] | BUILT, emitted; quantity inherited from the damage extent, never re-estimated |
| 2.9 | Confidence interval on every measurement | pipeline/confidence/intervals.py | Measurement | PARTIAL - every ceiling/wall/area/damage measurement carries a 95% interval; the error model's (rel, abs) per tier are still the placeholder values, with the fitted numbers now produced by benchmark/scripts/calibrate.py (see 2.23) but not yet pasted in |
| 2.10 | One command per capture | run.py | CLI, all 3 tiers | DONE |
| 2.11 | JSON to published schema | pipeline/output/schema_adapter.py + tests/test_output_schema.py | adapter + 13/13 real results validated | PARTIAL - validates cleanly, but `walls[]` (the schema's polygon-based model) has no field for wall-pair separation, our most reliable measurement - stated in the adapter's own docstring, not silently worked around |
| 2.12 | Rendered plan | pipeline/output/render.py, pipeline/geometry/floorplan.py | SVG + PNG | DONE for LiDAR - every LiDAR run writes `blueprint.png`/`.svg` (room polygons, per-room ceiling + area with interval width, connections) and `raster.png` (the wall occupancy grid the plan is built from). Photo single-room has no adjacency to draw |

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
| 2.13 | 3+ rooms plus connector | benchmark/raw/photo | 5 rooms: 3 bedrooms, kitchen, hallway (connector) | DONE |
| 2.14 | Furnished room, staged damage, 2 classes | - | - | OUT OF SCOPE - no damage staged; per-surface damage scoring is not part of this submission. `pipeline/damage/` is a synthetic-validated first pass, wired on the per-frame path and emitted in the schema output (2.6-2.8, technical report §8) |
| 2.15 | Same rooms at all 3 tiers | benchmark/raw/{photo,video,lidar}/ | raw data | WAIVED by the team - see note below |
| 2.16 | One room captured twice, same tier | benchmark/raw/video/ | IMG_0460.MOV, IMG_0462.MOV | DONE - the property walked twice at the video tier |
| 2.17 | Tape/laser ground truth | benchmark/ground_truth/ | laser survey | DONE - 19/20 wall lengths, 10/10 ceilings, 5/5 doors. Ceiling 2.74 m (supersedes an earlier 2.64 m tape figure). Windows discarded by the operator; `room_03_bed_two` w4 not measured |
| 2.18 | Gate: opening widths, detection scored | benchmark/scripts/gates.py | benchmark/results/gates.md | BUILT - scored on doors only (windows discarded) |
| 2.19 | Gate: ceiling height + spread | benchmark/scripts/gates.py | benchmark/results/gates.md | BUILT - every room scored against 2.74 m; all FAIL, root cause is depth-model scale bias (report §5/§6) |
| 2.20 | Gate: repeatability | benchmark/scripts/gates.py | benchmark/results/gates.md | CHECKED - FAIL, unrepeatable. Video tier, two walkthroughs: ceiling height disagrees 18.8 cm (2.830 vs 3.018 m) against a 1 cm gate (report §9) |
| 2.21 | Gate: drift, with on/off ablation | benchmark/scripts/ablation.py | benchmark/results/drift_ablation.md | BUILT - plane-anchored correction, ON vs OFF, on a synthetic two-room flat with injected drift (no real capture has closed a multi-room stitch to ablate, report §4/§7). Large drift: shared-wall gap 14.1 cm with correction OFF ("poses as-is"), 0.0 cm ON |
| 2.22 | Gate: photo-tier whole-property stitch | pipeline/stitching/stitch.py: stitch_photo_property() | real run + edge-level positive control | BUILT, currently REJECTS on our own capture - correctly: every cross-room edge on the real 5-room set fails an existing verification gate (implausible camera height, or depth-scale ratio outside sanity), because none of that overlap was ever deliberately shot. Proven able to accept genuine cross-room evidence separately (test_stitching.py, edge-level, real 3D-3D match). The gates.py report row itself is not yet written |
| 2.23 | Calibration scored at every tier | pipeline/confidence/calibrate.py, benchmark/scripts/calibrate.py | benchmark/results/calibration.md | BUILT - residuals, coverage of the current interval model at nominal 50/80/95%, and a refit per tier. Finding: photo intervals cover 50% at nominal 95% (bias-dominated, not width). Data thin: 5 photo rooms, 1 LiDAR room |

## Waived: same rooms at all three tiers (2.15)

The `.r3d` capture the team shared is a **different property** from the 3BHK we photographed
and filmed. The team has confirmed this constraint is waived, so cross-tier comparison on
identical rooms is out of scope for this submission rather than an outstanding gap.

What that costs, stated so nobody has to rediscover it: every cross-tier number in this repo
compares **different rooms**, so a tier-to-tier accuracy difference cannot be separated from a
room-to-room one. The LiDAR tier's +3.8% ceiling and the photo tier's +8 to +12% are not a
controlled comparison of the tiers - they are two different rooms measured by two different
methods. Per-tier results against each capture's own ground truth remain valid; the ranking
between tiers does not.

## Parts 3 to 5, deliverables

| # | Requirement | Path | Artifact | Status |
|---|---|---|---|---|
| 3.1 | Head-to-head, 2 rooms, LiDAR tier | - | - | OUT OF SCOPE - confirmed with the team; no incumbent-app comparison in this submission |
| 4.1 | Fix declaration, one page | docs/FIX_DECLARATION.md | page + post-mortem | DONE |
| 4.2 | Before run, regenerable | benchmark/scripts/fix_loop_photo.py | selector="largest" | DONE |
| 4.3 | After run, regenerable | benchmark/scripts/fix_loop_photo.py | selector="joint" | DONE |
| 4.4 | Readable diff | benchmark/results/fix_loop_photo.md | before/after + ablation | DONE |
| 5.1 | Commit history | .git | log | IN PROGRESS |
| D.0 | Capture sessions executed | benchmark/raw/ | 29 photos / 1 clip / 1 .r3d | DONE - photo and video shot in the 3BHK; .r3d supplied by the team (different property, waived) |
| D.3 | README, fresh machine to running in 15 min | README.md | doc | PARTIAL |
| C.1 | Weights fetched by script, not committed as binaries | scripts/fetch_weights.py | CLI | DONE |
| D.4 | Reproduction bundle | docs/REPRODUCTION.md, scripts/make_reproduction_bundle.py | doc + zip | DONE - `scripts/make_reproduction_bundle.py` zips benchmark/raw + ground_truth + depth cache + key docs (~1.2 GB, `--lite` drops the video) with its own BUNDLE_README; docs/REPRODUCTION.md is the number-by-number map. Team-supplied Stray scans (report §9) live outside the repo; run.py points at their dirs directly |
| D.7 | Technical report, max 6 pages | docs/TECHNICAL_REPORT.md | doc + 11 stage images | PARTIAL - trimmed to ~2870 words / 11 images (10 sections); a Word/PDF export runs slightly over 6 pages and needs a final image-size / cut pass |
| D.8 | Raw benchmark data | benchmark/raw/ | data | DONE - photo, video and lidar captures committed |
