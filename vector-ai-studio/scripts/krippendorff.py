#!/usr/bin/env python3
"""krippendorff.py — 评分者间信度 α（M4 / r76）。公式取自已发表文献，锚点钉死。

Krippendorff's alpha: the chance-corrected inter-rater reliability for ANY
number of raters, any number of units, missing data allowed. For a review
process this is the honest version of "the reviewers agreed": exact-rate
(review.py's agreement.rate) counts only cells where EVERY reviewer wrote the
same integer; alpha corrects for the agreement chance alone would produce and
weights a 0-vs-2 disagreement more than 0-vs-1 (ordinal metric).

Formulas are from Krippendorff, "Computing Krippendorff's Alpha-Reliability"
(2011), and the implementation is PINNED to the paper's own worked examples
(the A33 pattern: a published number, not a feeling):

  * B  (2 observers, binary, n=20, o_01=4, n_0=14, n_1=6):  nominal 0.095
  * C  (4 observers, 12 units, values 1-5 reconstructed unit by unit from the
       paper's pairable-value table; n=40):
       nominal 0.743 · ordinal 0.815 · interval 0.849

The ordinal distance is the paper's cumulative weight
    δ(v, v') = Σ_{g=v}^{v'-1} (n_g + n_{g+1}) / 2
over the PAIRABLE value counts (an unused rank contributes 0 and does not
change α -- the paper inserts one to prove exactly that). The metric's scale
cancels between D_o and D_e, so the unscaled sum is used as-is.

α is REPORTED by review.py, not gated: no threshold on it is calibrated yet,
and an uncalibrated number used as a gate is noise painted red (the same
reason the perceptual layer measures without failing). What this module
guarantees is that the number, when quoted, is the published formula's.

This module never reads files and never writes one -- pure functions + anchors.
"""

import math

# The paper's worked examples, as units: one list of observed values per unit
# of analysis (missing values simply absent). These are DATA, not tunables.

# Section B: 2 observers, 10 units, binary. n_0=14, n_1=6, o_01=4.
ANCHOR_B_UNITS = [(0, 1), (1, 1), (0, 1), (0, 0), (0, 0),
                  (0, 1), (0, 0), (0, 0), (0, 1), (0, 0)]
ANCHOR_B_NOMINAL = 0.095        # paper: 1 - 19*4/(14*6) = 0.0952

# Section C/D: 4 observers, 12 units (7 of 48 values missing), values 1-5;
# the paper's D section relabels value 5 as rank 6 with an unused rank 5
# inserted -- ordinal alpha is unchanged by that, which is part of the pin.
ANCHOR_C_UNITS = [
    (1, 1, 1),            # u1
    (2, 2, 2, 3),         # u2
    (3, 3, 3, 3),         # u3
    (3, 3, 3, 3),         # u4
    (2, 2, 2, 2),         # u5
    (1, 2, 3, 4),         # u6
    (4, 4, 4, 4),         # u7
    (1, 1, 1, 2),         # u8
    (2, 2, 2, 2),         # u9
    (5, 5, 5),            # u10
    (1, 1),               # u11
    (3,),                 # u12: a lone value -- no pairs, contributes nothing
]
ANCHOR_C_NOMINAL = 0.743
ANCHOR_C_ORDINAL = 0.815
ANCHOR_C_INTERVAL = 0.849
ANCHOR_TOL = 5e-4               # the paper rounds to 3 decimals


def _pairable(units):
    """Values per unit for units that can produce pairs (n_u >= 2)."""
    return [list(u) for u in units if u is not None and len(u) >= 2]


def _counts(pairable):
    """Pairable value counts n_g over the pairable units (missing ranks = 0)."""
    out = {}
    for u in pairable:
        for v in u:
            out[v] = out.get(v, 0) + 1
    return out


def _delta_sq(v1, v2, counts, metric):
    """The paper's difference functions, squared. Scale cancels in alpha."""
    if v1 == v2:
        return 0.0
    lo, hi = min(v1, v2), max(v1, v2)
    if metric == "nominal":
        return 1.0
    if metric == "interval":
        return float((hi - lo) ** 2)
    if metric == "ordinal":
        s = 0.0
        for g in range(lo, hi):
            s += (counts.get(g, 0) + counts.get(g + 1, 0)) / 2.0
        return float(s) ** 2
    raise ValueError("unknown metric: " + str(metric))


def alpha(units, metric="ordinal"):
    """Krippendorff's alpha over `units` (one list of values per unit).

    Returns a float, or None when the data cannot produce a reliability
    figure (fewer than two pairable values anywhere). Perfect agreement is
    exactly 1.0; chance-level agreement is 0.0; systematic disagreement can
    go negative -- that is the coefficient telling the truth, not an error.
    """
    pair = _pairable(units)
    n_pairs_total = sum(len(u) for u in pair)
    if n_pairs_total < 2:
        return None
    counts = _counts(pair)
    values = sorted(counts)
    if len(values) < 2:
        return 1.0                     # every pairable value identical: D_o = 0
    # coincidence matrix entries o_ck (c < k), and marginals n_c
    o = {}
    for u in pair:
        nu = len(u)
        seen = {}
        for v in u:
            seen[v] = seen.get(v, 0) + 1
        for c in seen:
            for k in seen:
                if c >= k:
                    continue
                o[(c, k)] = o.get((c, k), 0.0) + seen[c] * seen[k] / (nu - 1.0)
    marg = counts                        # n_c over pairable units only
    n = sum(marg.values())

    num = 0.0
    den = 0.0
    for i, c in enumerate(values):
        for k in values[i + 1:]:
            d2 = _delta_sq(c, k, counts, metric)
            num += o.get((c, k), 0.0) * d2
            den += marg[c] * marg[k] * d2
    if den == 0:
        # every disagreement has zero distance under this metric: no
        # disagreement is measurable, the data is agreement by definition
        return 1.0
    return 1.0 - (n - 1.0) * num / den


def alpha_matrix(matrix, metric="ordinal"):
    """alpha over a raters-x-items matrix: `matrix[r][i]` (raters outer),
    missing ratings as None. Units are the ITEMS (raters are the observers)."""
    if not matrix:
        return None
    n_items = len(matrix[0])
    units = []
    for i in range(n_items):
        vals = [matrix[r][i] for r in range(len(matrix))
                if matrix[r][i] is not None]
        units.append(vals)
    return alpha(units, metric)


def self_test():
    """The published anchors + the properties the coefficient must have.

    Returns a list of (name, ok, detail). A failure here means the formula
    was mistyped -- anchors from a paper do not negotiate.
    """
    out = []
    a = alpha(ANCHOR_B_UNITS, "nominal")
    out.append(("anchor-B-nominal", a is not None and abs(a - ANCHOR_B_NOMINAL) < 1e-3,
                "got %r want %r" % (a, ANCHOR_B_NOMINAL)))
    for metric, want in (("nominal", ANCHOR_C_NOMINAL),
                         ("ordinal", ANCHOR_C_ORDINAL),
                         ("interval", ANCHOR_C_INTERVAL)):
        a = alpha(ANCHOR_C_UNITS, metric)
        out.append(("anchor-C-" + metric,
                    a is not None and abs(a - want) < ANCHOR_TOL,
                    "got %r want %r" % (a, want)))
    perfect = [(1, 1), (2, 2), (0, 0), (2, 2), (1, 1)]
    a = alpha(perfect, "ordinal")
    out.append(("perfect-agreement-is-1", a == 1.0, "got %r" % a))
    one_value = [(2, 2, 2), (2, 2), (2, 2, 2)]
    a = alpha(one_value, "ordinal")
    out.append(("single-value-is-1", a == 1.0, "got %r" % a))
    lone = [(1,), (2,), (3,)]
    a = alpha(lone, "ordinal")
    out.append(("lone-values-undefined", a is None, "got %r" % a))
    # inserting an unused rank does not move the ordinal alpha (paper §D)
    relabeled = [(1, 1, 1), (2, 2, 2, 3), (3, 3, 3, 3), (3, 3, 3, 3),
                 (2, 2, 2, 2), (1, 2, 3, 4), (4, 4, 4, 4), (1, 1, 1, 2),
                 (2, 2, 2, 2), (6, 6, 6), (1, 1), (3,)]
    a1 = alpha(ANCHOR_C_UNITS, "ordinal")
    a2 = alpha(relabeled, "ordinal")
    out.append(("unused-rank-invariant",
                a1 is not None and a2 is not None and abs(a1 - a2) < 1e-9,
                "orig %r relabeled %r" % (a1, a2)))
    # monotonicity: appending a perfectly-agreed unit never lowers alpha
    base = [(1, 2), (2, 2), (1, 1), (0, 2)]
    a_base = alpha(base, "ordinal")
    a_more = alpha(base + [(1, 1)], "ordinal")
    out.append(("agreeing-unit-never-lowers",
                a_base is not None and a_more is not None and a_more >= a_base - 1e-12,
                "base %r more %r" % (a_base, a_more)))
    # chance-ish data stays near 0, never 1
    noisy = [(1, 2), (2, 1), (1, 2), (2, 1), (1, 2), (2, 1)]
    a = alpha(noisy, "nominal")
    out.append(("systematic-disagreement-below-1", a is not None and a < 0.5,
                "got %r" % a))
    return out
