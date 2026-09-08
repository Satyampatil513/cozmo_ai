# Device matrix

Which tier runs on which hardware, and what each tier honestly delivers.

Accuracy columns are filled from our own benchmark residuals, not from expectation. **Blank
means unmeasured, not assumed** — a number in this table is a claim we have to defend
against a laser measurer in the walk-in test, so nothing goes in until it is measured.

## Tier support by device class

| Tier | Minimum device | Sensors used | Scale source | Status |
|---|---|---|---|---|
| Photo | iPhone 15 or newer, any variant | Rear wide camera | Metric depth + floor plane | supported |
| Video | iPhone 15 or newer, any variant | Rear wide camera | Metric depth + floor plane | supported |
| LiDAR | iPhone 15 **Pro / Pro Max** or newer Pro | LiDAR depth, ARKit 6-DoF pose, intrinsics | Sensor-metric | supported |

The Pro/non-Pro split is a hardware fact, not a policy choice: Apple ships the LiDAR scanner
only on Pro-class iPhones (12 Pro onward) and iPad Pro (2020 onward). A base iPhone 17 has no
depth sensor on the rear array usable for this.

**Requesting the LiDAR tier on a device without LiDAR must fail loudly**, naming the device
and the missing sensor. Silently degrading to the video tier would report video-tier accuracy
under a LiDAR-tier label, which is the exact failure the calibration scoring exists to catch.

## Devices used for our benchmark

| Role | Device | iOS | Tiers captured | Notes |
|---|---|---|---|---|
| Primary | iPhone 17 (base) | | photo, video | Own device. No LiDAR — this is why the LiDAR tier is captured on a second device. |
| LiDAR | *(to confirm: Pro model + iOS)* | | lidar | Borrowed. Model must be confirmed via Settings → General → About before the shoot. |

All three tiers are captured on **the same physical rooms** of one 3BHK. Capturing the LiDAR
tier on a different property would satisfy no gate in the brief: every tier comparison and
every repeatability number is defined against the same rooms and the same tape measurements.

## Accuracy delivered, by tier

Filled from `benchmark/results/` (`benchmark_report.md`, `gates.md`, `calibration.md`). Every
figure is measured against the laser survey (photo) or the `.r3d` room's own tape (LiDAR); a
blank is still unmeasured. Gate column is the brief's threshold, not ours.

| Tier | Wall length | Ceiling height | Opening width | Footprint | Verdict vs gate |
|---|---|---|---|---|---|
| Photo | −30% bias, worst room −38% | +4.8% bias (11–24 cm/room) | not detected on most rooms | +33% area, the one room that closed a polygon | FAIL — wall, ceiling, footprint all outside gate |
| Video | no ground truth (walkthrough spans rooms) | +7.2% (2.83 m vs 2.64 m tape) | — | — | FAIL ceiling; repeatability FAIL (18.8 cm between two walkthroughs) |
| LiDAR | wall-pair +1.8% (3.60 m vs 3.54 m) | +3.8% (2.70 m vs 2.6 m tape) | door height +1.9%, 1 false positive | — | wall-pair within ±8%; ceiling FAIL ≤1.5 cm; openings not clean |

Intervals are calibrated per tier (`benchmark/scripts/calibrate.py`): at the photo tier a
nominal 95% interval empirically contains the truth ~50% of the time — the residual is a
depth-scale bias a symmetric band cannot chase, not an interval-width problem.

## Open questions to close before the walk-in test

- Exact model and iOS version of the borrowed LiDAR device.
- Whether Record3D's free tier permits `.r3d` export at the clip lengths needed, or whether
  the fallback (3D Scanner App → Export → All Data) is used. Test in the probe session.
- Whether the photo tier degrades measurably between 8, 6 and the 2-still minimum.
- A loud-failure guard + test for the LiDAR tier requested on a non-Pro device.
