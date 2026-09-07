# Device matrix

Which tier runs on which hardware, and what each tier honestly delivers.

Accuracy columns are filled from our own benchmark residuals, not from expectation. **Blank
means unmeasured, not assumed** — a number in this table is a claim we have to defend
against a laser measurer in the walk-in test, so nothing goes in until it is measured.

## Tier support by device class

| Tier | Minimum device | Sensors used | Scale source | Status |
|---|---|---|---|---|
| Photo | iPhone 15 or newer, any variant | Rear wide camera | Printed ArUco scale card, 149.86 mm | supported |
| Video | iPhone 15 or newer, any variant | Rear wide camera | Printed ArUco scale card, 149.86 mm | supported |
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

Filled from `benchmark/results/`. Gate column is the brief's threshold, not ours.

| Tier | Wall length | Ceiling height | Opening width | Footprint | Gate |
|---|---|---|---|---|---|
| Photo | | | | | wall ±8%, footprint ±8%, calibrated intervals |
| Video | | | | | wall ±3%, calibrated intervals |
| LiDAR | | | | | openings ≤2 cm on ≥85%, ceiling ≤1.5 cm, repeatability 1 cm / 0.5% |

## Open questions to close before submission

- Exact model and iOS version of the borrowed LiDAR device.
- Whether Record3D's free tier permits `.r3d` export at the clip lengths we need on that
  device. Fallback is 3D Scanner App (Laan Labs) → Export → All Data. **Tested in the probe
  session, not on shoot day.**
- Whether the photo tier degrades measurably between 8, 6 and the 2-still minimum. We capture
  a 2-still set specifically to measure this rather than assert it.
- Behaviour on a non-Pro device when the LiDAR tier is requested — must be a loud failure,
  and there must be a test asserting that.
