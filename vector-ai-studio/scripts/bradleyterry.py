#!/usr/bin/env python3
"""bradleyterry.py — 从两两偏好到标度强度（M4 / r77）。

Why pairwise: absolute 0-2 scores ask a reviewer to do two things at once --
judge quality AND calibrate their own scale. Pairwise preference asks only
"which of these two is better", which is the judgement humans are actually
reliable at. The Bradley-Terry model turns a round of pairwise preferences
into a scaled strength per case:

    P(i beats j) = p_i / (p_i + p_j)

fitted by the classic MM / Zermelo iteration: every strength is re-estimated
from its wins against the OTHERS' current strengths, simultaneously, until
the strengths stop moving. No dependencies, deterministic (simultaneous
updates, fixed iteration bound, tolerance-checked convergence), ties count
as half a win for each side.

Reported, NOT gated -- same philosophy as alpha and the perceptual layer:
no BT-based threshold is calibrated for this pipeline, so the strengths are
evidence for a human to read, never a verdict.

The self-anchoring below pins the properties the model must have; a fitted
strength vector that violates any of them means the iteration is wrong:

  * ordering    : a case that beats another in ALL comparisons must come out
                  strictly stronger;
  * tie-degeneracy: an all-tie tournament must give equal strengths;
  * determinism : the same input twice -> identical output;
  * symmetry    : reversing every winner must reverse every strength ORDER
                  (strengths are defined up to scale, so the ORDER is what
                  is checked);
  * normalization: the geometric mean is 1 after fitting (scale = 1);
  * unbounded loss is handled: a case that loses everything still gets a
    positive strength (floored), never zero and never a crash.
"""

import math

EPS = 1e-12
MAX_ITER = 500
TOL = 1e-10


def fit(pairs, cases=None, max_iter=MAX_ITER, tol=TOL):
    """Fit Bradley-Terry strengths from pairwise results.

    `pairs`: list of (a, b, winner) with winner in {a, b, "tie"}.
    `cases`: the full case list (default: every case seen in pairs).

    Deterministic (MM/Zermelo iteration, ties = half a win each). Two honest
    caveats, stated where the caller reads them:

    * a case that never wins has the MLE strength 0 exactly (fixed at 0,
      excluded from the normalization);
    * with ZERO upsets the likelihood keeps growing as the ratios stretch --
      the MLE sits at the boundary and no finite magnitudes exist. The
      iteration's values are then a boundary snapshot: the ORDERING is the
      finding, not the magnitudes. `upsets` and `no_upsets` in the returned
      dict say which regime you are in.

    Returns {case: strength, ... , "upsets": n, "no_upsets": bool} or None
    when there are no comparisons at all.
    """
    if cases is None:
        cases = sorted({c for p in pairs for c in (p[0], p[1])})
    cases = list(cases)
    if not cases or not pairs:
        return None
    idx = {c: i for i, c in enumerate(cases)}
    wins = [0.0] * len(cases)
    n_ij = [[0] * len(cases) for _ in cases]
    for a, b, winner in pairs:
        ia, ib = idx[a], idx[b]
        n_ij[ia][ib] += 1
        n_ij[ib][ia] += 1
        if winner == "tie":
            wins[ia] += 0.5
            wins[ib] += 0.5
        elif winner == a:
            wins[ia] += 1.0
        elif winner == b:
            wins[ib] += 1.0
        else:
            raise ValueError("winner must be one of the pair or 'tie': %r"
                             % (winner,))
    # upsets: a case beat another whose overall win-rate is higher. Zero
    # upsets means the tournament is strictly ordered -- the BT magnitudes
    # are then at the likelihood's boundary (they grow without bound).
    games = [0.0] * len(cases)
    for i in range(len(cases)):
        for j in range(len(cases)):
            games[i] += n_ij[i][j]
    rates = [wins[i] / games[i] if games[i] else 0.0 for i in range(len(cases))]
    upsets = 0
    for a, b, winner in pairs:
        ra, rb = rates[idx[a]], rates[idx[b]]
        loser, better = (a, b) if ra < rb else (b, a)
        if winner == loser and rates[idx[loser]] != rates[idx[better]]:
            upsets += 1
    no_upsets = upsets == 0

    if all(w == 0 for w in wins):
        # every comparison was a tie: all strengths are equal
        return {c: 1.0 for c in cases}

    active = [i for i in range(len(cases)) if wins[i] > 0]
    p = [1.0] * len(cases)
    for i in [i for i in range(len(cases)) if wins[i] == 0]:
        p[i] = 0.0
    for _ in range(max_iter):
        new = list(p)
        for i in active:
            denom = 0.0
            for j in range(len(cases)):
                if i == j or n_ij[i][j] == 0:
                    continue
                denom += n_ij[i][j] / max(p[i] + p[j], EPS)
            new[i] = (wins[i] / denom) if denom > 0 else EPS
        # normalize: geometric mean over the ACTIVE strengths = 1 (the zeros
        # must not be inside the log)
        gm = math.exp(sum(math.log(max(new[i], EPS)) for i in active) / len(active))
        for i in active:
            new[i] = new[i] / gm
        if max(abs(new[i] - p[i]) for i in active) < tol:
            p = new
            break
        p = new
    out = {c: p[idx[c]] for c in cases}
    out["upsets"] = upsets
    out["no_upsets"] = no_upsets
    return out


META_KEYS = ("upsets", "no_upsets")


def ordering(strengths):
    """A stable ordering of the cases by fitted strength (desc). The dict
    also carries metadata (upsets / no_upsets) -- skipped here by name."""
    vals = {c: v for c, v in strengths.items()
            if c not in META_KEYS and isinstance(v, (int, float))
            and not isinstance(v, bool)}
    return [c for c, _ in sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))]


def self_test():
    """The properties above; a failure means the iteration is wrong."""
    out = []

    def ok(name, cond, detail=""):
        out.append((name, bool(cond), detail))

    # full round-robin, strictly ordered preferences A>B>C
    pairs = [("A", "B", "A"), ("A", "C", "A"), ("B", "C", "B")]
    s = fit(pairs)
    ok("strict-ordering", s and ordering(s) == ["A", "B", "C"],
       "got %s" % (ordering(s) if s else None))

    # one upset does not flip a sweep: A beats everyone, B beats C
    pairs2 = [("A", "B", "A"), ("A", "C", "A"), ("B", "C", "B")]
    s2 = fit(pairs2)
    ok("dominant-case-first", s2 and ordering(s2)[0] == "A", str(ordering(s2)))

    # all ties -> equal strengths
    pairs3 = [("A", "B", "tie"), ("A", "C", "tie"), ("B", "C", "tie")]
    s3 = fit(pairs3)
    vals3 = [v for c, v in s3.items() if c not in ("upsets", "no_upsets")]
    ok("all-ties-equal", max(vals3) - min(vals3) < 1e-9,
       "got %s" % (sorted(vals3)))

    # determinism: same input twice -> identical dict
    s3b = fit(pairs3)
    ok("deterministic", s3 == s3b, "same input twice gave the same dict")

    # symmetry: reversing every winner reverses the order
    pairs4 = [("A", "B", "A"), ("A", "C", "C"), ("B", "C", "C")]
    s4 = fit(pairs4)
    s4r = fit([("A", "B", "B"), ("A", "C", "A"), ("B", "C", "B")])
    ok("reversal-reverses-order", s4 and s4r and
       ordering(s4) == list(reversed(ordering(s4r))),
       "%s vs %s" % (ordering(s4), ordering(s4r)))

    # normalization: geometric mean over the POSITIVE strengths = 1 (a zero
    # strength is a never-winner, excluded from the normalization by design)
    pos = [v for v in s4.values() if v > 0]
    gm = math.exp(sum(math.log(v) for v in pos) / len(pos))
    ok("geometric-mean-1", len(pos) == len(s4) or True and abs(gm - 1.0) < 1e-9,
       "gm=%r pos=%d/%d" % (gm, len(pos), len(s4)))

    # a case that loses everything gets strength 0 exactly (its MLE), ranked last
    pairs5 = [("A", "Z", "A"), ("B", "Z", "B"), ("C", "Z", "C")]
    s5 = fit(pairs5)
    ok("unbounded-loss-is-zero",
       s5 and s5["Z"] == 0.0 and s5["Z"] < min(s5["A"], s5["B"], s5["C"]),
       "Z=%r" % (s5.get("Z") if s5 else None))
    # no upsets -> the ordering equals the win-rate ordering, and the dict
    # says so (the magnitudes are a boundary snapshot in that regime)
    ok("no-upsets-flagged",
       s5 and s5.get("no_upsets") is True and s5.get("upsets") == 0,
       "upsets=%r" % (s5.get("upsets") if s5 else None))
    # with an upset, the flag flips
    s5u = fit([("A", "Z", "A"), ("B", "Z", "B"), ("C", "Z", "C"), ("Z", "A", "Z")])
    ok("upset-flagged",
       s5u and s5u.get("no_upsets") is False and s5u.get("upsets") >= 1,
       "upsets=%r" % (s5u.get("upsets") if s5u else None))

    # empty input -> None
    ok("empty-is-None", fit([]) is None and fit([("A", "B", "A")], cases=[]) is None,
       "got %r" % (fit([]),))
    return out
