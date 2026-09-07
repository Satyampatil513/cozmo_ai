# Fix loop — photo tier ceiling height

Regenerate: `python benchmark/scripts/fix_loop_photo.py`
(add `--no-cache` to force live depth inference rather than the content-hashed cache)

- Frames: **28**, `benchmark/raw/photo/`
- Depth backend: `metric3d_v2`
- Ground truth: ceiling **2.64 m**, tape, Room 1 and Hallway
- Depth stage: 78.0s (28/28 cache hits)

## Before / after

| Selector | Mean err | Median | SD | Within ±8% | Catastrophic (>50%) | Reported | Abstained |
|---|---|---|---|---|---|---|---|
| **Before** — two largest horizontals | -12.2% | -8.4% | 24.1% | 36% | 3 | 28/28 | 0 |
| **After** — joint scoring (shipped) | +1.8% | +3.2% | 10.6% | 40% | 0 | 25/28 | 3 |

## Leave-one-out ablation

Each row removes exactly one signal from the joint score and re-runs the same frames.

| Removed signal | Mean err | Median | SD | Within ±8% | Catastrophic | Reported | Abstained |
|---|---|---|---|---|---|---|---|
| `bounding` | +1.4% | +3.0% | 10.6% | 40% | 0 | 25/28 | 3 |
| `camera` | +1.8% | +3.2% | 10.6% | 40% | 0 | 25/28 | 3 |
| `horizontality` | +1.8% | +3.2% | 10.6% | 40% | 0 | 25/28 | 3 |
| `parallelism` | +1.8% | +3.2% | 10.6% | 40% | 0 | 25/28 | 3 |
| `separation` | +1.8% | +3.2% | 10.6% | 40% | 0 | 25/28 | 3 |
| `support` | +1.8% | +3.2% | 10.6% | 40% | 0 | 25/28 | 3 |

Leave-one-out is **degenerate here**: dropping any single term leaves the remaining
five agreeing on the same argmax. So the table below scores with one term at a time,
and `none` keeps the exhaustive pair search but scores every pair equally — which
isolates how much of the fix is the search rather than any particular signal.

| Only signal | Mean err | Median | SD | Within ±8% | Catastrophic | Reported | Abstained |
|---|---|---|---|---|---|---|---|
| `none` (search only, uniform score) | -12.2% | -8.4% | 24.1% | 36% | 3 | 28/28 | 0 |
| `bounding` | -6.4% | +1.9% | 25.9% | 36% | 3 | 28/28 | 0 |
| `camera` | -6.4% | +1.9% | 25.9% | 36% | 3 | 28/28 | 0 |
| `horizontality` | -18.4% | -7.4% | 32.0% | 36% | 6 | 28/28 | 0 |
| `parallelism` | -16.6% | -6.4% | 31.2% | 32% | 6 | 28/28 | 0 |
| `separation` | -2.1% | -0.5% | 15.8% | 38% | 1 | 26/28 | 2 |
| `support` | -12.2% | -8.4% | 24.1% | 36% | 3 | 28/28 | 0 |

### What the ablation says

The exhaustive search on its own buys nothing: `none` scores -12.2% with 3 catastrophic frames, identical to the baseline. Considering every candidate pair only helps if the pairs are scored.

The load-bearing signal is **`separation`**: alone it reaches -2.1% with 1 catastrophic frames, against -12.2% and 3 for no scoring at all.

No single signal matches the full combination (+1.8%, SD 10.6%, 0 catastrophic), so the terms do compose — but the leave-one-out table above shows none of them is individually necessary, because the survivors agree on the same argmax.

This **contradicts the design rationale as originally written**, which claimed the
`bounding` term did most of the work. It does not. That claim is corrected in the
source and left on the record here rather than quietly edited away.

## Per-frame

| Room | Frame | Before | After |
|---|---|---|---|
| Hallway | IMG_0429.jpeg | 2.719 m (+3.0%) | 2.719 m (+3.0%) |
| Hallway | IMG_0430.jpeg | 2.877 m (+9.0%) | 2.877 m (+9.0%) |
| Hallway | IMG_0431.jpeg | 2.830 m (+7.2%) | 2.830 m (+7.2%) |
| Hallway | IMG_0432.jpeg | 2.724 m (+3.2%) | 2.724 m (+3.2%) |
| Hallway | IMG_0433.jpeg | 3.013 m (+14.1%) | 3.013 m (+14.1%) |
| Hallway | IMG_0434.jpeg | 3.034 m (+14.9%) | 3.034 m (+14.9%) |
| Kitchen | IMG_0435.jpeg | 0.967 m (-63.4%) | abstain |
| Kitchen | IMG_0436.jpeg | 1.915 m (-27.4%) | 2.955 m (+11.9%) |
| Kitchen | IMG_0437.jpeg | 2.952 m (+11.8%) | 2.952 m (+11.8%) |
| Kitchen | IMG_0438.jpeg | 2.723 m (+3.2%) | 2.723 m (+3.2%) |
| Room 1 | IMG_0439.jpeg | 2.556 m (-3.2%) | 2.556 m (-3.2%) |
| Room 1 | IMG_0440.jpeg | 2.345 m (-11.2%) | 2.904 m (+10.0%) |
| Room 1 | IMG_0443.jpeg | 2.304 m (-12.7%) | 2.901 m (+9.9%) |
| Room 1 | IMG_0444.jpeg | 2.509 m (-5.0%) | 2.509 m (-5.0%) |
| Room 1 | IMG_0445.jpeg | 2.664 m (+0.9%) | 2.664 m (+0.9%) |
| Room 1 | IMG_0446.jpeg | 2.593 m (-1.8%) | 2.593 m (-1.8%) |
| Room 2 | IMG_0447.jpeg | 2.288 m (-13.3%) | 2.288 m (-13.3%) |
| Room 2 | IMG_0448.jpeg | 2.237 m (-15.2%) | 2.989 m (+13.2%) |
| Room 2 | IMG_0449.jpeg | 2.292 m (-13.2%) | 2.292 m (-13.2%) |
| Room 2 | IMG_0450.jpeg | 2.260 m (-14.4%) | 2.260 m (-14.4%) |
| Room 2 | IMG_0451.jpeg | 0.599 m (-77.3%) | abstain |
| Room 2 | IMG_0452.jpeg | 2.434 m (-7.8%) | 2.434 m (-7.8%) |
| Room 3 | IMG_0453.jpeg | 2.136 m (-19.1%) | 2.136 m (-19.1%) |
| Room 3 | IMG_0454.jpeg | 2.403 m (-9.0%) | 3.138 m (+18.9%) |
| Room 3 | IMG_0456.jpeg | 2.334 m (-11.6%) | 2.334 m (-11.6%) |
| Room 3 | IMG_0457.jpeg | 2.299 m (-12.9%) | 2.906 m (+10.1%) |
| Room 3 | IMG_0458.jpeg | 0.435 m (-83.5%) | abstain |
| Room 3 | IMG_0459.jpeg | 2.457 m (-7.0%) | 2.457 m (-7.0%) |
