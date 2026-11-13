#!/usr/bin/env python3
"""thresholds_guard.py — refuse an unearned threshold change (M5 / r79).

`config/thresholds.json` has always DECLARED which fields may be calibrated,
which are locked, and that a loosening needs a ruling. Nothing enforced the
declaration: a `calibration_log` entry could name any field -- including a
locked criterion -- and still pass the old checks, because they only compared
`after` with the current value and looked for a log reference.

This guard makes the policy executable:

  * whitelist : the field must match a `calibratable` glob and no `locked`
                glob (locked wins; tightening is not an exception);
  * direction : each whitelisted pattern declares max / min / range in
                `calibration_direction`; the guard derives whether the change
                loosens or tightens and refuses a mislabelled `loosening`;
  * evidence  : `sample_n` (positive integer), `provenance` and a real
                `real_run_log_ref` (an existing non-empty file) are required;
                a loosening also needs `ruling_ref` and, per policy, external
                provenance;
  * modes     : a PROPOSAL is checked before the config changes (`before` must
                equal the current value); an ENTRY is checked after the fact
                (`after` must equal the current value), which is what
                aggregate_log.py runs over `calibration_log`.

Grade A / B / C is reported from provenance (external / internal / machine).
It is NOT a gate: no calibrated mapping from grade to truth exists, so the
gate stays structural. A grade-A entry is evidence a human can weigh, not a
certificate.
"""
import argparse
import fnmatch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import calibration_bench as CB

SCRIPT = "thresholds_guard.py"
RESULT_SCHEMA = "vector-ai-studio/thresholds-guard-v1"
NUM = (int, float)


def _get(th, field):
    cur = th
    for seg in field.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _matches(field, patterns):
    return [p for p in patterns if fnmatch.fnmatchcase(field, p)]


def _direction(th, field):
    table = (th.get("calibration_policy") or {}).get("calibration_direction") or {}
    for pat, d in table.items():
        if fnmatch.fnmatchcase(field, pat):
            return d
    return None


def _numeric(v):
    return isinstance(v, NUM) and not isinstance(v, bool)


def _resolve(ref, base):
    p = Path(ref)
    cands = [p] if p.is_absolute() else [(base or Path.cwd()) / p,
                                         L.skill_root() / p]
    return next((c for c in cands if c.is_file()), None)


def _effect(direction, before, after):
    """loosening / tightening / noop / mixed / invalid, from the direction map."""
    if direction in ("max", "min"):
        if not (_numeric(before) and _numeric(after)):
            return "invalid"
        if after == before:
            return "noop"
        if direction == "max":
            return "loosening" if after > before else "tightening"
        return "loosening" if after < before else "tightening"
    if direction == "range":
        if not (isinstance(before, (list, tuple)) and isinstance(after, (list, tuple))
                and len(before) == 2 and len(after) == 2
                and all(_numeric(v) for v in list(before) + list(after))):
            return "invalid"
        if list(after) == list(before):
            return "noop"
        lo_loosen, lo_tighten = after[0] < before[0], after[0] > before[0]
        hi_loosen, hi_tighten = after[1] > before[1], after[1] < before[1]
        if (lo_loosen or hi_loosen) and not (lo_tighten or hi_tighten):
            return "loosening"
        if (lo_tighten or hi_tighten) and not (lo_loosen or hi_loosen):
            return "tightening"
        return "mixed"
    return "invalid"


def check(th, doc, mode="proposal", base=None):
    """Return the guard document. `mode` is proposal or entry."""
    problems = []

    def add(code, msg):
        problems.append({"code": code, "message": msg})

    policy = th.get("calibration_policy") or {}
    field = str(doc.get("field") or "").strip()
    direction = None
    grade = None
    bench = None
    set_ref = str(doc.get("calibration_set") or "").strip()
    before = doc.get("before")
    after = doc.get("after")

    def result():
        return {
            "schema": RESULT_SCHEMA,
            "mode": mode,
            "field": field or None,
            "direction": direction,
            "loosening": (doc.get("loosening") if isinstance(doc.get("loosening"), bool)
                          else None),
            "sample_n": doc.get("sample_n"),
            "provenance": doc.get("provenance"),
            "evidence_grade": grade,
            "calibration_set": set_ref or None,
            "bench": bench,
            "problems": problems,
            "record": ({
                "field": field,
                "before": before,
                "after": after,
                "direction": direction,
                "loosening": doc.get("loosening"),
                "ruling_ref": doc.get("ruling_ref"),
                "real_run_log_ref": doc.get("real_run_log_ref"),
                "calibration_set": set_ref or None,
                "sample_n": doc.get("sample_n"),
                "provenance": doc.get("provenance"),
                "evidence_grade": grade,
                "bench_holdout": (bench or {}).get("holdout"),
            } if not problems else None),
        }

    if not field:
        add("E-CALIB-FIELD", "document has no field")
        return result()

    locked = _matches(field, policy.get("locked") or [])
    if locked:
        add("E-CALIB-LOCKED",
            "%s is a locked criterion (%s); no calibration is allowed, "
            "tightening included" % (field, ", ".join(locked)))
        return result()
    white = _matches(field, policy.get("calibratable") or [])
    if not white:
        add("E-CALIB-NOT-WHITELISTED",
            "%s matches no calibratable pattern" % field)
        return result()

    direction = _direction(th, field)
    if direction is None:
        add("E-CALIB-DIRECTION",
            "no calibration_direction entry matches %s" % field)

    current = _get(th, field)
    if current is None:
        add("E-CALIB-FIELD", "field not found in config: " + field)
    else:
        if mode == "proposal":
            if before is None:
                add("E-CALIB-STALE", "proposal has no before value")
            elif before != current:
                add("E-CALIB-STALE",
                    "before %r != current config %r" % (before, current))
            if after is None:
                add("E-CALIB-NOOP", "proposal has no after value")
            elif after == current:
                add("E-CALIB-NOOP", "after equals the current value; nothing to promote")
        else:
            if after is None:
                add("E-CALIB-STALE", "entry has no after value")
            elif after != current:
                add("E-CALIB-STALE",
                    "entry after %r != current config %r" % (after, current))
            if before is None:
                add("E-CALIB-STALE", "entry has no before value")
            elif before == after:
                add("E-CALIB-NOOP", "entry before equals after; nothing changed")

    eff = _effect(direction, before, after) if direction else "invalid"
    if eff == "invalid":
        add("E-CALIB-DIRECTION",
            "values do not fit direction %r: before=%r after=%r"
            % (direction, before, after))
    elif eff == "mixed":
        add("E-CALIB-DIRECTION",
            "interval change widens one bound and narrows the other; "
            "declare one direction or split the change")
    elif eff == "noop":
        add("E-CALIB-NOOP", "before and after are equal")
    else:
        declared = doc.get("loosening")
        if not isinstance(declared, bool):
            add("E-CALIB-DIRECTION", "loosening must be a JSON boolean")
        elif declared != (eff == "loosening"):
            add("E-CALIB-DIRECTION",
                "declared loosening=%s but direction %r says %s"
                % (declared, direction, eff))
        if declared is True and not str(doc.get("ruling_ref") or "").strip():
            add("E-CALIB-LOOSENING", "loosening without ruling_ref")
        if (declared is True and policy.get("loosening_requires_external")
                and doc.get("provenance") != "external"):
            add("E-CALIB-PROVENANCE",
                "policy requires external provenance for a loosening; got %r"
                % doc.get("provenance"))

    n = doc.get("sample_n")
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        add("E-CALIB-SAMPLE", "sample_n must be a positive integer; got %r" % (n,))

    provenance = doc.get("provenance")
    allowed = policy.get("evidence_provenance") or ["external", "internal", "machine"]
    if provenance not in allowed:
        add("E-CALIB-PROVENANCE",
            "provenance must be one of %s; got %r" % (allowed, provenance))
    else:
        grade = (policy.get("evidence_grade") or {}).get(provenance)

    ref = str(doc.get("real_run_log_ref") or "").strip()
    if not ref:
        add("E-CALIB-NO-LOG", "missing real_run_log_ref")
    else:
        hit = _resolve(ref, base)
        if hit is None or hit.stat().st_size == 0:
            add("E-CALIB-NO-LOG",
                "real_run_log_ref does not resolve to a non-empty file: " + ref)

    # r80 (M5 slice 2): paperwork is not evidence. A loosening must ship a
    # calibration set, and any supplied set must show a strict holdout gain.
    if set_ref:
        sp = _resolve(set_ref, base)
        if sp is None:
            add("E-CALIB-DATASET",
                "calibration_set does not resolve to a file: " + set_ref)
        else:
            try:
                setdoc = L.read_json(str(sp))
            except L.ToolError as e:
                add("E-CALIB-DATASET", "calibration_set unreadable: " + str(e))
            else:
                bench = CB.evaluate(setdoc, field, direction, before, after)
                problems.extend(bench.get("problems") or [])
                if (isinstance(n, int) and not isinstance(n, bool)
                        and bench.get("n") != n):
                    add("E-CALIB-SAMPLE",
                        "sample_n %d != calibration set size %d"
                        % (n, bench.get("n")))
    elif doc.get("loosening") is True and policy.get("requires_bench_for_loosening"):
        add("E-CALIB-NO-BENCH",
            "a loosening needs calibration_set with a holdout gain; "
            "paperwork alone does not show the gate improves")

    return result()


def main():
    ap = argparse.ArgumentParser(description="guard threshold promotions")
    ap.add_argument("--proposal", required=True,
                    help="proposed change JSON (field/before/after/...)")
    ap.add_argument("--thresholds", default="", help="thresholds.json path")
    ap.add_argument("--entry", action="store_true",
                    help="check an existing calibration_log entry (after == current)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    th = L.load_thresholds(args.thresholds or None)
    doc = L.read_json(args.proposal)
    res = check(th, doc, mode="entry" if args.entry else "proposal")
    ok = not res["problems"]
    errors = [L.err(p["code"], p["message"]) for p in res["problems"]]
    return L.emit(SCRIPT, status="ok" if ok else "fail",
                  code=L.EXIT_OK if ok else L.EXIT_FAIL,
                  data=res, errors=errors,
                  warnings=[] if res["evidence_grade"] in (None, "A") else
                  ["evidence grade %s: provenance is not external-human; "
                   "the grade is reported, never treated as proof"
                   % res["evidence_grade"]])


if __name__ == "__main__":
    sys.exit(main())
