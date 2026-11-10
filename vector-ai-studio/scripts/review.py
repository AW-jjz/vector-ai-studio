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
  * `evidence.usable_for_claim` is true only for >= 2 reviewers of kind
    `external` with zero empty cells

`--reviewer-kind machine` is accepted so a model-assisted pass can be recorded
honestly, but it can never be promoted to external evidence.

Scores are never produced here. This script reads what humans wrote.
"""
import argparse
import csv
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

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

    sheets, all_problems, reviewers = [], [], []
    for s in args.sheet:
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
        empty = blank == cells
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

    warnings = []
    if len(sheets) < cfg["min_reviewers_for_claim"]:
        warnings.append("only %d sheet(s); a claim needs at least %d"
                        % (len(sheets), cfg["min_reviewers_for_claim"]))
    if agreement < cfg["agreement_warn_below"]:
        warnings.append("exact agreement %.2f is below %.2f: the reviewers "
                        "disagree on the same drawings, read the dissent list "
                        "before quoting a mean"
                        % (agreement, cfg["agreement_warn_below"]))
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
                         "rate": agreement, "dissent": dissent},
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
