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
    kind `external` with zero empty cells. Sheets are de-duplicated by
    (absolute path, content sha256) BEFORE counting, so passing the same
    sheet twice can no longer fake a second reviewer (r70 #4).

`--reviewer-kind machine` is accepted so a model-assisted pass can be recorded
honestly, but it can never be promoted to external evidence.

Honest boundary: this de-duplication catches the same file (or identical
content) submitted twice. It CANNOT detect one person who saves the same
ratings under two different filenames with different content -- there is no
way for a script to tell two independent humans from one human with two
files. Do not describe this mechanism as making the evidence unforgeable.

Scores are never produced here. This script reads what humans wrote.
"""
import argparse
import csv
import hashlib
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import journal  # r76 (M4): canonical form for the preregistration hash
import krippendorff as KR  # r76 (M4): chance-corrected inter-rater reliability

SCRIPT = "review.py"
SUMMARY_SCHEMA = "vector-ai-studio/review-summary-v1"
KEY_SCHEMA = "vector-ai-studio/review-key-v1"
DEFAULT_DIMS = ["concept", "hierarchy", "proportion", "detail", "durability"]
NOTE_COL = "notes"


def _read_sheet(path, dims):
    """Return (rows, problems). rows: case -> {dim: int}."""
    problems = []
    p = Path(path)
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            rd = list(csv.reader(fh))
    except OSError as e:
        return {}, ["sheet unreadable: " + str(e)]
    if not rd:
        return {}, ["sheet is empty"]
    head = [h.strip() for h in rd[0]]
    if not head or head[0] != "case":
        return {}, ["first column must be `case`, got " + str(head[:1])]
    missing_cols = [d for d in dims if d not in head]
    if missing_cols:
        return {}, ["header is missing dimension(s): " + ", ".join(missing_cols)]
    idx = {d: head.index(d) for d in dims}
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
    return rows, problems


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
    # r70 #4: de-duplicate by (absolute path, content sha256). Passing the very
    # same review sheet twice used to count as TWO independent reviewers and
    # make usable_for_claim true (report P0-4). A repeat is not evidence: it is
    # an invalid submission, reported through the existing E-REVIEW-INVALID
    # branch. The key is content-based, so two different files with identical
    # content are also caught, not just the same filename twice.
    seen_sheets = {}
    for s in args.sheet:
        key = (str(Path(s).resolve()), L.file_sha256(s))
        if key in seen_sheets:
            all_problems.append(Path(s).name + ": duplicate review sheet "
                                "(same absolute path + content sha256 as " +
                                seen_sheets[key] + ")")
            # still read it so per-cell problems are listed, but do NOT append
            # to sheets/reviewers: it must not count toward the reviewer total.
            continue
        seen_sheets[key] = Path(s).name
        rows, problems = _read_sheet(s, dims)
        all_problems.extend(Path(s).name + ": " + p for p in problems)
        unknown = sorted(set(rows) - set(cases))
        absent = sorted(set(cases) - set(rows))
        all_problems.extend(Path(s).name + ": unknown case " + c for c in unknown)
        all_problems.extend(Path(s).name + ": case not scored " + c for c in absent)
        filled = sum(len(v) for v in rows.values())
        reviewers.append({"sheet": Path(s).name, "cells_filled": filled,
                          "cells_total": len(cases) * len(dims)})
        sheets.append(rows)

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
    if args.reviewer_kind != "external":
        warnings.append("reviewer kind is `%s`: usable as a record, never as "
                        "external evidence" % args.reviewer_kind)

    usable = (args.reviewer_kind == "external"
              and len(sheets) >= cfg["min_reviewers_for_claim"]
              and blank == 0)
    why = ("%d external reviewer(s), %d/%d cells filled"
           % (len(sheets), cells - blank, cells)) if usable else \
          ("not external evidence: " + "; ".join(warnings))
    doc = {"schema": SUMMARY_SCHEMA,
           "reviewer_kind": args.reviewer_kind,
           "reviewers": reviewers,
           "cases": per_case,
           "agreement": {"cells": total_cells, "exact": exact,
                         "rate": agreement, "dissent": dissent,
                         "krippendorff": alpha_doc},
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
