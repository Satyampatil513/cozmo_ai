# Device matrix

Which tier runs on which hardware and what each tier honestly delivers. Accuracy columns come
from our own benchmark, not from expectation. Empty means unmeasured, not assumed.

| Tier | Minimum device | Sensors used | Scale source | Wall length | Ceiling height | Opening width | Status |
|---|---|---|---|---|---|---|---|
| Photo | iPhone 15 (non-Pro) | Rear wide camera | Printed scale card | | | | not measured |
| Video | iPhone 15 (non-Pro) | Rear wide camera | Printed scale card | | | | not measured |
| LiDAR | iPhone 15 Pro / Pro Max | LiDAR depth, ARKit pose, intrinsics | Sensor-metric | | | | not measured |

To fill before submission:
- Devices actually tested, by model and iOS version.
- Whether the photo tier degrades measurably below 6 stills per room.
- What happens on a non-Pro device when the LiDAR tier is requested. It must fail loudly.
