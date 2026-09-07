# Fix declaration

Filed **before** the fix is written. Not edited afterwards; the post-mortem goes below it.

Date filed: 2026-09-08
Commit at filing: see `git log` for the commit that adds this file.

## 1. Worst-performing gate in our own benchmark

**Gate:** ceiling height, photo tier — and, more usefully, the spread across frames of the
same room, which the brief scores separately (`repeatable-but-biased` vs `unrepeatable`).

**Measured:** across 28 frames with Metric3D v2, mean error **−12.2%**, **SD 24.1%**, and only
**36%** of frames within ±8%. Three frames report ceiling heights of **0.44 m, 0.60 m and
0.97 m** against a true 2.64 m — errors of −83.5%, −77.3% and −63.4%.

**Threshold:** 1.5 cm per room, and ≤1 cm spread across repeat captures.

Full run: `benchmark/results/depth_backend_comparison.md`.

## 2. Root-cause hypothesis and evidence

**Hypothesis:** the three catastrophic frames are a **geometry-stage plane-selection fault,
not a depth-model fault.** `classify()` labels floor and ceiling as the two largest horizontal
planes. In a frame that looks through a doorway into an adjoining space, the largest two
horizontal planes can be a near floor and a door head, or two different floor levels — and
their separation is then physically impossible for a room.

**Evidence:**

1. **The same three frames fail under both depth models.** Room 3/IMG_0458 gave 0.837 m with
   Depth-Anything and 0.435 m with Metric3D. A fault that survives replacing the learned
   component is not in the learned component.
2. **All three frames look through a doorway.** IMG_0458 and IMG_0435 both frame an open
   doorway into another space; the contact sheets show it directly.
3. **Their implied camera heights are anomalous relative to their own room.** The failing
   frames sit at 1.65–1.73 m where their siblings sit at 0.89–1.11 m, i.e. a *different plane*
   was chosen as the floor, not a rescaled one.
4. **Removing them collapses the spread.** Per-room errors go from −23.5% (SD 24.2%) to
   roughly −12.8% with far tighter spread, without touching the depth model.

## 3. Fix to be shipped, and predicted number

**Fix:** replace "the two largest horizontal planes" with a plausibility-constrained
selection.

- Among horizontal planes, choose the (floor, ceiling) pair maximising combined inlier support
  **subject to** their separation lying in a physically possible range for a habitable room
  (1.9 m to 4.5 m).
- Where no pair satisfies the constraint, **mark the frame failed with a reason** rather than
  emitting a number. A frame that cannot see a room is not a measurement of one, and silently
  dropping it would be as dishonest as reporting 0.435 m.
- The constraint is a stated prior, disclosed in the report, not a tuned threshold.

**Predicted after:**

- Catastrophic frames (|error| > 50%): **3 → 0**
- SD across all frames: 24.1% → **under 12%**
- Frames within ±8%: 36% → **50–60%**
- Mean error: roughly unchanged at **−10% to −13%** — this fix targets spread, not bias.

Stating that mean will barely move is deliberate. The remaining bias is single-frame monocular
scale error and this fix does not address it; claiming otherwise would make the prediction
look better and be wrong.

## Post-mortem (written after the after-run)

Actual after:

Prediction error:

Why:
