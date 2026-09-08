# Drift ablation

Regenerate: `python benchmark/scripts/ablation.py`

Plane-anchored drift correction, ON vs OFF, on a synthetic two-room flat with a known
shared wall and a known rigid drift injected into the second room's poses. Same
`stitch_posed_capture` run.py calls; only `drift_correction` is toggled (run.py
`--no-drift-correction`). No real capture has produced a closed multi-room stitch to
ablate (technical report sections 4 and 7), so this is synthetic ground truth.

`drift_correction=False` is "poses used as-is" for room placement - the brief's
automatic-fail condition. The point of the row is that it visibly costs something.

| Case | injected drift | shared-wall gap, no correction | gap, correction OFF | gap, correction ON | footprint OFF | footprint ON |
|---|---|---|---|---|---|---|
| no drift (control) | none | 0.0 cm | 0.0 cm | 0.0 cm | - | - |
| small drift | yaw 3 deg, shift [8.0, -4.0, 2.0] cm | 7.8 cm | 7.8 cm | 0.0 cm | - | - |
| large drift | yaw 6 deg, shift [15.0, -8.0, 5.0] cm | 14.1 cm | 14.1 cm | 0.0 cm | - | - |

**Reading it.** The shared-wall gap is the distance between the two rooms' copies of
the wall they share - 0 for a perfect stitch. With correction OFF that gap is the raw
injected drift; with it ON the gap closes. Where both rooms' polygons also close, the
footprint column shows the union area moving with it. The control row (no injected
drift) is the check that correction is near a no-op when there is nothing to correct.
