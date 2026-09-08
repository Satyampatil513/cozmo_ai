# Photo tier: per-frame vs naive multi-view vs cycle-gated multi-view

Regenerate: `python benchmark/scripts/compare_photo_modes.py`  
Ground truth: ceiling 2.64 m by tape. Wall lengths have NO ground truth (`benchmark/ground_truth/rooms.csv` is empty), so wall spans below are measurements, not errors.

## Ceiling height, against 2.64 m tape

| Room | per_frame | multiview_unvalidated | multiview |
|---|---|---|---|
| Hallway | 2.854 m (+8.1%) | 2.854 m (+8.1%) | 2.854 m (+8.1%) *(fell back)* |
| Kitchen | 2.955 m (+11.9%) | 2.917 m (+10.5%) | 2.955 m (+11.9%) *(fell back)* |
| Room 1 | 2.954 m (+11.9%) | 2.720 m (+3.0%) | abstained |
| Room 2 | 2.907 m (+10.1%) | abstained | abstained |
| Room 3 | 2.689 m (+1.9%) | 2.852 m (+8.0%) | 2.978 m (+12.8%) |

## Registration quality (gated mode)

`max, all edges` is the misleading number: a tree edge is the edge a pose was composed from, so checking the pose against it is circular and always passes. `max, non-tree` is the only independent evidence in the graph, and it is what the verdict uses. A room with zero non-tree edges has no loop anywhere in it and has verified nothing, however small its residuals look.

| Room | photos | registered | edges | cut: pairwise | cut: cycle | non-tree edges | max, all edges | max, non-tree | camera height dev | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Hallway | 6 | 2 | 2 | 0 | 0 | 0 | 3.24 cm | **none** | - | REJECTED -> per-frame |
| Kitchen | 4 | 3 | 2 | 0 | 0 | 0 | 1.97 cm | **none** | 0.095 m | REJECTED -> per-frame |
| Room 1 | 6 | 4 | 10 | 1 | 3 | 1 | 7.69 cm | 7.69 cm | 0.696 m | TRUSTED |
| Room 2 | 6 | 6 | 7 | 0 | 1 | 1 | 5.28 cm | 4.34 cm | 0.198 m | TRUSTED |
| Room 3 | 6 | 6 | 9 | 1 | 2 | 1 | 3.74 cm | 2.8 cm | 0.313 m | TRUSTED |

### Frames dropped, and why

- **Hallway** frame 1: no usable edge to any other frame
- **Hallway** frame 2: in a separate component [2, 5], not connected to the main reconstruction after cycle gating
- **Hallway** frame 4: no usable edge to any other frame
- **Hallway** frame 5: in a separate component [2, 5], not connected to the main reconstruction after cycle gating
- **Hallway** frame 0: placed, but on no loop - never independently cross-checked
- **Hallway** frame 3: placed, but on no loop - never independently cross-checked
- **Kitchen** frame 2: no usable edge to any other frame
- **Kitchen** frame 0: placed, but on no loop - never independently cross-checked
- **Kitchen** frame 1: placed, but on no loop - never independently cross-checked
- **Kitchen** frame 3: placed, but on no loop - never independently cross-checked
- **Room 1** frame 0: camera placed 2.30 m from the median camera height - physically impossible for one operator walking one room
- **Room 1** frame 5: reachable only through a frame dropped as implausible
- **Room 1** frame 1: placed, but on no loop - never independently cross-checked
- **Room 2** frame 1: placed, but on no loop - never independently cross-checked
- **Room 2** frame 2: placed, but on no loop - never independently cross-checked
- **Room 2** frame 4: placed, but on no loop - never independently cross-checked
- **Room 3** frame 1: placed, but on no loop - never independently cross-checked
- **Room 3** frame 3: placed, but on no loop - never independently cross-checked
- **Room 3** frame 5: placed, but on no loop - never independently cross-checked

## Structure recovered

| Room | mode | walls | corners | polygon | floor area | openings | spread | runtime |
|---|---|---|---|---|---|---|---|---|
| Hallway | per_frame | 4 | 2 | no | - | 0 | 12.4 cm (between-frame SD) | 19.9 s |
| Hallway | multiview_unvalidated | 4 | 2 | no | - | 0 | 12.4 cm (between-frame SD) | 20.6 s |
| Hallway | multiview | 4 | 2 | no | - | 0 | 12.4 cm (between-frame SD) | 20.2 s |
| Kitchen | per_frame | 7 | 5 | no | - | 0 | 12.9 cm (between-frame SD) | 16.7 s |
| Kitchen | multiview_unvalidated | 8 | 9 | no | - | 0 | 5.5 cm (plane thickness) | 5.1 s |
| Kitchen | multiview | 7 | 5 | no | - | 0 | 12.9 cm (between-frame SD) | 20.7 s |
| Room 1 | per_frame | 5 | 3 | no | - | 0 | 17.1 cm (between-frame SD) | 22.0 s |
| Room 1 | multiview_unvalidated | 5 | 5 | no | - | 0 | 49.0 cm (plane thickness) | 14.5 s |
| Room 1 | multiview | 7 | 7 | no | - | 0 | - | 6.4 s |
| Room 2 | per_frame | 5 | 4 | no | - | 0 | 30.1 cm (between-frame SD) | 18.6 s |
| Room 2 | multiview_unvalidated | 5 | 5 | no | - | 0 | - | 9.8 s |
| Room 2 | multiview | 5 | 6 | yes | 12.535 m2 | 0 | - | 9.1 s |
| Room 3 | per_frame | 7 | 8 | no | - | 0 | 38.8 cm (between-frame SD) | 24.6 s |
| Room 3 | multiview_unvalidated | 7 | 9 | no | - | 0 | 19.9 cm (plane thickness) | 11.4 s |
| Room 3 | multiview | 7 | 12 | no | - | 1 | 7.2 cm (plane thickness) | 11.5 s |

## Wall spans, as measured (no ground truth to score against)

| Room | mode | spans (m) |
|---|---|---|
| Hallway | per_frame | 2.688 |
| Hallway | multiview_unvalidated | 2.688 |
| Hallway | multiview | 2.688 |
| Kitchen | per_frame | 1.498 |
| Kitchen | multiview_unvalidated | 3.333, 2.025, 3.220, 2.619 |
| Kitchen | multiview | 1.498 |
| Room 1 | per_frame | 2.042 |
| Room 1 | multiview_unvalidated | 3.502, 3.096, 3.788 |
| Room 1 | multiview | 3.529, 2.323 |
| Room 2 | per_frame | 2.061 |
| Room 2 | multiview_unvalidated | 3.248, 3.127, 3.818 |
| Room 2 | multiview | 3.502, 3.345, 4.093, 3.288 |
| Room 3 | per_frame | 1.829 |
| Room 3 | multiview_unvalidated | 3.325, 3.205, 2.727 |
| Room 3 | multiview | 3.307, 3.171, 2.982, 3.066 |

### Hallway - gate detail

```
Edges (with a pairwise fit): 2
Rejected by pairwise residual: 0
Rejected by cycle consistency: 0  (triangles: 0, fundamental cycles: 0)
Triangles tested: 0  (failing at first pass: 0)
Largest cycle error: n/a
Edges in no triangle (uncheckable): 2
Connected component: 2/6 frames
World residual, all edges: median 3.2 cm, max 3.2 cm
World residual, NON-TREE edges only (0): none - nothing independently checked
Frames cycle-verified: none   placed but unverified: [0, 3]
Camera height deviation: n/a
Verdict: REJECTED
```

### Kitchen - gate detail

```
Edges (with a pairwise fit): 2
Rejected by pairwise residual: 0
Rejected by cycle consistency: 0  (triangles: 0, fundamental cycles: 0)
Triangles tested: 0  (failing at first pass: 0)
Largest cycle error: n/a
Edges in no triangle (uncheckable): 2
Connected component: 3/4 frames
World residual, all edges: median 1.9 cm, max 2.0 cm
World residual, NON-TREE edges only (0): none - nothing independently checked
Frames cycle-verified: none   placed but unverified: [0, 1, 3]
Camera height deviation: 0.09 m from median
Verdict: REJECTED
```

### Room 1 - gate detail

```
Edges (with a pairwise fit): 10
Rejected by pairwise residual: 1
Rejected by cycle consistency: 3  (triangles: 1, fundamental cycles: 2)
Triangles tested: 4  (failing at first pass: 2)
Largest cycle error: 2.36 m
Edges in no triangle (uncheckable): 0
Connected component: 4/6 frames
World residual, all edges: median 3.9 cm, max 7.7 cm
World residual, NON-TREE edges only (1): max 7.7 cm
Frames cycle-verified: [2, 3, 4]   placed but unverified: [1]
Camera height deviation: 0.70 m from median   dropped 1 implausible frame(s)
Verdict: TRUSTED
```

### Room 2 - gate detail

```
Edges (with a pairwise fit): 7
Rejected by pairwise residual: 0
Rejected by cycle consistency: 1  (triangles: 0, fundamental cycles: 1)
Triangles tested: 1  (failing at first pass: 0)
Largest cycle error: 0.03 m
Edges in no triangle (uncheckable): 4
Connected component: 6/6 frames
World residual, all edges: median 4.1 cm, max 5.3 cm
World residual, NON-TREE edges only (1): max 4.3 cm
Frames cycle-verified: [0, 3, 5]   placed but unverified: [1, 2, 4]
Camera height deviation: 0.20 m from median
Verdict: TRUSTED
```

### Room 3 - gate detail

```
Edges (with a pairwise fit): 9
Rejected by pairwise residual: 1
Rejected by cycle consistency: 2  (triangles: 1, fundamental cycles: 1)
Triangles tested: 3  (failing at first pass: 2)
Largest cycle error: 0.81 m
Edges in no triangle (uncheckable): 1
Connected component: 6/6 frames
World residual, all edges: median 2.6 cm, max 3.7 cm
World residual, NON-TREE edges only (1): max 2.8 cm
Frames cycle-verified: [0, 2, 4]   placed but unverified: [1, 3, 5]
Camera height deviation: 0.31 m from median
Verdict: TRUSTED
```
