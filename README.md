# Cozmo AI case study

Handheld consumer capture to a dimensioned, stitched, damage-annotated property plan.
Three input tiers (photo, video, LiDAR), one output contract, calibrated intervals throughout.

## Status

Early. See `docs/COMPLIANCE_MATRIX.md` for what exists and what does not. Every row marked
NOT BUILT is genuinely not built; nothing here is stubbed to look finished.

## Setup, fresh machine

```
git clone <repo>
cd cozmo-ai
python -m venv .venv && . .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/fetch_weights.py                     # not built yet
```

## Run one capture

```
python run.py capture/ --out out/
```

Writes `out/result.json`, `out/plan.svg`, `out/plan.png`.

Drift ablation:

```
python run.py capture/ --out out_nodrift/ --no-drift-correction
```

## Capture

Route 2, stock capture: native iOS Camera for the photo and video tiers, Record3D for the
LiDAR tier. No custom app to install.

- `docs/CAPTURE_PROTOCOL.md` - the one page an operator follows literally
- `docs/DEVICE_MATRIX.md` - which tier runs on which hardware, and what it delivers
- `docs/capture/` - the operator-facing shot lists we used for our own benchmark

**Nothing to print, nothing to place in the room.** Photo and video are scale-ambiguous -
a room and a scale model of it produce identical pixels - so metres come from a metric depth
model fused with the floor plane and one stated number, the operator's camera height. We
deliberately require no fiducial: the benchmark has to be captured the same way Cozmo will
capture at the walk-in test, or the benchmark predicts nothing.

LiDAR requires a Pro-class iPhone. On a non-Pro device that tier fails loudly rather than
falling back to the video path - reporting video-tier accuracy under a LiDAR-tier label
would be worse than refusing.

## Design in one paragraph

Every tier resolves to the same `Scene`: a list of frames, each with optional depth and
optional pose, plus an explicit scale source. LiDAR arrives with depth, poses and metres.
Video and photos arrive with none of those and get geometry from a shared pointmap backbone
and metres from a metric depth model fused with the floor plane. Everything downstream of
`pipeline/capture/` is tier-agnostic and never branches on tier; the tiers differ only in how
wide their intervals end up, and those widths are fitted on our own benchmark residuals rather
than chosen.

## Layout

```
docs/         protocol, device matrix, compliance matrix, reports
schemas/      published output contract
pipeline/     capture -> geometry -> stitching -> damage -> confidence -> output
benchmark/    raw captures, tape ground truth, harness, results
```
