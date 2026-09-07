# Baseline: ceiling height, photo tier, single-frame

First measurement of the pipeline against real ground truth. This is the **before** run for
the fix loop.

- **Capture:** 28 stills, iPhone 17, 5 spaces, `benchmark/raw/photo/`
- **Ground truth:** ceiling height **2.64 m**, measured by tape in Room 1 and the Hallway.
  Assumed uniform across the flat pending measurement of the remaining rooms — the two
  measured rooms agree exactly, and a single slab pour is the norm.
- **Pipeline:** photo → Depth-Anything-V2-Metric-Indoor-Small → lift → RANSAC planes →
  floor/ceiling separation. One frame at a time; no multi-view fusion yet.
- **Commit:** see `git log`; regenerate with
  `python benchmark/scripts/depth_diagnostic.py benchmark/raw/photo`

## Result

| Room | Mean ceiling | Mean error | SD | ±8% gate |
|---|---|---|---|---|
| Room 1 | 3.212 m | **+21.7%** | 4.1% | FAIL |
| Room 2 | 3.287 m | **+24.5%** | 6.1% | FAIL |
| Room 3 | 2.693 m | +2.0% | 32.4% | *see below* |
| Hallway | 4.195 m | **+58.9%** | 6.4% | FAIL |
| Kitchen | 3.199 m | **+21.2%** | 42.1% | FAIL |

**All 27 usable frames: mean +26.1%, median +23.3%, SD 29.9%. Within ±8%: 4%.**

Against the brief's ceiling gate of **1.5 cm**, the error is roughly **60 cm**. Not close.

**Room 3's "+2.0%" is not a pass.** Five frames sit at +16% to +24% and one fails at −68%;
the mean is cancellation, not accuracy. Reporting the mean alone here would have been
flattering and wrong, which is precisely the failure mode the SD column exists to expose.

## What the error is, and is not

**It is not the geometry.** The plane stage finds the correct floor and ceiling on essentially
every frame, and Room 3 / IMG_0453 recovers 2.640 m against a true 2.64 m — exact. The
deterministic half is behaving as the synthetic tests said it would.

**It is not furniture.** A bed top mistaken for the floor was the leading hypothesis and it is
wrong: the frame that lands exactly on truth is in a bedroom with a large bed.

**It is the depth model's absolute scale**, and the structure of the error is informative:

- Strongly **per-room**: Room 1 +21.7 ± 4.1%, Hallway +58.9 ± 6.4%. Tight within a room,
  wildly different between rooms.
- Therefore **systematic, not random** — averaging frames of the same room does not remove it.
  This is the exact behaviour the synthetic error model predicted and the reason scale was
  never going to come from one source sampled repeatedly.
- **Not a single global factor either.** Assuming a constant true camera height and solving
  for it per room gives 1.13 m to 1.63 m, so the depth field is non-uniformly distorted, not
  merely scaled. A single multiplicative correction per frame cannot fully fix this.

## Consequence for the design

The floor-plane + camera-height anchor was designed as the second, uncorrelated scale source.
On this evidence it is **weaker than hoped**: it corrects a global scale, and the residual
here is not purely global. It stays useful as a cross-check and as a plausibility gate — a
frame implying a 2.4 m camera height is self-evidently wrong and can be rejected — but it is
not sufficient on its own.

## Next, in order of expected value

1. **A depth model that consumes intrinsics.** Depth-Anything's metric head infers field of
   view rather than being told it; Metric3D v2 takes K explicitly. Our frames are portrait
   with a 79.5° diagonal FOV, and a model assuming something else will be systematically off.
   This is the cheapest large win available and it is a one-line backend swap by design.
2. **A larger variant.** Small is the weakest head in the family; Base is affordable on CPU.
3. **Multi-view fusion.** The real fix, and the one the ±3% video gate will require. Needs
   poses, which needs VGGT-class inference — currently blocked on 4 GB of VRAM.

Each is a candidate for the fix declaration. The number to beat is **+26.1% mean, 4% of
frames within ±8%**.
