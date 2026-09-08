# Gate report

Regenerate: `python benchmark/scripts/gates.py`  
Ground truth: `benchmark/ground_truth/`, laser survey. Ceiling 2.74 m (supersedes an earlier 2.64 m tape figure that was hardcoded in four scripts).

## Ground-truth coverage

- Rooms: **5/5** complete
- Wall lengths: **20/20** measured
- Ceiling readings: **10**
- Floor areas: **5** derived from walls (none measured directly)
- Openings: **5/5** measured

## Ceiling height - gate <= 1.5 cm per room

| Room | truth | per_frame | multiview_unvalidated | multiview |
|---|---|---|---|---|
| Hallway | 2.740 m | 2.854 (+11.4 cm) FAIL | 2.854 (+11.4 cm) FAIL | 2.854 (+11.4 cm) FAIL |
| Kitchen | 2.740 m | 2.955 (+21.5 cm) FAIL | 2.917 (+17.7 cm) FAIL | 2.955 (+21.5 cm) FAIL |
| Room 1 | 2.740 m | 2.954 (+21.4 cm) FAIL | 2.720 (-2.1 cm) FAIL | ABSTAINED |
| Room 2 | 2.740 m | 2.907 (+16.7 cm) FAIL | ABSTAINED | ABSTAINED |
| Room 3 | 2.740 m | 2.689 (-5.1 cm) FAIL | 2.852 (+11.2 cm) FAIL | 2.978 (+23.8 cm) FAIL |

## Wall lengths - gate +/- 8% (photo tier)

| Room | truth dims | per_frame | multiview_unvalidated | multiview |
|---|---|---|---|---|
| Hallway | 5.44, 3.52 | 0/1 within 8%, worst 23.6% - **FAIL** | 0/1 within 8%, worst 23.6% - **FAIL** | 0/1 within 8%, worst 23.6% - **FAIL** |
| Kitchen | 2.63, 2.09 | 0/1 within 8%, worst 28.3% - **FAIL** | 0/2 within 8%, worst 54.1% - **FAIL** | 0/1 within 8%, worst 28.3% - **FAIL** |
| Room 1 | 3.40, 3.05 | 0/1 within 8%, worst 33.0% - **FAIL** | 0/2 within 8%, worst 14.8% - **FAIL** | 1/2 within 8%, worst 23.8% - **PARTIAL** |
| Room 2 | 3.24, 2.90 | 0/1 within 8%, worst 28.9% - **FAIL** | 0/2 within 8%, worst 17.8% - **FAIL** | 0/2 within 8%, worst 26.3% - **FAIL** |
| Room 3 | 3.00, 2.93 | 0/1 within 8%, worst 37.6% - **FAIL** | 0/2 within 8%, worst 10.8% - **FAIL** | 0/2 within 8%, worst 10.2% - **FAIL** |

### Span by span

**Hallway / per_frame**

| measured | truth | error |
|---|---|---|
| 2.688 m | 3.520 m | -23.6% |
| - | 5.440 m | dimension never measured by the pipeline |

**Hallway / multiview_unvalidated**

| measured | truth | error |
|---|---|---|
| 2.688 m | 3.520 m | -23.6% |
| - | 5.440 m | dimension never measured by the pipeline |

**Hallway / multiview**

| measured | truth | error |
|---|---|---|
| 2.688 m | 3.520 m | -23.6% |
| - | 5.440 m | dimension never measured by the pipeline |

**Kitchen / per_frame**

| measured | truth | error |
|---|---|---|
| 1.498 m | 2.090 m | -28.3% |
| - | 2.630 m | dimension never measured by the pipeline |

**Kitchen / multiview_unvalidated**

| measured | truth | error |
|---|---|---|
| 3.333 m | 2.630 m | +26.7% |
| 3.220 m | 2.090 m | +54.1% |
| 2.619 m | - | no unmatched ground-truth dimension left |
| 2.025 m | - | no unmatched ground-truth dimension left |

**Kitchen / multiview**

| measured | truth | error |
|---|---|---|
| 1.498 m | 2.090 m | -28.3% |
| - | 2.630 m | dimension never measured by the pipeline |

**Room 1 / per_frame**

| measured | truth | error |
|---|---|---|
| 2.042 m | 3.050 m | -33.0% |
| - | 3.400 m | dimension never measured by the pipeline |

**Room 1 / multiview_unvalidated**

| measured | truth | error |
|---|---|---|
| 3.788 m | 3.400 m | +11.4% |
| 3.502 m | 3.050 m | +14.8% |
| 3.096 m | - | no unmatched ground-truth dimension left |

**Room 1 / multiview**

| measured | truth | error |
|---|---|---|
| 3.529 m | 3.400 m | +3.8% |
| 2.323 m | 3.050 m | -23.8% |

**Room 2 / per_frame**

| measured | truth | error |
|---|---|---|
| 2.061 m | 2.900 m | -28.9% |
| - | 3.240 m | dimension never measured by the pipeline |

**Room 2 / multiview_unvalidated**

| measured | truth | error |
|---|---|---|
| 3.818 m | 3.240 m | +17.8% |
| 3.248 m | 2.900 m | +12.0% |
| 3.127 m | - | no unmatched ground-truth dimension left |

**Room 2 / multiview**

| measured | truth | error |
|---|---|---|
| 4.093 m | 3.240 m | +26.3% |
| 3.502 m | 2.900 m | +20.8% |
| 3.345 m | - | no unmatched ground-truth dimension left |
| 3.288 m | - | no unmatched ground-truth dimension left |

**Room 3 / per_frame**

| measured | truth | error |
|---|---|---|
| 1.829 m | 2.930 m | -37.6% |
| - | 3.000 m | dimension never measured by the pipeline |

**Room 3 / multiview_unvalidated**

| measured | truth | error |
|---|---|---|
| 3.325 m | 3.000 m | +10.8% |
| 3.205 m | 2.930 m | +9.4% |
| 2.727 m | - | no unmatched ground-truth dimension left |

**Room 3 / multiview**

| measured | truth | error |
|---|---|---|
| 3.307 m | 3.000 m | +10.2% |
| 3.171 m | 2.930 m | +8.2% |
| 3.066 m | - | no unmatched ground-truth dimension left |
| 2.982 m | - | no unmatched ground-truth dimension left |

## Gates that cannot be scored, and why

| Gate | Status | Why |
|---|---|---|
| openings | **SCOREABLE** | <= 2 cm on >= 85% |
| repeatability | **UNSCOREABLE** | no room has been captured twice at the same tier |
| drift_ablation | **FAIL** | multi-room stitching is NOT BUILT, so there is no stitched footprint to ablate. Within a room, photo multiview now rejects inconsistent poses by cycle consistency (see multiview_findings.md), but the brief's row is about accumulated drift across rooms and that is not addressed |
| photo_stitch | **FAIL** | pipeline/stitching/stitch.py raises NotImplementedError |
| head_to_head | **NOT BUILT** | no incumbent app export captured |
