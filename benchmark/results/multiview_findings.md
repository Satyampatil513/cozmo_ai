# Multi-view photo registration: what it fixed, and what it did not

Regenerate everything below:

```bash
python benchmark/scripts/compare_photo_modes.py          # table + per-room overlays
python run.py benchmark/raw/photo --tier photo --out out_photo --photo-mode multiview
```

Three approaches, same raw photos, same cached depth, so every difference is registration:

| approach | what it does |
|---|---|
| `photo_per_frame` | each photo measured in its own camera frame, aggregated by median. The shipped default. |
| `photo_multiview_unvalidated` | maximum-support spanning tree, every edge trusted. The naive baseline. |
| `photo_multiview` | edges whose loops do not close are cut, implausible cameras dropped, then fuse. |

---

## Headline: the gate works. Multi-view does not improve ceiling accuracy.

Both halves of that sentence are load-bearing, and the second one refutes the expectation the
work started from. Stating it plainly here because the numbers are easy to quote selectively.

### Ceiling height, against 2.64 m tape

| Room | per_frame | naive multiview | gated multiview |
|---|---|---|---|
| Hallway | 2.854 (**+8.1%**) | 2.854 (+8.1%) *fell back* | 2.854 (+8.1%) *fell back* |
| Kitchen | 2.955 (+11.9%) | 2.917 (**+10.5%**) | 2.955 (+11.9%) *fell back* |
| Room 1 | 2.954 (+11.9%) | 2.720 (**+3.0%**) | **abstained** |
| Room 2 | 2.907 (**+10.1%**) | abstained | abstained |
| Room 3 | 2.689 (**+1.9%**) | 2.852 (+8.0%) | 2.978 (+12.8%) |

**Gated multi-view never wins a room.** It equals per-frame twice (by falling back), abstains
twice, and is 10.9 points worse on Room 3. There is no reading of this table in which
registration improved the ceiling measurement.

**Why, and why it was predictable.** The photo tier's ceiling error is dominated by monocular
depth scale, which is a bias every frame of a room shares. Fusion reduces the *random*
per-frame component as 1/sqrt(N); it cannot remove a bias common to all N. That was already
written down in `OVERNIGHT_PROGRESS.md` before this work started. The right conclusion is that
multi-view was never the lever for ceiling height, not that the registration is broken.

### The naive baseline's best number is luck, and the spread proves it

Room 1 naive multi-view reports **+3.0%**, the best multi-view number in the table. It is
built on poses containing a **2.36 m cycle error** and a **155 cm loop closure**. The evidence
that it is not a measurement is in the same result row: its fitted ceiling plane is **49 cm
thick**. A ceiling is flat. A 49 cm "plane" is a smear across a badly aligned cloud that
happened to average near the right height.

By contrast the gated Room 3 ceiling plane is **7.2 cm** thick, and gated Room 3 is the
*worst* ceiling number in the table. Plane thickness and ceiling accuracy point in opposite
directions here, which is exactly why neither number should be quoted alone.

---

## What multi-view actually bought

### 1. The first room polygon the photo tier has ever produced

Room 2, gated: a closed polygon, **12.535 m² floor area**, wall lengths
`[3.502, 3.345, 4.093, 3.288] m`. Neither per-frame nor naive multi-view produced a polygon in
any room. Per-frame *cannot*, in principle: unposed frames share no coordinate system, so a
room shape would be fabricated.

`out_multiview/Room_2/D_clouds_by_source.png` is the evidence that this polygon is real — the
top-down panel shows six photos' points landing on the *same* wall lines, interleaved by
colour, rather than six separate slabs.

### 2. Wall spans that are plausibly room dimensions

| Room | per_frame spans | gated multiview spans |
|---|---|---|
| Hallway | 2.688 | *(fell back)* |
| Kitchen | 1.498 | *(fell back)* |
| Room 1 | 2.042 | 3.529, 2.323 |
| Room 2 | 2.061 | 3.502, 3.345, 4.093, 3.288 |
| Room 3 | 1.829 | 3.307, 3.171, 2.982, 3.066 |

Per-frame yields one span per room, from a single view, and they are not room dimensions -
1.498 m across a kitchen is a gap between two cabinet fronts. Gated multi-view yields two to
four, clustered where a bedroom's dimensions should be.

**This is not scored.** `benchmark/ground_truth/rooms.csv` is empty: all 35 rows unfilled. The
spans are more numerous and mutually consistent, which is not the same as more accurate, and
they will stay unscored until someone walks the flat with a tape.

### 3. More corners, and a third of the runtime

| Room | corners: per_frame -> gated | seconds: per_frame -> gated |
|---|---|---|
| Room 1 | 3 -> 7 | 18.8 -> 5.4 |
| Room 2 | 4 -> 6 | 17.2 -> 6.5 |
| Room 3 | 8 -> 12 | 17.8 -> 7.6 |

Fusing once and fitting once is cheaper than fitting every frame separately.

---

## The defect this work actually fixed

`register.py` computed a world residual - the same feature, seen twice, transformed into the
common frame by each frame's pose - and printed it. Nothing consumed it. Four rooms shipped
reconstructions containing edges wrong by 41 cm to 204 cm, with a **median of 3.7 cm** in every
one of them. The median hid it completely.

Worst independently-checked loop closure, naive -> gated:

| Room | naive | gated | frames kept |
|---|---|---|---|
| Room 1 | 155 cm | **7.7 cm** | 4/6 |
| Room 2 | 204 cm | **4.3 cm** | 6/6 |
| Room 3 | 41 cm | **2.8 cm** | 6/6 |
| Hallway | - | REJECTED | 2/6 |
| Kitchen | - | REJECTED | 3/4 |

### Three things had to be true, and each was found by the previous one failing

**1. A pairwise residual cannot detect this.** Room 1 edge (0,3) has a pairwise residual of
4.8 cm and a world residual of 160 cm. The two-frame fit is fine; the pose one endpoint
inherited from elsewhere in the tree is not. Nothing is composed at pairwise time, so there is
nothing yet to disagree.

**2. Triangles are not enough.** Room 2 has 7 usable edges and exactly **one** triangle, and
its 204 cm edge sits in no triangle at all. Triangle-only cycle consistency is blind to it.
The fix is fundamental cycles: every edge the spanning tree does *not* use closes exactly one
loop with the tree path between its endpoints, which covers every remaining edge.

**3. Checking a pose against the edge that placed it is circular.** After gating, Room 1 put a
camera **2.30 m above the median camera height** and scored 7.7 cm, because frame 0 hung off
the graph by a single edge and that edge was the only thing available to check it. Two
consequences, both now implemented:

- `residual_max_nontree_m` is the number that decides trust. Tree edges are excluded, because
  a pose composed from an edge always agrees with it.
- A protocol-derived plausibility check: one operator walking one room holds the phone within
  a height band. Same class of prior as `CAMERA_UP` for gravity - it comes from the capture
  protocol, not from the scene.

### The gate refuses two rooms, and that is the correct answer

Hallway and Kitchen are **REJECTED** and fall back to per-frame. Their graphs contain **no
non-tree edge at all** - Kitchen is a bare chain 0-1-3 - so no loop exists anywhere in them and
nothing was ever independently verified. Residuals of 1.9 cm and 2.0 cm look excellent and mean
nothing, because they are measured on the edges that built the poses.

Refusing to fuse there is the whole point. Kitchen naive multi-view *did* fuse and reported
+10.5%, better than per-frame's +11.9%, on a reconstruction with zero independent verification.
Taking that number would have been rewarding an unverifiable result for being lucky.

---

## Honest state, and what is next

**Believe:** the gate. It catches 41-204 cm pose errors that the previous median-based report
called 3.7 cm, it explains every rejection in numbers, and it refuses rooms it cannot verify.

**Do not believe:** any claim that multi-view improves photo-tier accuracy. It does not, on
this benchmark, on any room.

**Unresolved:**

1. **Room 1 and Room 2 abstain under the gated path.** Both have verified poses and produce
   walls and corners, but no defensible floor/ceiling pair. Room 2 still yields a polygon while
   abstaining on ceiling height. Not yet diagnosed.
2. **Room 3 gets worse with better poses** - +1.9% per-frame to +12.8% gated - while its fused
   ceiling plane is *tighter* (7.2 cm). A well-fitted plane in the wrong place. Per-frame's
   +1.9% carries a 38.8 cm between-frame SD, so it is not a stable win either.
3. **Wall spans remain unscored.** Empty ground-truth CSVs are now the binding constraint on
   this entire line of work.
4. **No bundle adjustment.** This rejects inconsistent poses; it does not refine consistent
   ones. Refinement is the obvious next step and would likely narrow the abstentions - but it
   must come after the gate, not instead of it, or it would smooth these failures into
   plausible-looking wrong answers.
