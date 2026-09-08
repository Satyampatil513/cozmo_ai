"""Multi-view photo registration with an explicit cycle-consistency gate.

This sits on top of `pipeline.capture.register`, which already does the hard geometry: SIFT
correspondences between every pair, and a RANSAC-wrapped Umeyama similarity fit giving the
transform between any two frames that overlap. That module is UNCHANGED and still runnable on
its own - it is the `photo_multiview_unvalidated` baseline, kept deliberately so the fix loop
can show per-frame -> naive multiview -> gated multiview as three real runs.

WHAT THIS ADDS, AND WHY IT WAS NEEDED.

`register.register()` builds a maximum-support spanning tree and then *reports* a world
residual: the same physical feature, seen in two frames, transformed into the common frame by
each frame's pose, and the distance between the two estimates. The number was computed and
printed. It was never used to decide anything.

Measured on our own capture (`benchmark/results/register_photos.json`):

    Room 2  pair (1,4)   pairwise residual 3.1 cm    world residual 203.6 cm
    Room 1  pair (0,3)   pairwise residual 4.8 cm    world residual 159.7 cm
    Room 3  pair (2,3)   pairwise residual 5.0 cm    world residual  77.9 cm

Two frames placing the same corner two metres apart is not a reconstruction. Note the shape of
it: the *pairwise* fit is fine in every case. The direct edge is a good edge. What is wrong is
the pose one endpoint inherited from elsewhere in the tree, so the direct edge and the composed
path disagree.

    THE LESSON: a pairwise residual cannot detect this, because nothing is composed yet.
    Only closing a loop can - and a spanning tree has no loops by construction. Every edge
    the tree discards is exactly the evidence needed to check the edges it kept.

So the gate is a cycle-consistency test. It runs in two stages, because one kind of loop is
not enough to cover a real photo graph.

STAGE A - TRIANGLES, before any pose exists.

For every triangle (i, j, k) whose three edges survived the pairwise filter, compose the loop
that should return a point to where it started:

    Loop = T_ij . T_jk . T_ki          (T_ab maps a point in b's frame into a's frame)

If the three transforms agree, Loop is the identity. It is measured in metres, not as an
abstract matrix distance: real matched scene points are pushed around the loop and the median
displacement is the cycle error. That number is directly interpretable - "this triangle of
photos disagrees with itself by 2.36 m" - which matters because the gate has to be defensible
out loud, not merely tuned. A pure 2-degree rotation error contributes no translation term at
all yet moves a point 3 m away by 10 cm, and it is the point that has to land on the wall.

STAGE B - FUNDAMENTAL CYCLES, after the tree is built.

Triangles alone are not enough, and our own capture proves it: Room 2 has 7 usable edges and
exactly ONE triangle, and its 204 cm edge sits in no triangle at all. A triangle-only test is
blind to it.

But every edge the spanning tree does NOT use closes exactly one loop with the tree path
between its endpoints - its fundamental cycle - and the closure error of that loop is what
`world_residual` measures once poses exist. Checking every non-tree edge therefore checks
every remaining independent loop in the graph, triangle or not. This is the same insight as
above, generalised: the edges a tree discards are precisely the evidence needed to audit the
edges it kept.

In both stages, blame is spread across the whole failing loop, because a loop that does not
close does not say which of its edges is wrong. The edge with the most accumulated blame is
removed, the loops are re-scored, and it repeats. Iterating is what resolves the ambiguity: a
genuinely bad edge fails every loop it touches, while a good edge appears in one failing loop
and many passing ones. Cutting an edge can split the graph, so components are recomputed after
every removal rather than assumed.

What survives is a graph whose loops close. Frames are split into connected components, the
largest is kept, and only within it are poses composed. Frames in smaller components are NOT
forced into the reconstruction - a frame reachable only through a rejected edge stays
unregistered, and the report says why. Partial coverage measured correctly beats full coverage
measured wrongly.

An edge in no loop of either kind cannot be checked at all. It is kept, because rejecting every
unverifiable edge would discard most of a 4-photo room, but it is counted and reported as
uncheckable so the summary never implies more verification than actually happened.

STAGE C - PHYSICAL PLAUSIBILITY, because graph consistency has a blind spot.

A frame placed through an edge that lies in no loop is composed from that edge and then checked
against it. That is circular and it always passes. Room 1 placed a camera 2.30 m above the
median camera height and scored 7.7 cm, because the only edge available to check it was the
edge that put it there. One protocol-derived fact closes the hole: a single operator walked one
room holding a phone, so the cameras share a height band. That prior is the same class as
CAMERA_UP for gravity - it comes from the capture protocol, not from the scene, so it does not
depend on which surfaces happened to be visible.

MEASURED on the five benchmark rooms (`benchmark/results/compare_photo_modes.md`).

Worst INDEPENDENTLY-CHECKED loop closure - non-tree edges only, naive -> gated:

    Room 1   155 cm -> 7.7 cm    4/6 frames kept (one camera 2.30 m out, plus the frame
                                 reachable only through it, both dropped)
    Room 2   204 cm -> 4.3 cm    6/6 frames kept
    Room 3    41 cm -> 2.8 cm    6/6 frames kept
    Hallway, Kitchen             REJECTED - their graphs contain no non-tree edge at all, so
                                 nothing in them was ever independently checked. Both fall
                                 back to per-frame rather than fuse on unverified poses.

WHAT THIS DOES NOT DO, stated because the benchmark refuted the obvious expectation: it does
NOT improve ceiling-height accuracy. Gated multi-view is worse than the per-frame path on
ceiling height in every room where the two differ, and abstains in two. Fusion cannot remove a
bias that every frame shares, and photo-tier ceiling error is dominated by monocular depth
scale rather than by geometry. What it does buy is structure: the first room polygon the photo
tier has produced, two to four wall spans per room instead of one, more corners, and about a
third of the runtime. Read the report before quoting any accuracy claim from this module.

DELIBERATELY NOT DONE HERE: bundle adjustment, loop closure, pose-graph optimisation. This
rejects inconsistent poses; it does not refine consistent ones. Refinement is the obvious next
step, is a different piece of work, and would hide this failure mode rather than expose it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from pipeline.capture.register import PairMatch, match_all
from pipeline.types import Frame

# A triangle of photos must return a point to within this of where it started. Derived, not
# picked: the pairwise fits themselves land at 2-5 cm and register.INLIER_THRESH_M is 10 cm,
# so a loop of three composed transforms should stay within roughly three times that. 30 cm
# allows every edge in the loop to be at its own tolerance and still pass, while rejecting the
# 78-204 cm failures by an order of magnitude. Anything looser would admit a corner placed
# most of a room away from itself, which is the failure this exists to catch.
CYCLE_GATE_M = 0.30

# An edge must clear its own pairwise fit before it is allowed into a triangle at all.
MAX_PAIR_RESIDUAL_M = 0.12

# A pairwise depth-scale ratio outside this is a feature mismatch, not two frames disagreeing
# about scale. register.SCALE_BOUNDS is (0.5, 2.0) for the fit itself; a 40% disagreement
# between two photos of one room is already implausible for a metric depth model.
SCALE_SANITY = (0.7, 1.4)

# Final acceptance: worst loop-closure residual over the NON-TREE edges. Tree edges are
# excluded on purpose - see `MultiviewRegistration.residual_max_nontree_m`.
WORLD_RESIDUAL_GATE_M = 0.25

# Camera-height plausibility. The capture protocol has one operator walking a room holding a
# phone, so the cameras of a room sit within a band: standing to crouching is well under a
# metre of variation. This is the same class of prior as CAMERA_UP for gravity - it comes from
# the protocol, not from the scene, so it does not depend on which surfaces happened to be
# visible. It exists because graph consistency alone cannot catch a frame placed through a
# single unverifiable edge: Room 1 put a camera 2.74 m above the reference and passed every
# residual test, because the only edge available to check it was the edge that placed it.
# 0.9 m admits any real crouch-to-standing range and rejects that 2.74 m by a factor of three.
MAX_CAMERA_HEIGHT_DEV_M = 0.9


# --------------------------------------------------------------------------- geometry helpers

def edge_transform(pm: PairMatch, a: int, b: int) -> np.ndarray:
    """Transform carrying a point in b's camera frame into a's. `pm.T_ij` maps j into i."""
    return pm.T_ij if (a, b) == (pm.i, pm.j) else np.linalg.inv(pm.T_ij)


def _apply(T: np.ndarray, P: np.ndarray) -> np.ndarray:
    return (T[:3, :3] @ P.T).T + T[:3, 3]


def world_residual(pm: PairMatch, pose_i: np.ndarray, pose_j: np.ndarray) -> float:
    """Median distance between the two world estimates of each shared feature.

    The whole verification in one line: if the poses are right, `pose_i` applied to a feature's
    3D position in frame i and `pose_j` applied to the same feature in frame j are two
    measurements of one physical point, and they must coincide. No ground truth involved.
    """
    if pm.pts_i is None or pm.pts_j is None or not len(pm.pts_i):
        return float("nan")
    wi = _apply(pose_i, pm.pts_i)
    wj = _apply(pose_j, pm.pts_j)
    return float(np.median(np.linalg.norm(wi - wj, axis=1)))


# ------------------------------------------------------------------------------- stage 1: edges

@dataclass
class EdgeReport:
    edge: tuple[int, int]
    inliers: int = 0
    matches: int = 0
    pair_scale: float = float("nan")
    pair_residual_m: float = float("nan")
    cycle_error_m: float = float("nan")     # worst triangle this edge sits in
    n_triangles: int = 0
    world_residual_m: float = float("nan")
    status: str = "kept"                    # kept | no_overlap | pairwise | cycle | orphaned
    reason: str = ""
    in_tree: bool = False

    def to_json(self) -> dict:
        def cm(x):
            return None if not np.isfinite(x) else round(x * 100, 2)
        return {"edge": list(self.edge), "status": self.status, "reason": self.reason,
                "inliers": self.inliers, "pair_scale": (None if not np.isfinite(self.pair_scale)
                                                        else round(self.pair_scale, 4)),
                "pair_residual_cm": cm(self.pair_residual_m),
                "cycle_error_cm": cm(self.cycle_error_m), "triangles": self.n_triangles,
                "world_residual_cm": cm(self.world_residual_m), "in_tree": self.in_tree}


def filter_edges(pairs: list[PairMatch]) -> dict[tuple[int, int], EdgeReport]:
    """Stage 1. Judge every candidate edge on its own fit, before anything is composed."""
    reports: dict[tuple[int, int], EdgeReport] = {}
    for p in pairs:
        key = (p.i, p.j)
        r = EdgeReport(edge=key, inliers=p.n_inliers, matches=p.n_matches,
                       pair_scale=p.scale, pair_residual_m=p.residual_m)
        if p.T_ij is None:
            r.status = "no_overlap"
            r.reason = (f"only {p.n_matches} matches, similarity fit failed"
                        if p.n_matches else "no feature overlap")
        elif not p.ok:
            r.status = "pairwise"
            r.reason = f"only {p.n_inliers} 3D inliers"
        elif not np.isfinite(p.residual_m) or p.residual_m > MAX_PAIR_RESIDUAL_M:
            r.status = "pairwise"
            r.reason = (f"pairwise residual {p.residual_m * 100:.1f} cm over "
                        f"{MAX_PAIR_RESIDUAL_M * 100:.0f} cm")
        elif not (SCALE_SANITY[0] < p.scale < SCALE_SANITY[1]):
            r.status = "pairwise"
            r.reason = (f"depth-scale ratio {p.scale:.2f} outside "
                        f"{SCALE_SANITY} - a mismatch, not scale disagreement")
        reports[key] = r
    return reports


# ------------------------------------------------------------------- stage 2: cycle consistency

def triangle_error(pm_ij: PairMatch, pm_jk: PairMatch, pm_ik: PairMatch,
                   i: int, j: int, k: int) -> float:
    """Median displacement, in metres, of real scene points pushed around the loop i->k->j->i.

    Measured on points rather than on the matrix so the number means something physical. A
    pure rotation error of 2 degrees produces no translation term at all but moves a point 3 m
    away by 10 cm, and it is the point that has to land on the right wall.
    """
    T_ij = edge_transform(pm_ij, i, j)      # j -> i
    T_jk = edge_transform(pm_jk, j, k)      # k -> j
    T_ki = edge_transform(pm_ik, k, i)      # i -> k
    loop = T_ij @ T_jk @ T_ki               # i -> i, should be identity

    P = pm_ij.pts_i if (pm_ij.i == i) else pm_ij.pts_j
    if P is None or not len(P):
        return float("nan")
    return float(np.median(np.linalg.norm(_apply(loop, P) - P, axis=1)))


def cycle_filter(reports: dict[tuple[int, int], EdgeReport],
                 pairs: dict[tuple[int, int], PairMatch],
                 gate_m: float = CYCLE_GATE_M) -> dict:
    """Stage 2. Remove edges until every triangle in the graph closes.

    Greedy, and re-scored after each removal: the edge implicated in the most failing
    triangles - weighted by how badly they fail - is removed first, because one bad transform
    drags every triangle it touches over the gate and the good edges alongside it would
    otherwise look equally guilty.
    """
    alive = {k for k, r in reports.items() if r.status == "kept"}
    stats = {"triangles_total": 0, "triangles_failing_initial": 0,
             "largest_cycle_error_m": float("nan"), "rounds": 0,
             "removed": [], "edges_in_no_triangle": 0}

    def key(a, b):
        return (a, b) if (a, b) in pairs else (b, a)

    def triangles(live: set) -> list[tuple]:
        nodes = sorted({n for e in live for n in e})
        out = []
        for x in range(len(nodes)):
            for y in range(x + 1, len(nodes)):
                for z in range(y + 1, len(nodes)):
                    i, j, k = nodes[x], nodes[y], nodes[z]
                    e1, e2, e3 = key(i, j), key(j, k), key(i, k)
                    if e1 in live and e2 in live and e3 in live:
                        out.append((i, j, k, e1, e2, e3))
        return out

    first_pass = True
    while True:
        tris = triangles(alive)
        scored = []
        for i, j, k, e1, e2, e3 in tris:
            err = triangle_error(pairs[e1], pairs[e2], pairs[e3], i, j, k)
            if np.isfinite(err):
                scored.append((err, e1, e2, e3))

        if first_pass:
            stats["triangles_total"] = len(scored)
            stats["triangles_failing_initial"] = sum(1 for s in scored if s[0] > gate_m)
            if scored:
                stats["largest_cycle_error_m"] = max(s[0] for s in scored)
            # Record each edge's worst triangle for the report, before any removal.
            for err, *es in scored:
                for e in es:
                    reports[e].n_triangles += 1
                    if not np.isfinite(reports[e].cycle_error_m) or err > reports[e].cycle_error_m:
                        reports[e].cycle_error_m = err
            first_pass = False

        failing = [s for s in scored if s[0] > gate_m]
        if not failing:
            break

        # Blame each edge for the excess of every failing triangle it belongs to.
        blame: dict[tuple[int, int], float] = {}
        for err, *es in failing:
            for e in es:
                blame[e] = blame.get(e, 0.0) + (err - gate_m)
        # Tie-break on inlier support: drop the weakest of two equally implicated edges.
        worst = max(blame, key=lambda e: (blame[e], -reports[e].inliers))
        reports[worst].status = "cycle"
        reports[worst].reason = (
            f"in {sum(1 for _, *es in failing if worst in es)} failing triangle(s), "
            f"worst cycle error {max(err for err, *es in failing if worst in es) * 100:.0f} cm "
            f"over the {gate_m * 100:.0f} cm gate")
        stats["removed"].append({"edge": list(worst), "blame_m": round(blame[worst], 3)})
        alive.discard(worst)
        stats["rounds"] += 1
        if stats["rounds"] > 4 * len(reports):        # cannot happen; guards a pathological graph
            break

    stats["edges_in_no_triangle"] = sum(
        1 for e in alive if reports[e].n_triangles == 0)
    return stats


# ------------------------------------------------------------------ stage 3: components + poses

def connected_components(alive: set[tuple[int, int]], n: int) -> list[list[int]]:
    """Frames grouped by reachability over the surviving edges, largest first."""
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in alive:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    groups: dict[int, list[int]] = {}
    for i in range(n):
        if any(i in e for e in alive):
            groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=len, reverse=True)


@dataclass
class MultiviewRegistration:
    """The reconstruction, plus everything needed to judge whether to trust it."""
    poses: list[Optional[np.ndarray]]
    scales: list[float]
    reference: int
    edges: list[EdgeReport] = field(default_factory=list)
    components: list[list[int]] = field(default_factory=list)
    unregistered: list[dict] = field(default_factory=list)
    cycle_stats: dict = field(default_factory=dict)
    residual_median_m: float = float("nan")
    residual_p90_m: float = float("nan")
    residual_max_m: float = float("nan")
    # The number that actually decides trust. A tree edge is the edge a frame's pose was
    # COMPOSED from, so checking the pose against it is circular and always passes: Room 1
    # placed a camera 2.74 m in the air and scored 7.7 cm because the only edge available to
    # check it was the one that put it there. Non-tree edges were not used to build any pose,
    # so they are the only independent evidence in the graph.
    residual_max_nontree_m: float = float("nan")
    n_nontree_edges: int = 0
    verified_frames: list[int] = field(default_factory=list)
    unverified_frames: list[int] = field(default_factory=list)
    camera_height_dev_m: float = float("nan")
    dropped_implausible: list[dict] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def n_registered(self) -> int:
        return sum(1 for p in self.poses if p is not None)

    @property
    def n_rejected_pairwise(self) -> int:
        return sum(1 for e in self.edges if e.status == "pairwise")

    @property
    def n_rejected_cycle(self) -> int:
        return sum(1 for e in self.edges if e.status == "cycle")

    @property
    def n_candidate_edges(self) -> int:
        """Edges that had a pairwise fit at all - the denominator worth reporting.

        Pairs with no feature overlap are not failures of anything; two photos of opposite
        walls are simply not a constraint, and counting them as rejected edges would inflate
        the rejection rate with pairs that never had a chance.
        """
        return sum(1 for e in self.edges if e.status != "no_overlap")

    @property
    def trustworthy(self) -> bool:
        """Accepted only on independent evidence, never on the median and never on a tree edge.

        Three frames is the minimum `measure_room` fuses at. Beyond that, the reconstruction
        must carry at least one non-tree edge - without one there is no loop anywhere in the
        graph and nothing has actually been verified, however small the residuals look - that
        edge must close inside the gate, and no camera may sit at an impossible height.
        """
        return (self.n_registered >= 3
                and self.n_nontree_edges >= 1
                and np.isfinite(self.residual_max_nontree_m)
                and self.residual_max_nontree_m <= WORLD_RESIDUAL_GATE_M
                and np.isfinite(self.camera_height_dev_m)
                and self.camera_height_dev_m <= MAX_CAMERA_HEIGHT_DEV_M)

    def report_lines(self) -> list[str]:
        """The gate, stated in numbers, not as a verdict. Printed by every runner."""
        cs = self.cycle_stats
        lc = cs.get("largest_cycle_error_m", float("nan"))
        return [
            f"Edges (with a pairwise fit): {self.n_candidate_edges}",
            f"Rejected by pairwise residual: {self.n_rejected_pairwise}",
            f"Rejected by cycle consistency: {self.n_rejected_cycle}"
            f"  (triangles: {len(cs.get('removed', []))}, "
            f"fundamental cycles: {len(cs.get('fundamental_cycles_cut', []))})",
            f"Triangles tested: {cs.get('triangles_total', 0)}"
            f"  (failing at first pass: {cs.get('triangles_failing_initial', 0)})",
            f"Largest cycle error: " + ("n/a" if not np.isfinite(lc) else f"{lc:.2f} m"),
            f"Edges in no triangle (uncheckable): {cs.get('edges_in_no_triangle', 0)}",
            f"Connected component: {self.n_registered}/{len(self.poses)} frames",
            f"World residual, all edges: "
            + ("n/a" if not np.isfinite(self.residual_max_m)
               else f"median {self.residual_median_m * 100:.1f} cm, "
                    f"max {self.residual_max_m * 100:.1f} cm"),
            f"World residual, NON-TREE edges only ({self.n_nontree_edges}): "
            + ("none - nothing independently checked"
               if not np.isfinite(self.residual_max_nontree_m)
               else f"max {self.residual_max_nontree_m * 100:.1f} cm"),
            f"Frames cycle-verified: {self.verified_frames or 'none'}"
            + (f"   placed but unverified: {self.unverified_frames}"
               if self.unverified_frames else ""),
            f"Camera height deviation: "
            + ("n/a" if not np.isfinite(self.camera_height_dev_m)
               else f"{self.camera_height_dev_m:.2f} m from median")
            + (f"   dropped {len(self.dropped_implausible)} implausible frame(s)"
               if self.dropped_implausible else ""),
            f"Verdict: {'TRUSTED' if self.trustworthy else 'REJECTED'}",
        ]

    @property
    def summary(self) -> str:
        if not np.isfinite(self.residual_max_m):
            return f"{self.n_registered}/{len(self.poses)} registered (no verifiable edges)"
        return (f"{self.n_registered}/{len(self.poses)} registered, "
                f"{self.n_rejected_cycle} edge(s) cut by cycle consistency, "
                f"world residual max {self.residual_max_m * 100:.1f} cm")

    def to_json(self) -> dict:
        def cm(x):
            return None if not np.isfinite(x) else round(x * 100, 2)
        cs = dict(self.cycle_stats)
        if np.isfinite(cs.get("largest_cycle_error_m", float("nan"))):
            cs["largest_cycle_error_cm"] = round(cs["largest_cycle_error_m"] * 100, 2)
        cs.pop("largest_cycle_error_m", None)
        return {
            "n_frames": len(self.poses),
            "n_registered": self.n_registered,
            "reference": self.reference,
            "trustworthy": self.trustworthy,
            "candidate_edges": self.n_candidate_edges,
            "rejected_pairwise": self.n_rejected_pairwise,
            "rejected_cycle": self.n_rejected_cycle,
            "cycle": cs,
            "components": self.components,
            "unregistered": self.unregistered,
            "residual_median_cm": cm(self.residual_median_m),
            "residual_p90_cm": cm(self.residual_p90_m),
            "residual_max_cm": cm(self.residual_max_m),
            "residual_max_nontree_cm": cm(self.residual_max_nontree_m),
            "n_nontree_edges": self.n_nontree_edges,
            "verified_frames": self.verified_frames,
            "unverified_frames": self.unverified_frames,
            "camera_height_dev_m": (None if not np.isfinite(self.camera_height_dev_m)
                                    else round(self.camera_height_dev_m, 3)),
            "dropped_implausible": self.dropped_implausible,
            "edges": [e.to_json() for e in self.edges],
            "scales": [round(float(s), 4) for s in self.scales],
            "gates": {"cycle_m": CYCLE_GATE_M, "pair_residual_m": MAX_PAIR_RESIDUAL_M,
                      "world_residual_m": WORLD_RESIDUAL_GATE_M,
                      "camera_height_dev_m": MAX_CAMERA_HEIGHT_DEV_M},
            "seconds": round(self.seconds, 1),
        }


def register_multiview(frames: list[Frame], pairs: Optional[list[PairMatch]] = None,
                       cycle_gate_m: float = CYCLE_GATE_M,
                       seed: int = 0) -> MultiviewRegistration:
    """photo -> SIFT -> pairwise transforms -> overlap graph -> cycle consistency ->
    reject unreliable edges -> largest reliable component -> poses.

    Unplaced frames get `None`, never identity: a frame dropped at the origin piles a whole
    room's geometry onto the reference camera and corrupts every plane fitted afterwards.
    """
    import time
    t0 = time.time()
    n = len(frames)
    if pairs is None:
        pairs = match_all(frames, seed=seed)
    pm_by_key = {(p.i, p.j): p for p in pairs}

    reports = filter_edges(pairs)
    cycle_stats = cycle_filter(reports, pm_by_key, gate_m=cycle_gate_m)
    alive = {k for k, r in reports.items() if r.status == "kept"}

    comps = connected_components(alive, n)
    reg = MultiviewRegistration(poses=[None] * n, scales=[1.0] * n, reference=0,
                                edges=list(reports.values()), components=comps,
                                cycle_stats=cycle_stats)
    if not comps:
        reg.unregistered = [{"frame": i, "reason": "no surviving edge to any other frame"}
                            for i in range(n)]
        reg.seconds = time.time() - t0
        return reg

    # Reasons are accumulated as they become known and read back at the end, so a frame that
    # drops out early still reports WHY it dropped rather than the generic reason that fits
    # whatever the graph looks like once every stage has finished with it.
    why_map: dict[int, str] = {}
    keep = set(comps[0])
    for i in range(n):
        if i in keep:
            continue
        if any(i in c for c in comps[1:]):
            other = next(c for c in comps[1:] if i in c)
            why_map[i] = (f"in a separate component {sorted(other)}, not connected to the "
                          f"main reconstruction after cycle gating")
        elif any(i in r.edge for r in reports.values() if r.status == "cycle"):
            why_map[i] = "its only edges were cut by cycle consistency"
        else:
            why_map[i] = "no usable edge to any other frame"

    # ------------------------------------------------------------------ stage 3: fundamental
    # cycles. Triangles alone do not cover these graphs: Room 2 has 7 edges and exactly ONE
    # triangle, and its 204 cm edge sits in no triangle at all, so a triangle-only test is
    # blind to it. But every edge the spanning tree does NOT use closes exactly one loop with
    # the tree path between its endpoints - the fundamental cycle of that edge - and that
    # loop's closure error is what `world_residual` measures once poses exist. So checking
    # every non-tree edge checks every remaining loop in the graph, triangle or not.
    #
    # Blame is spread over the whole cycle, because a failing loop does not say which of its
    # edges is wrong. Iterating resolves it: a genuinely bad edge fails every cycle it
    # touches and accumulates blame, while a good edge appears in one failing cycle and many
    # passing ones.
    support = np.zeros(n)
    for e in alive:
        support[e[0]] += reports[e].inliers
        support[e[1]] += reports[e].inliers
    ref = int(max(keep, key=lambda i: support[i]))
    reg.reference = ref
    cycle_stats["fundamental_cycles_cut"] = []

    for _ in range(len(alive) + 1):
        poses, scales, tree, parent = _compose(alive, reports, pm_by_key, ref, n)

        failing = []
        for e in sorted(alive - tree):
            if poses[e[0]] is None or poses[e[1]] is None:
                continue
            r = world_residual(pm_by_key[e], poses[e[0]], poses[e[1]])
            if np.isfinite(r) and r > WORLD_RESIDUAL_GATE_M:
                failing.append((r, e, _tree_cycle(e, parent, tree)))

        if not failing:
            reg.poses, reg.scales = poses, scales
            for e in tree:
                reports[e].in_tree = True
            break

        blame: dict[tuple[int, int], float] = {}
        for r, e, cyc in failing:
            for c in cyc:
                blame[c] = blame.get(c, 0.0) + (r - WORLD_RESIDUAL_GATE_M)
        worst = max(blame, key=lambda e: (blame[e], -reports[e].inliers))
        r_worst = max(r for r, e, cyc in failing if worst in cyc)
        reports[worst].status = "cycle"
        reports[worst].reason = (
            f"in {sum(1 for _, _, cyc in failing if worst in cyc)} failing fundamental "
            f"cycle(s), worst loop closure {r_worst * 100:.0f} cm over the "
            f"{WORLD_RESIDUAL_GATE_M * 100:.0f} cm gate")
        if not np.isfinite(reports[worst].cycle_error_m) or r_worst > reports[worst].cycle_error_m:
            reports[worst].cycle_error_m = r_worst
        cycle_stats["fundamental_cycles_cut"].append(
            {"edge": list(worst), "loop_closure_cm": round(r_worst * 100, 1)})
        alive.discard(worst)

        # Cutting an edge can split the graph, so components are recomputed rather than
        # assumed. A frame that loses its last good edge drops out here, not silently later.
        comps = connected_components(alive, n)
        if not comps:
            reg.unregistered = [{"frame": i, "reason": "every edge rejected"}
                                for i in range(n)]
            reg.edges = list(reports.values())
            reg.seconds = time.time() - t0
            return reg
        reg.components = comps
        keep = set(comps[0])
        if ref not in keep:
            ref = int(max(keep, key=lambda i: support[i]))
            reg.reference = ref
    else:
        reg.poses, reg.scales = poses, scales

    # ---------------------------------------------------------- stage 4: physical plausibility
    # Graph consistency cannot catch a frame placed through an edge that sits in no loop: its
    # pose is composed from that edge and then checked against it, which is circular and
    # always passes. One protocol-derived fact closes the hole - the operator walked the room
    # holding a phone, so the cameras share a height band. Frames outside it are dropped and
    # the poses recomposed, because a frame 2.7 m in the air drags a whole ceiling with it.
    for _ in range(n):
        placed = [i for i in range(n) if reg.poses[i] is not None]
        if len(placed) < 3:
            break
        # World frame is the reference camera's frame, so camera-up is -y and height is -ty.
        heights = {i: -float(reg.poses[i][1, 3]) for i in placed}
        med = float(np.median(list(heights.values())))
        dev = {i: abs(h - med) for i, h in heights.items()}
        worst = max(dev, key=dev.get)
        if dev[worst] <= MAX_CAMERA_HEIGHT_DEV_M:
            reg.camera_height_dev_m = dev[worst]
            break
        reg.dropped_implausible.append(
            {"frame": worst, "height_m": round(heights[worst], 3),
             "median_height_m": round(med, 3), "deviation_m": round(dev[worst], 3),
             "reason": f"camera placed {dev[worst]:.2f} m from the median camera height, "
                       f"over the {MAX_CAMERA_HEIGHT_DEV_M:.1f} m protocol band"})
        for e in [e for e in alive if worst in e]:
            reports[e].status = "implausible"
            reports[e].reason = f"frame {worst} placed at an impossible camera height"
            alive.discard(e)
        comps = connected_components(alive, n)
        reg.components = comps
        if not comps:
            reg.poses = [None] * n
            break
        keep = set(comps[0])
        if ref not in keep:
            ref = int(max(keep, key=lambda i: support[i]))
            reg.reference = ref
        reg.poses, reg.scales, tree, _p = _compose(alive, reports, pm_by_key, ref, n)
        for e in reports:
            reports[e].in_tree = e in tree

    for d in reg.dropped_implausible:
        why_map[d["frame"]] = (
            f"camera placed {d['deviation_m']:.2f} m from the median camera height - "
            f"physically impossible for one operator walking one room")
    reg.unregistered = [
        {"frame": i, "reason": why_map.get(
            i, ("reachable only through a frame dropped as implausible"
                if reg.dropped_implausible else
                "dropped from the reconstruction after cycle gating cut its edges"))}
        for i in range(n) if reg.poses[i] is None]

    # ------------------------------------------------------------------- stage 5: final report
    res, nontree = [], []
    for e in alive:
        if reg.poses[e[0]] is None or reg.poses[e[1]] is None:
            continue
        r = world_residual(pm_by_key[e], reg.poses[e[0]], reg.poses[e[1]])
        reports[e].world_residual_m = r
        if np.isfinite(r):
            res.append(r)
            if not reports[e].in_tree:
                nontree.append(r)
    if res:
        arr = np.array(res)
        reg.residual_median_m = float(np.median(arr))
        reg.residual_p90_m = float(np.percentile(arr, 90))
        reg.residual_max_m = float(arr.max())
    reg.n_nontree_edges = len(nontree)
    if nontree:
        reg.residual_max_nontree_m = float(max(nontree))

    # A frame is verified only if it lies on a loop: incident to a non-tree edge, or on the
    # tree path that non-tree edge closes. Everything else was placed but never cross-checked,
    # and the report says which is which rather than presenting them as equally established.
    _poses, _scales, tree, parent = _compose(alive, reports, pm_by_key, reg.reference, n)
    verified: set[int] = set()
    for e in alive - tree:
        if reg.poses[e[0]] is None or reg.poses[e[1]] is None:
            continue
        for c in _tree_cycle(e, parent, tree):
            verified.update(c)
    reg.verified_frames = sorted(verified)
    reg.unverified_frames = sorted(i for i in range(n)
                                   if reg.poses[i] is not None and i not in verified)

    reg.edges = list(reports.values())
    reg.seconds = time.time() - t0
    return reg


def _compose(alive: set, reports: dict, pm_by_key: dict, ref: int, n: int):
    """Spanning tree over the surviving edges, strongest first, and the poses it implies.

    Returns (poses, scales, tree_edges, parent) - `parent[b] = a` records which frame placed
    b, which is what makes the tree path between any two frames recoverable afterwards.
    """
    poses: list[Optional[np.ndarray]] = [None] * n
    scales = [1.0] * n
    parent: dict[int, int] = {}
    tree: set[tuple[int, int]] = set()
    poses[ref] = np.eye(4)

    order = sorted(alive, key=lambda e: -reports[e].inliers)
    changed = True
    while changed:
        changed = False
        for e in order:
            pm = pm_by_key[e]
            for a, b in (e, e[::-1]):
                if poses[a] is not None and poses[b] is None:
                    poses[b] = poses[a] @ edge_transform(pm, a, b)
                    scales[b] = scales[a] * (pm.scale if (a, b) == (pm.i, pm.j)
                                             else 1.0 / pm.scale)
                    parent[b] = a
                    tree.add(e)
                    changed = True
    return poses, scales, tree, parent


def _tree_cycle(edge: tuple[int, int], parent: dict[int, int],
                tree: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """The fundamental cycle of a non-tree edge: itself plus the tree path joining its ends."""
    def path_to_root(x: int) -> list[int]:
        out = [x]
        while x in parent:
            x = parent[x]
            out.append(x)
        return out

    pa, pb = path_to_root(edge[0]), path_to_root(edge[1])
    seen = {v: i for i, v in enumerate(pa)}
    meet = next((v for v in pb if v in seen), None)
    if meet is None:
        return [edge]
    chain = pa[:seen[meet] + 1] + list(reversed(pb[:pb.index(meet)]))
    cyc = [edge]
    for u, v in zip(chain, chain[1:]):
        e = (u, v) if (u, v) in tree else (v, u)
        if e in tree:
            cyc.append(e)
    return cyc


def apply_poses(frames: list[Frame], poses: list[Optional[np.ndarray]]) -> int:
    """Write recovered poses onto frames. Returns how many frames got one.

    Once `T_wc` is set, the existing fused path in `pipeline/measure.py` picks these frames up
    with no further change - the pipeline already branches on whether a frame carries a pose
    rather than on the tier, so registration is the only thing the photo tier was missing.
    """
    k = 0
    for f, T in zip(frames, poses):
        if T is not None:
            f.T_wc = T
            k += 1
    return k


def clear_poses(frames: list[Frame]) -> None:
    """Drop poses so the same frame objects can be measured again by another approach."""
    for f in frames:
        f.T_wc = None
