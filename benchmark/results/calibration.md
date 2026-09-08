# Calibration report

Regenerate: `python benchmark/scripts/calibrate.py`

Does a nominal interval contain the truth as often as it claims. Truth is the laser
survey (`benchmark/ground_truth/`) for the photo tier and the `.r3d` room's own tape
for LiDAR. **Data is thin** - 5 photo rooms, 1 LiDAR room - so these are directional,
not converged; the row says so.

## Residuals

| Tier | Quantity | n | bias | RMS error | worst |
|---|---|---|---|---|---|
| photo | ceiling | 5 | +4.8% | 6.0% | 7.9% |
| photo | wall | 5 | -30.3% | 30.7% | 37.6% |
| lidar | ceiling | 1 | +3.8% | 3.8% | 3.8% |

## Coverage of the current ERROR_MODEL

Nominal vs empirical - the fraction of truths inside `measured +/- z*sigma` for the
sigma `intervals.py` produces today. Calibrated means the two columns match.

| Tier | n | nominal 50% | nominal 80% | nominal 95% |
|---|---|---|---|---|
| photo | 10 | 10% | 30% | 50% |
| lidar | 1 | 0% | 0% | 0% |

## Refit on these residuals

The `(rel_sigma, abs_sigma)` a least-squares fit puts in `intervals.ERROR_MODEL`,
next to what is there now. Replace the placeholders with the fitted column and say
in the report that they were fitted.

| Tier | current (rel, abs m) | fitted (rel, abs m) | n |
|---|---|---|---|
| photo | (0.050, 0.040) | (0.221, 0.000) | 10 |
| lidar | (0.004, 0.008) | too few samples to fit | 1 |

**What the coverage says.** Where empirical 95% coverage is far below 95%, the
residual is dominated by a per-tier *bias* - a symmetric band around the measured
value cannot contain a truth the measurement is consistently offset from. Widening
sigma alone raises coverage only by making every interval uselessly wide; the honest
fix is the depth-scale bias itself (technical report section 5), not the interval.
