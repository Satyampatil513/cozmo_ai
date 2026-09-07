# Depth backend comparison — ceiling height, photo tier, single-frame

Same 28 frames, same geometry stage, same ground truth (**2.64 m**, tape, Room 1 and Hallway).
Only the depth model changed.

Regenerate: `python benchmark/scripts/depth_diagnostic.py benchmark/raw/photo --backend <name>`

## Headline

| | Depth-Anything-V2 Metric-Indoor-Small | **Metric3D v2 ViT-Small** |
|---|---|---|
| Consumes intrinsics | no — infers FOV | **yes** |
| Mean error | +26.1% | **−12.2%** |
| Median error | +23.3% | **−8.4%** |
| SD | 29.9% | 24.1% |
| Frames within ±8% | 4% | **36%** |
| Implied camera height | 1.40–2.37 m (**impossible**) | **1.26 ± 0.33 m** (plausible) |
| CPU time / frame | 3.3 s | 13.1 s |

## Per room

| Room | Depth-Anything | Metric3D v2 |
|---|---|---|
| Room 1 | +21.7% | **−5.5%** (PASS ±8%) |
| Room 2 | +24.5% | −23.5% |
| Room 3 | +2.0% (cancellation) | −23.8% |
| Hallway | **+58.9%** | **+8.6%** |
| Kitchen | +21.2% | −19.0% |

## What this confirms

The hypothesis was that an FOV-inferring metric head would be systematically wrong on our
optics, and that a model conditioned on real intrinsics would fix most of it. That is what
happened, and the Hallway is the cleanest evidence: **+58.9% → +8.6%**, a room where the
previous model implied the camera floated 2.4 m above the floor.

The camera-height plausibility check earned its place here. It flagged the failure before any
tape measurement existed, and it is the metric that improved most: from physically impossible
to physically sensible. It remains a better *detector* of scale error than corrector.

## What is still wrong

**1. Three catastrophic frames.** Room 2/IMG_0451 (−77.3%), Room 3/IMG_0458 (−83.5%),
Kitchen/IMG_0435 (−63.4%) report ceilings of 0.44–0.97 m. These fail under *both* depth
models, so this is a geometry-stage fault, not a depth fault: the wrong pair of horizontal
planes is being selected as floor and ceiling. All three frames look through a doorway into
an adjoining space, which puts two floor levels and a door head in one view.

They also dominate the spread — the SD is 24.1% with them and far lower without. Excluding
them, per-room errors are roughly −5.5%, −12.8%, −11.9%, +8.6%, −4.1%.

The fix is not to silently drop them. It is to (a) choose the horizontal-plane pair whose
separation is physically plausible for a room rather than merely the largest two, and (b)
where no plausible pair exists, mark the frame failed with a reason instead of emitting
0.435 m as though it were a measurement.

**2. A residual per-room bias of about ±10%**, now signed differently per room rather than
uniformly positive. Still far outside the 1.5 cm ceiling gate. Single-frame monocular depth
is unlikely to close this; multi-view fusion is the real answer.

## Cost note

13.1 s/frame on CPU is fine for benchmarking and **too slow for the walk-in test**, where the
pipeline runs live. On the GTX 1650 this should drop by roughly an order of magnitude, which
is the argument for installing the CUDA build before the defense rather than after.

## Caveat on this comparison

Metric3D was swapped in *before* a fix declaration was filed, so this is model selection, not
the fix loop. The fix loop proper is declared in `docs/FIX_DECLARATION.md` and targets the
catastrophic-frame failure above, with this run as its "before".
