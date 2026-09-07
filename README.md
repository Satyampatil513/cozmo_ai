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

We provide no capture app. See `docs/CAPTURE_PROTOCOL.md` for the one-page stock-capture
protocol and `docs/DEVICE_MATRIX.md` for what each tier delivers on what hardware.

## Design in one paragraph

Every tier resolves to the same `Scene`: a list of frames, each with optional depth and
optional pose, plus an explicit scale source. LiDAR arrives with depth, poses and metres.
Video and photos arrive with none of those and get geometry from a shared pointmap backbone
and metres from a printed scale card placed by the capture protocol. Everything downstream of
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
