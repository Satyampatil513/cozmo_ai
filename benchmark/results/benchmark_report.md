# Benchmark report

Regenerate: `python benchmark/scripts/report.py --run`

Ground truth is tape. A metric with no ground truth reads `-` and is never
filled with an estimate. A tier that has not been run says so.

## Ceiling height

| Tier | Room | Measured | Truth | Error | Mode | Frames | Abstained |
|---|---|---|---|---|---|---|---|
| photo | Hallway | 2.853m | 2.64 | +8.1% | per_frame | 6 | 0 |
| photo | Kitchen | 2.952m | 2.64 | +11.8% | per_frame | 4 | 1 |
| photo | Room 1 | 2.628m | 2.64 | -0.4% | per_frame | 6 | 0 |
| photo | Room 2 | 2.288m | 2.64 | -13.3% | per_frame | 6 | 1 |
| photo | Room 3 | 2.457m | 2.64 | -6.9% | per_frame | 6 | 1 |
| video | IMG_0460 | 2.830m | 2.64 | +7.2% | fused | 24 | 0 |
| lidar | 2026-09-08--00-36-25 | 2.700m | 2.6 | +3.8% | fused | 148 | 0 |

## Wall dimensions (opposite-pair separation)

| Tier | Room | Pair | Separation | Nearest truth | Error |
|---|---|---|---|---|---|
| video | IMG_0460 | 1 | 3.717m | - | - |
| video | IMG_0460 | 2 | 3.565m | - | - |
| lidar | 2026-09-08--00-36-25 | 1 | 3.603m | 3.54 | +1.8% |
| lidar | 2026-09-08--00-36-25 | 2 | 3.111m | 3.54 | -12.1% |
| lidar | 2026-09-08--00-36-25 | 3 | 2.887m | 3.54 | -18.4% |

## Openings

| Tier | Room | Kind | Width | Height | Sill | Confidence |
|---|---|---|---|---|---|---|
| lidar | 2026-09-08--00-36-25 | door | 0.90m | 2.15m | 0.03m | 0.85 |
| lidar | 2026-09-08--00-36-25 | door | 1.30m | 0.85m | 0.03m | 0.64 |
| lidar | 2026-09-08--00-36-25 | window | 0.90m | 1.10m | 0.95m | 0.61 |

## Repeatability

Within-room spread across frames, for tiers measuring per frame. This is the
closest thing we have to the brief's repeatability gate until a second capture
of the same room exists - it measures frame-to-frame agreement, NOT the
capture-to-capture agreement the gate actually asks for.

| Tier | Room | Frames reported | Ceiling SD |
|---|---|---|---|
| photo | Hallway | 6 | 12.4 cm |
| photo | Kitchen | 3 | 10.9 cm |
| photo | Room 1 | 6 | 15.9 cm |
| photo | Room 2 | 5 | 27.8 cm |
| photo | Room 3 | 5 | 37.1 cm |

## Processing time

| Tier | Seconds | Notes |
|---|---|---|
| photo | 608.4 | CPU depth inference dominates |
| video | 358.2 | CPU depth inference dominates |
| lidar | 61.9 | no depth inference; sensor supplies it |

All timings are CPU-only (no CUDA build installed). The depth model is
~13 s/frame on CPU, which is the bulk of the photo and video numbers.

