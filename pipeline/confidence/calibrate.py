"""Fit the interval error model from benchmark residuals, and score how well it is calibrated.

`intervals.py` builds every interval as `value +/- z * sigma(value, tier)` with
`sigma = sqrt((rel*value)^2 + abs^2 + (scale.sigma*value)^2)`. The `rel` and `abs` per tier
are not chosen - they are meant to be fitted on our own benchmark residuals, and then the fit
checked: does a nominal 95% interval actually contain the truth ~95% of the time.

Two functions, deliberately small:

  fit_error_model   given (value, truth) pairs per tier, least-squares fit of (rel, abs) so
                    that predicted sigma matches the observed residual spread. Linear in
                    (rel^2, abs^2), so it is one `np.linalg.lstsq`, no iteration.

  coverage          given (value, truth, sigma) triples, the fraction of truths that fall
                    inside `value +/- z*sigma` for each nominal confidence level. A calibrated
                    model has empirical coverage close to nominal. Reported, never adjusted
                    away - if the intervals under-cover because the error is mostly a shared
                    bias a symmetric band cannot chase, that is the finding.
"""
from __future__ import annotations

import math

import numpy as np

Z = {0.50: 0.674, 0.80: 1.282, 0.95: 1.960}


def residuals(pairs: list[tuple[float, float]]) -> dict:
    """(value, truth) pairs -> signed and absolute residual summary. Errors are relative to
    truth so tiers measuring different-sized things stay comparable."""
    if not pairs:
        return {"n": 0}
    v = np.array([p[0] for p in pairs], float)
    t = np.array([p[1] for p in pairs], float)
    rel = (v - t) / t
    return {
        "n": len(pairs),
        "bias_pct": float(np.mean(rel) * 100),
        "rms_pct": float(np.sqrt(np.mean(rel ** 2)) * 100),
        "abs_rms_m": float(np.sqrt(np.mean((v - t) ** 2))),
        "worst_pct": float(np.max(np.abs(rel)) * 100),
    }


def fit_error_model(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares (rel_sigma, abs_sigma) so that sqrt((rel*t)^2 + abs^2) tracks |value-truth|.

    r^2 = rel^2 * t^2 + abs^2  is linear in (rel^2, abs^2). Negative solutions are clamped to
    0 and the other term refit, because a variance cannot be negative.
    """
    if len(pairs) < 2:
        return (0.0, 0.0)
    t = np.array([p[1] for p in pairs], float)
    r2 = np.array([(p[0] - p[1]) ** 2 for p in pairs], float)
    A = np.column_stack([t ** 2, np.ones_like(t)])
    sol, *_ = np.linalg.lstsq(A, r2, rcond=None)
    rel2, abs2 = float(sol[0]), float(sol[1])
    if rel2 < 0:
        rel2, abs2 = 0.0, float(np.mean(r2))
    if abs2 < 0:
        abs2 = 0.0
        rel2 = float(np.mean(r2 / np.maximum(t ** 2, 1e-9)))
    return (math.sqrt(max(rel2, 0.0)), math.sqrt(max(abs2, 0.0)))


def coverage(triples: list[tuple[float, float, float]]) -> dict:
    """(value, truth, sigma) -> empirical coverage at each nominal level in Z.

    `sigma` is whatever the model under test produced for that measurement, so this scores the
    model as it actually runs, not a refit.
    """
    if not triples:
        return {"n": 0}
    out = {"n": len(triples)}
    for conf, z in Z.items():
        hit = sum(1 for v, t, s in triples if s > 0 and abs(v - t) <= z * s)
        out[f"cov_{int(conf * 100)}"] = hit / len(triples)
    return out
