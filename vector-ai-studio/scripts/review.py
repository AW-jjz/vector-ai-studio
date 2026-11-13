#!/usr/bin/env python3
"""Turn a returned review sheet into evidence, or refuse to.

Why this exists: a blank review sheet is a file that looks like evidence. The
r48/r49 packs shipped three empty columns of scores, and "the sheet exists" was
the only thing anyone could check. An empty sheet, a half-filled sheet and a
sheet scored by the model itself are three different levels of nothing, and none
of them may be quoted as a design review.

So this script does the arithmetic a human should not have to redo, and refuses
the rest:

  * every cell must be present and be an integer 0-2 -- a missing cell is not a
    zero, and it is not a pass
  * agreement across reviewers is reported, never silently averaged away
  * `evidence.usable_for_claim` is true only for >= 2 DISTINCT reviewers of
    kind `external` with zero empty cells AND a verified reviewer identity
    (r78). Sheets are de-duplicated by CONTENT sha256 before counting, so the
    same ratings can no longer pose as a second reviewer by being copied to a
    new filename; each sheet may also carry a self-asserted `reviewer_token`,
    and one token may submit only one distinct sheet.

`--reviewer-kind machine` is accepted so a model-assisted pass can be recorded
honestly, but it can never be promoted to external evidence.

Honest boundary: the token is a pseudonym the reviewer writes into the sheet,
NOT a credential (H4 keeps this pipeline keyless). It survives renaming and
catches accidental double submission, but one person who invents two tokens is
still indistinguishable from two people. Do not describe this mechanism as
making the evidence unforgeable.

Scores are never produced here. This script reads what humans wrote.
"""
import argparse
import csv
import hashlib
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import journal  # r76 (M4): canonical form for the preregistration hash
import krippendorff as KR  # r76 (M4): chance-corrected inter-rater reliability
import bradleyterry as BT  # r77 (M4 slice 2): pairwise -> scaled strengths

SCRIPT = "review.py"
SUMMARY_SCHEMA = "vector-ai-studio/review-summary-v1"
KEY_SCHEMA = "vector-ai-studio/review-key-v1"
DEFAULT_DIMS = ["concept", "hierarchy", "proportion", "detail", "durability"]
NOTE_COL = "notes"
TOKEN_COL = "reviewer_token"
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


def _read_sheet(path, dims):
    """Return (rows, token, problems). rows: case -> {dim: int}; token: the
    sheet's self-asserted reviewer_token, or None for a legacy sheet."""
    problems = []
    p = Path(path)
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            rd = list(csv.reader(fh))
    except OSError as e:
        return {}, None, ["sheet unreadable: " + str(e)]
    if not rd:
        return {}, None, ["sheet is empty"]
    head = [h.strip() for h in rd[0]]
    if not head or head[0] != "case":
        return {}, None, ["first column must be `case`, got " + str(head[:1])]
    missing_cols = [d for d in dims if d not in head]
    if missing_cols:
        return {}, None, ["header is missing dimension(s): " + ", ".join(missing_cols)]
    idx = {d: head.index(d) for d in dims}
    token_idx = head.index(TOKEN_COL) if TOKEN_COL in head else None
    tokens = {}
    rows = {}
    for n, raw in enumerate(rd[1:], start=2):
        if not any(c.strip() for c in raw):
            continue
        if len(raw) < len(head):
            raw = raw + [""] * (len(head) - len(raw))
        case = raw[0].strip()
        if not case:
            problems.append("line %d: empty case id" % n)
            continue
        if case in rows:
            problems.append("line %d: duplicate case %s" % (n, case))
            continue
        if token_idx is not None:
            tokens.setdefault(raw[token_idx].strip(), []).append(n)
        vals = {}
        for d in dims:
            cell = raw[idx[d]].strip()
            if cell == "":
                problems.append("%s.%s is blank" % (case, d))
                continue
            try:
                v = int(cell)
            except ValueError:
                problems.append("%s.%s is not an integer: %r" % (case, d, cell))
                continue
            if v < 0 or v > 2:
                problems.append("%s.%s out of range 0-2: %d" % (case, d, v))
                continue
            vals[d] = v
        rows[case] = vals

    token = None
    if token_idx is not None:
        declared = sorted(t for t in tokens if t)
        blanks = tokens.get("", [])
        if len(declared) > 1:
            problems.append("%s declares more than one reviewer_token: %s"
                            % (TOKEN_COL, ", ".join(declared)))
        elif declared and blanks:
            problems.append("%s is blank on line(s) %s but declared %r on "
                            "others: one sheet, one identity"
                            % (TOKEN_COL, ",".join(str(n) for n in blanks),
                               declared[0]))
        elif declared:
            token = declared[0]
            if not TOKEN_RE.match(token):
                problems.append("reviewer_token %r must be 3-64 chars of "
                                "A-Z a-z 0-9 . _ -" % token)
                token = None
    return rows, token, problems


def _read_pairs(path, expected):
    """Parse one reviewer's review-pairs.csv -> (observations, problems).

    observations: (case_a, case_b, winner) with winner in {case_a, case_b,
    "tie"}. Every expected pair must be present exactly once; an unknown
    pair id, a swapped pair, an invalid winner or a duplicate is a problem.
    """
    problems = []
    obs = []
    p = Path(path)
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            rd = list(csv.reader(fh))
    except OSError as e:
        return [], ["pairs sheet unreadable: " + str(e)]
    if not rd:
        return [], ["pairs sheet is empty"]
    head = [h.strip() for h in rd[0]]
    if head[:4] != ["pair_id", "case_a", "case_b", "winner"]:
        return [], ["pairs header must be pair_id,case_a,case_b,winner; got " + str(head[:4])]
    seen = set()
    for n, raw in enumerate(rd[1:], start=2):
        if not any(c.strip() for c in raw):
            continue
        if len(raw) < 4:
            raw = raw + [""] * (4 - len(raw))
        pid, ca, cb, winner = (x.strip() for x in raw[:4])
        if pid not in expected:
            problems.append("line %d: unknown pair_id %r" % (n, pid))
            continue
        ea, eb = expected[pid]
        if (ca, cb) != (ea, eb):
            problems.append("line %d: %s lists (%s, %s) but the pack paired (%s, %s)"
                            % (n, pid, ca, cb, ea, eb))
            continue
        if pid in seen:
            problems.append("line %d: duplicate pair %s" % (n, pid))
            continue
        seen.add(pid)
        if winner == "tie":
            obs.append((ea, eb, "tie"))
        elif winner == ea:
            obs.append((ea, eb, ea))
        elif winner == eb:
            obs.append((ea, eb, eb))
        else:
            problems.append("line %d: %s winner must be %s, %s or tie; got %r"
                            % (n, pid, ea, eb, winner))
    for pid in expected:
        if pid not in seen:
            problems.append("pair %s not filled in" % pid)
    return obs, problems


def _verdict(dim_means, total_mean, cfg):
    lo, hi = cfg["total_conditional"], cfg["total_pass"]
    if total_mean >= hi and all(v >= cfg["min_dim_pass"] for v in dim_means):
        return "pass"
    if total_mean >= lo:
        return "conditional"
    return "fail"


def main():
    ap = argparse.ArgumentParser(description="validate and aggregate review sheets")
    ap.add_argument("--sheet", action="append", required=True,
                    help="returned review-sheet.csv, one per reviewer")
    ap.add_argument("--pairs", action="append", default=[],
                    help="returned review-pairs.csv, one per reviewer (r77)")
    ap.add_argument("--key", required=True, help="review key.json from --review-pack")
    ap.add_argument("--reviewer-kind", required=True,
                    choices=("external", "internal", "machine"))
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    th = L.load_thresholds()
    cfg = th.get("R")
    if not cfg:
        raise L.ToolError("E-SCHEMA", "thresholds.json has no R block",
                          L.EXIT_USAGE)
    dims = cfg.get("dims") or DEFAULT_DIMS
    key = L.read_json(args.key, expect_schema=KEY_SCHEMA)
    cases = [c["case"] for c in key.get("cases", [])]
    sources = {c["case"]: c.get("source", "") for c in key.get("cases", [])}
    if not cases:
        raise L.ToolError("E-SCHEMA", "review key lists no cases", L.EXIT_USAGE)

    # r76 (M4): PREREGISTRATION. The review protocol -- dimensions, scale,
    # case list, seed -- was fixed when --review-pack generated the key, and
    # its hash travels in the key itself. Verifying it here means a pack
    # edited after registration (a dimension renamed, a case swapped, the
    # scale widened) is refused with the drift as the headline: the criteria
    # cannot be quietly re-chosen after the results came back. A legacy pack
    # without a protocol block cannot be checked -- it is warned about below,
    # never silently accepted as preregistered.
    prereg = {"present": False, "sha256": None}
    proto = key.get("protocol")
    if proto is not None:
        prereg = {"present": True, "sha256": key.get("protocol_sha256")}
        want = "sha256:" + hashlib.sha256(journal.canonical(proto)).hexdigest()
        if key.get("protocol_sha256") != want:
            raise L.ToolError("E-REVIEW-INVALID",
                              "review pack protocol drifted after registration: the "
                              "key's protocol block no longer matches its registration "
                              "hash; regenerate the pack", L.EXIT_FAIL)
        if list(proto.get("dims") or []) != list(dims):
            raise L.ToolError("E-REVIEW-INVALID",
                              "preregistered dimensions %r differ from the scoring "
                              "dimensions %r" % (proto.get("dims"), list(dims)), L.EXIT_FAIL)
        if [c.get("case") for c in (proto.get("cases") or [])] != cases:
            raise L.ToolError("E-REVIEW-INVALID",
                              "preregistered cases differ from the key's cases", L.EXIT_FAIL)

    sheets, all_problems, reviewers = [], [], []
    # r70 #4 + r78: de-duplicate by CONTENT sha256 only. The old key also
    # carried the absolute path, so the very same ratings copied to a new
    # filename counted as a second reviewer -- the boundary r71 P0-2 declared.
    # Content is the identity of a submission: a repeat is not evidence, it is
    # an invalid submission through the existing E-REVIEW-INVALID branch.
    # A reviewer_token travels IN the sheet (r78): one token may submit one
    # distinct sheet; the same token with different content is the same person
    # submitting twice and is refused the same way.
    seen_sheets = {}
    seen_tokens = {}
    for s in args.sheet:
        content = L.file_sha256(s)
        if content in seen_sheets:
            all_problems.append(Path(s).name + ": duplicate review sheet "
                                "(identical content sha256 " + content[:12] +
                                " already submitted as " +
                                seen_sheets[content] + ")")
            # do NOT append to sheets/reviewers: it must not count toward the
            # reviewer total.
            continue
        seen_sheets[content] = Path(s).name
        rows, token, problems = _read_sheet(s, dims)
        all_problems.extend(Path(s).name + ": " + p for p in problems)
        unknown = sorted(set(rows) - set(cases))
        absent = sorted(set(cases) - set(rows))
        all_problems.extend(Path(s).name + ": unknown case " + c for c in unknown)
        all_problems.extend(Path(s).name + ": case not scored " + c for c in absent)
        if token and token in seen_tokens:
            all_problems.append(Path(s).name + ": reviewer_token %r already "
                                "used by %s with different content: one token, "
                                "one reviewer" % (token, seen_tokens[token]))
            continue
        if token:
            seen_tokens[token] = Path(s).name
        filled = sum(len(v) for v in rows.values())
        reviewers.append({"sheet": Path(s).name, "token": token,
                          "cells_filled": filled,
                          "cells_total": len(cases) * len(dims)})
        sheets.append(rows)

    # r77 (M4 slice 2): the PAIRWISE sheets. The expected pair list derives
    # from the pack's cases (full round-robin, the same ids the pack shipped);
    # problems here join the same refusal branch as sheet problems -- an
    # invalid preference is exactly as unusable as an invalid score.
    pair_expected = {}
    for i in range(len(cases)):
        for j in range(i + 1, len(cases)):
            pair_expected["pair-%02d-vs-%02d" % (i + 1, j + 1)] = (cases[i], cases[j])
    pair_obs, pair_sheets = [], []
    seen_pairs_files = {}
    for pf in args.pairs:
        pk = (str(Path(pf).resolve()), L.file_sha256(pf))
        if pk in seen_pairs_files:
            all_problems.append(Path(pf).name + ": duplicate pairs sheet "
                                "(same absolute path + content sha256 as " +
                                seen_pairs_files[pk] + ")")
            continue
        seen_pairs_files[pk] = Path(pf).name
        obs, probs_p = _read_pairs(pf, pair_expected)
        all_problems.extend(Path(pf).name + ": " + p for p in probs_p)
        pair_sheets.append(obs)
        pair_obs.extend(obs)

    cells = len(cases) * len(dims) * len(sheets)
    blank = sum(1 for r in sheets for c in cases
                for d in dims if d not in r.get(c, {}))
    if all_problems:
        # An empty sheet is a distinct, more specific diagnosis. A run where
        # every submission was a duplicate has no sheets at all (cells=0);
        # that is E-REVIEW-INVALID (duplicate/insufficient), NOT an empty sheet.
        empty = bool(sheets) and blank == cells
        code = "E-REVIEW-EMPTY" if empty else "E-REVIEW-INVALID"
        msg = ("every score cell is blank: an empty sheet is not evidence"
               if empty else
               "review sheet is not usable: " + "; ".join(all_problems[:6]))
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL,
                      run_id=args.run_id,
                      data={"reviewer_kind": args.reviewer_kind,
                            "reviewers": reviewers, "blank_cells": blank,
                            "cells": cells, "problems": all_problems[:40],
                            "evidence": {"usable_for_claim": False, "why": msg}},
                      errors=[L.err(code, msg)])

    per_case = []
    for case in cases:
        per_dim, means = {}, []
        for d in dims:
            vals = [r[case][d] for r in sheets]
            m = round(sum(vals) / float(len(vals)), 3)
            means.append(m)
            per_dim[d] = {"mean": m, "min": min(vals), "max": max(vals),
                          "spread": max(vals) - min(vals)}
        total_mean = round(sum(means), 3)
        per_case.append({"case": case, "source": sources.get(case, ""),
                         "per_dim": per_dim, "total_mean": total_mean,
                         "verdict": _verdict(means, total_mean, cfg)})

    total_cells = len(cases) * len(dims)
    exact = sum(1 for c in cases for d in dims
                if len({r[c][d] for r in sheets}) == 1)
    agreement = round(exact / float(total_cells), 3)
    dissent = [{"case": c, "dim": d,
                "values": [r[c][d] for r in sheets]}
               for c in cases for d in dims
               if len({r[c][d] for r in sheets}) > 1]

    # r76 (M4): Krippendorff's alpha over the rating matrix -- the
    # chance-corrected reliability the exact-rate above only gestures at
    # (exact-rate counts only unanimous cells and ignores the SIZE of a
    # disagreement: a 0-vs-2 split is not the same as a 0-vs-1 split, and the
    # ordinal metric says so). Metric = ordinal (the 0-2 scale is ordered);
    # the implementation is pinned to the published worked examples
    # (scripts/krippendorff.py, Krippendorff 2011: 0.743 nominal / 0.815
    # ordinal / 0.849 interval on his own data).
    #
    # REPORTED, NOT GATED: no threshold on alpha is calibrated for this
    # pipeline yet, and an uncalibrated number used as a gate is noise
    # painted red (the same reason the perceptual layer measures without
    # failing). The number is evidence for a human to quote or ignore.
    matrix = [[r[c][d] for c in cases for d in dims] for r in sheets]
    alpha_all = KR.alpha_matrix(matrix, "ordinal")
    per_dim_alpha = {}
    for d in dims:
        m = [[r[c][d] for c in cases] for r in sheets]
        per_dim_alpha[d] = KR.alpha_matrix(m, "ordinal")
    alpha_doc = {
        "metric": "ordinal",
        "alpha": round(alpha_all, 4) if alpha_all is not None else None,
        "per_dim": {d: (round(v, 4) if v is not None else None)
                    for d, v in per_dim_alpha.items()},
        "source": "Krippendorff, Computing Krippendorff's Alpha-Reliability "
                  "(2011); anchored in scripts/krippendorff.py",
        "gating": "reported only -- no calibrated alpha threshold; the "
                  "uncalibrated-exact-rate warning above is the gate",
    }

    # r77 (M4 slice 2): Bradley-Terry strengths from the pooled pairwise
    # preferences -- the scale the absolute 0-2 sheet cannot give (preferences
    # need no self-calibration). Deterministic MM/Zermelo iteration, ties
    # count half; REPORTED, NOT GATED (same reason as alpha: no calibrated
    # BT threshold exists for this pipeline).
    if pair_obs and not all_problems:
        strengths = BT.fit(pair_obs, cases=cases)
        bt_doc = ({"metric": "bradley-terry",
                   "comparisons": len(pair_obs),
                   "ties": sum(1 for o in pair_obs if o[2] == "tie"),
                   "strength": {c: round(v, 4) for c, v in strengths.items()
                                if c not in BT.META_KEYS},
                   "ordering": BT.ordering(strengths),
                   "upsets": strengths.get("upsets"),
                   "no_upsets": strengths.get("no_upsets"),
                   "pairs_sheets": len(pair_sheets),
                   "source": "MM/Zermelo iteration (scripts/bradleyterry.py)",
                   "gating": "reported only"} if strengths else
                  {"metric": "bradley-terry", "comparisons": len(pair_obs),
                   "gating": "reported only", "note": "not enough comparisons"})
    else:
        bt_doc = {"metric": "bradley-terry", "comparisons": 0,
                  "gating": "reported only",
                  "note": ("no pairwise sheets submitted; generate them with "
                           "scripts/studio.py --review-pack") if not args.pairs else
                          "pairwise sheets invalid -- see the refusal above"}
    no_pairs_submitted = not args.pairs

    # r78 (M4): reviewer identity. The token is a pseudonym written into the
    # sheet, not a credential; verified here means "every counted sheet
    # declared one, and no token was reused with different content". It is
    # required for claim-grade evidence: a duplicate with a new filename or a
    # resubmission under the same token is no longer countable, and a legacy
    # sheet without a token still aggregates but cannot be quoted as external
    # evidence.
    identity = {
        "column": TOKEN_COL,
        "declared": len(seen_tokens),
        "sheets": [{"sheet": r["sheet"], "token": r.get("token")}
                   for r in reviewers],
        "missing": [r["sheet"] for r in reviewers if not r.get("token")],
    }
    identity["verified"] = (bool(sheets) and not identity["missing"]
                            and len(seen_tokens) == len(sheets))

    warnings = []
    if len(sheets) < cfg["min_reviewers_for_claim"]:
        warnings.append("only %d sheet(s); a claim needs at least %d"
                        % (len(sheets), cfg["min_reviewers_for_claim"]))
    if agreement < cfg["agreement_warn_below"]:
        warnings.append("exact agreement %.2f is below %.2f: the reviewers "
                        "disagree on the same drawings, read the dissent list "
                        "before quoting a mean"
                        % (agreement, cfg["agreement_warn_below"]))
    if not prereg["present"]:
        warnings.append("review pack has no preregistered protocol block: the "
                        "review criteria cannot be checked against what the "
                        "pack promised before the reviews came back")
    if no_pairs_submitted:
        warnings.append("no pairwise sheet submitted: BT strengths unavailable, "
                        "absolute-only review stays as-is")
    if not identity["verified"]:
        warnings.append("reviewer identity is not token-verified (%d/%d counted "
                        "sheet(s) declare %s): identity is filename-only, so "
                        "this is not claim-grade external evidence"
                        % (identity["declared"], len(sheets), TOKEN_COL))
    if args.reviewer_kind != "external":
        warnings.append("reviewer kind is `%s`: usable as a record, never as "
                        "external evidence" % args.reviewer_kind)

    usable = (args.reviewer_kind == "external"
              and len(sheets) >= cfg["min_reviewers_for_claim"]
              and blank == 0
              and identity["verified"])
    why = ("%d external reviewer(s) with distinct %s, %d/%d cells filled"
           % (len(sheets), TOKEN_COL, cells - blank, cells)) if usable else \
          ("not external evidence: " + "; ".join(warnings))
    doc = {"schema": SUMMARY_SCHEMA,
           "reviewer_kind": args.reviewer_kind,
           "reviewers": reviewers,
           "identity": identity,
           "cases": per_case,
           "agreement": {"cells": total_cells, "exact": exact,
                         "rate": agreement, "dissent": dissent,
                         "krippendorff": alpha_doc},
           "pairwise": bt_doc,
           "protocol": prereg,
           "blank_cells": blank,
           "thresholds": {"total_pass": cfg["total_pass"],
                          "total_conditional": cfg["total_conditional"],
                          "min_dim_pass": cfg["min_dim_pass"],
                          "min_reviewers_for_claim": cfg["min_reviewers_for_claim"]},
           "evidence": {"usable_for_claim": bool(usable), "why": why},
           "note": "scores are read from human sheets; this script never writes one"}
    if args.out:
        L.write_json_atomic(Path(args.out), doc)

    fails = [c["case"] for c in per_case if c["verdict"] == "fail"]
    return L.emit(SCRIPT, status="ok",
                  code=L.EXIT_OK if not fails else L.EXIT_FAIL,
                  run_id=args.run_id, data=doc, warnings=warnings,
                  artifacts={"review_summary": Path(args.out).name} if args.out else {})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
