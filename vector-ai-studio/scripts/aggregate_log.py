#!/usr/bin/env python3
"""H2 / grading / O2 / O3 / generation budget / F data isolation.

Grading is a mechanical numeric comparison driven by config, never declared by
the LLM. Handles the L2 capability-missing status (no third grading tier), the
semi-manual COM branch, strict fallback-PNG H2 counting and runtime-derived
generation budget. acceptance.json puts headline on the first line.

--validate-case runs the acceptance-log validator against a fixture sample so
run_fixtures.py can assert every E-* rejection code mechanically.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "aggregate_log.py"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# Design S4.5: every graded log must carry these dims. E is optional because
# "no style reference" legitimately records it as N/A.
REQUIRED_DIMS = ("A", "B", "C", "D", "F")
# Words that would claim a pass / excellent / perfect result in L2 output.
BANNED_CLAIM_WORDS = ("达标", "优秀", "完美", "Excellent")


def _png_info(p):
    """Real parseable PNG: non-zero, magic, IHDR width/height, IEND present."""
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if len(data) < 24 or data[:8] != PNG_MAGIC or data[12:16] != b"IHDR":
        return None
    w = struct.unpack(">I", data[16:20])[0]
    h = struct.unpack(">I", data[20:24])[0]
    if w <= 0 or h <= 0 or data[-8:-4] != b"IEND":
        return None
    return {"width": w, "height": h, "bytes": len(data)}


def _load_optional(path):
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _get_path(obj, dotted):
    cur = obj
    for seg in dotted.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _calibration_check(th):
    problems = []
    for rec in th.get("calibration_log", []) or []:
        field = str(rec.get("field", ""))
        if not rec.get("real_run_log_ref"):
            problems.append(L.err("E-CALIB-NO-LOG", "calibration_log entry lacks real_run_log_ref: " + field))
        cur = _get_path(th, field)
        if cur is not None and "after" in rec and cur != rec["after"]:
            problems.append(L.err("E-CALIB-MISMATCH", "calibration_log after != current config: " + field))
        if rec.get("loosening") and not rec.get("ruling_ref"):
            problems.append(L.err("E-CALIB-LOOSENING", "loosening without ruling_ref: " + field))
    return problems


def _collect_scores(run_dir):
    rounds = []
    latest = None
    for p in sorted(run_dir.glob("round-*/score.json")):
        d = _load_optional(p)
        if isinstance(d, dict):
            rounds.append({"file": p.parent.name + "/score.json",
                           "round": d.get("round"), "failed_dims": d.get("failed_dims", []),
                           "l2_degraded": d.get("l2_degraded", False)})
            latest = d
    d = _load_optional(run_dir / "score.json")
    if isinstance(d, dict):
        latest = d
        if not rounds:
            rounds.append({"file": "score.json", "round": d.get("round"),
                           "failed_dims": d.get("failed_dims", []),
                           "l2_degraded": d.get("l2_degraded", False)})
    return latest, rounds


def _template_note(th, values):
    tpl = (L.templates_dir() / "delivery-note.template.md").read_text(encoding="utf-8")
    for k, v in values.items():
        tpl = tpl.replace("@@" + k + "@@", str(v))
    return tpl


def _isolation_problems(a):
    """O4: C2 revisions must never be backfilled into another run's F stats.

    Also forbids the same SVG hash appearing both in c2_revisions and in the
    F source statistics (a shared-generation shortcut).
    """
    fs = a.get("f_source_runs", []) or []
    c2 = a.get("c2_revisions", []) or []
    fs_ids = [str(r.get("run_id", "")) for r in fs if isinstance(r, dict)]
    problems = []
    for r in c2:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("run_id", ""))
        if rid not in fs_ids:
            problems.append("c2_revisions entry has no matching f_source run: " + rid)
        f_run = r.get("f_run_id")
        if f_run is not None and str(f_run) != rid:
            problems.append("cross-run F backfill: " + rid + " -> " + str(f_run))
        if r.get("score") is not None and r.get("counted_in_f") is True:
            problems.append("C2 revision counted in F statistics: " + rid)
    c2_hashes = {str(r.get("draft_sha256")) for r in c2
                 if isinstance(r, dict) and r.get("draft_sha256")}
    for r in fs:
        if isinstance(r, dict) and str(r.get("draft_sha256", "")) in c2_hashes:
            problems.append("same SVG hash in c2_revisions and F source statistics")
    if a.get("f_data_isolated") is False:
        problems.append("f_data_isolated flag is false")
    return problems


def _validate_acceptance(a, th):
    """Mechanical acceptance-log validation. Returns E-* problems.

    Two evidence tracks exist and must never be confused:

    * ``mode="full"``          -> ``grading_entered=true``; H1-H4 all judged,
                                 ``h2`` is a bool and ``grade`` is one of two tiers.
    * ``mode="landing-only"``  -> ``grading_entered=false``; H2/H3/H4 are not
                                 evaluated (``null``), ``h2`` is ``null`` and
                                 ``grade`` is ``null``. This track exists only to
                                 capture H1 on a machine where the pure-script
                                 layer was not run in the same directory.

    The exemption is a hole unless it is policed from both sides, so both
    directions are rejected below (E-EXEMPTION-ABUSE).
    """
    msg = th["messages"]
    budget = L.generation_budget(th)
    cap = L.capability_missing_value(th)
    problems = []

    # ---- evidence-track gate (checked before anything else) ----
    # A log written before this field existed is read as the full chain, so old
    # fixtures keep their original rejection code instead of tripping the new gate.
    mode = a.get("mode", "full")
    entered = a.get("grading_entered", True)
    if "mode" in a and mode not in ("full", "landing-only"):
        problems.append(L.err("E-EXEMPTION-ABUSE",
                              "mode must be 'full' or 'landing-only', got " + repr(mode)))
    if entered is False:
        # landing-only track: no tier word, no bool gate, no score may be claimed.
        if mode != "landing-only":
            problems.append(L.err("E-EXEMPTION-ABUSE",
                                  "grading_entered=false is only legal with mode=landing-only"))
        if a.get("h2") is not None:
            problems.append(L.err("E-EXEMPTION-ABUSE",
                                  "landing-only must carry h2=null, got " + repr(a.get("h2"))))
        if a.get("grade") is not None:
            problems.append(L.err("E-EXEMPTION-ABUSE",
                                  "landing-only must carry grade=null, got " + repr(a.get("grade"))))
        if a.get("tier") is not None:
            problems.append(L.err("E-EXEMPTION-ABUSE",
                                  "landing-only must not carry a tier, got " + repr(a.get("tier"))))
        for g in ("H2", "H3", "H4"):
            if (a.get("hard_gates", {}) or {}).get(g, {}).get("pass") is not None:
                problems.append(L.err("E-EXEMPTION-ABUSE",
                                      "landing-only must leave " + g + " unevaluated"))
        blob = json.dumps(a, ensure_ascii=False)
        if any(w in blob for w in BANNED_CLAIM_WORDS + ("未验证",)):
            problems.append(L.err("E-EXEMPTION-ABUSE",
                                  "landing-only output must not claim pass/excellent/未验证"))
        return problems
    if entered is not True:
        problems.append(L.err("E-EXEMPTION-ABUSE",
                              "grading_entered must be a bool, got " + repr(entered)))
        return problems
    if mode == "landing-only":
        problems.append(L.err("E-EXEMPTION-ABUSE",
                              "mode=landing-only must set grading_entered=false"))
    if "h2" in a and not isinstance(a.get("h2"), bool):
        problems.append(L.err("E-EXEMPTION-ABUSE",
                              "full mode must carry a bool h2, got " + repr(a.get("h2"))))

    gc = a.get("generation_count")
    if not isinstance(gc, int) or isinstance(gc, bool) or gc < 0:
        problems.append(L.err("E-BUDGET-EXCEEDED", "generation_count missing or invalid"))
    else:
        limit = budget["pass"] if a.get("grade") == msg["grade_pass"] else budget["excellent"]
        if gc > limit or gc > budget["excellent"]:
            problems.append(L.err("E-BUDGET-EXCEEDED",
                                  "generation_count " + str(gc) + " exceeds derived upper bound " + str(budget)))

    iso = _isolation_problems(a)
    if iso:
        problems.append(L.err("E-F-DATA-ISOLATION", "; ".join(iso)))

    sc = a.get("scores") or {}
    # Design S4.5: a graded (full-track) log must carry the whole required dim
    # set. Checking only C and D was not enough: dropping A, B or F produced a
    # silently accepted log, and dropping F is exactly the P0-1 shape (the
    # broken chain that made 达标 unreachable). An incomplete log is a broken
    # chain, not a degradation, and must never pass quietly.
    if "mode" in a:
        missing_dims = [d for d in REQUIRED_DIMS if d not in sc]
        if missing_dims:
            problems.append(L.err("E-NO-SCORE",
                                  "graded log is missing required dims: " +
                                  ",".join(missing_dims) +
                                  " (run score.py and batch_check.py first)"))
        # A pass claim carries a stricter requirement than a graded log: E may
        # be absent only because E was not applicable, and an applicable E
        # scored 0 must not be hidable by deleting the key. Without this, a
        # hand-edited log claiming 达标 with the E key removed passed quietly.
        if a.get("grade") == msg["grade_pass"]:
            if "E" not in sc:
                problems.append(L.err("E-NO-SCORE",
                                      "a pass claim must carry E (N/A or a scored dict); "
                                      "deleting E hides an applicable E=0"))
            for d in ("A", "B", "C", "D"):
                if not isinstance(sc.get(d), dict):
                    problems.append(L.err("E-NO-SCORE",
                                          "a pass claim requires a scored dict for dim " + d))
    is_l2 = (a.get("reason") == msg["reason_capability_missing"] or
             a.get("capability_missing") is True or
             sc.get("A") == cap or sc.get("B") == cap)
    if is_l2:
        for d in ("A", "B"):
            v = sc.get(d)
            if v != cap:
                problems.append(L.err("E-FABRICATED-SCORE",
                                      "L2 " + d + " must be capability_missing status, not a numeric score"))
        if a.get("grade") != msg["grade_fail"]:
            problems.append(L.err("E-L2-GRADE", "L2 grade must be not-pass (no third tier)"))
        if a.get("degraded") is not True or a.get("needs_human_review") is not True:
            problems.append(L.err("E-L2-FIELDS", "L2 requires degraded and needs_human_review true"))
        if a.get("headline") != msg["l2_headline"]:
            problems.append(L.err("E-L2-HEADLINE", "L2 headline must be the requirements text"))
        blob = json.dumps(a, ensure_ascii=False).replace(msg["grade_fail"], "")
        if any(w in blob for w in BANNED_CLAIM_WORDS):
            problems.append(L.err("E-L2-WORDING", "L2 output must not claim pass / excellent / perfect"))

    if a.get("quick") is True or a.get("f_verified") is False:
        if a.get("grade") == msg["grade_pass"] or a.get("grade") == "优秀":
            problems.append(L.err("E-QUICK-UNVERIFIED",
                                  "unverified F (quick mode) must not claim a pass"))
    return problems


def main():
    ap = argparse.ArgumentParser(description="aggregate acceptance log")
    ap.add_argument("--run-dir", default="")
    ap.add_argument("--validate-case", default="")
    ap.add_argument("--thresholds", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--selftest-log", default="",
                    help="explicit path to the selftest.json that proves H3")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--landing-only", action="store_true",
                    help="H1 evidence track: skip H2/H3/H4 and do not enter grading")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    th = L.load_thresholds(args.thresholds or None)
    msg = th["messages"]
    lv = th["score_levels"]
    cap = L.capability_missing_value(th)
    mode = "landing-only" if args.landing_only else "full"
    grading_entered = mode == "full"

    # Standalone validator mode used by the fixtures regression suite.
    if args.validate_case:
        case = L.read_json(args.validate_case)
        problems = _validate_acceptance(case, th)
        return L.emit(SCRIPT, status="ok" if not problems else "fail",
                      code=L.EXIT_OK if not problems else L.EXIT_FAIL,
                      run_id=args.run_id,
                      data={"case": Path(args.validate_case).name,
                            "generation_budget": L.generation_budget(th),
                            "problems": [p["code"] for p in problems]},
                      errors=problems)

    run_dir = Path(args.run_dir) if args.run_dir else None
    if run_dir is None or not run_dir.is_dir():
        raise L.ToolError("E-SCHEMA", "run-dir not found or not given", L.EXIT_USAGE)

    score_doc, rounds = _collect_scores(run_dir)
    scores = (score_doc or {}).get("scores", {})
    degraded_dims = list((score_doc or {}).get("degraded_dims", []))
    l2 = (bool((score_doc or {}).get("l2_degraded")) or
          bool((score_doc or {}).get("capability_missing")) or
          "A" in degraded_dims or "B" in degraded_dims)

    # ---- H1: real-machine structure readback ----
    sr = _load_optional(run_dir / "structure_readback.json")
    final_ai = run_dir / "final.ai"
    ai_ok = final_ai.is_file() and final_ai.stat().st_size > 0
    h1_ok, h1_status, h1_reason = False, "unverified", "no Illustrator saveAs readback in this run"
    if isinstance(sr, dict) and ai_ok:
        raster, placed, paths = sr.get("raster"), sr.get("placed"), sr.get("paths")
        dims_ok = sr.get("artboard_matches_viewBox") is True
        h1_ok = (raster == 0 and placed == 0 and isinstance(paths, int)
                 and paths > 0 and dims_ok and sr.get("ok") is True)
        h1_status = "pass" if h1_ok else "fail"
        h1_reason = "saveAs readback: raster/placed=0, paths>0, artboard==viewBox"

    # ---- H2: four artifacts, strict real-PNG rule ----
    final_svg = run_dir / "final.svg"
    preview = run_dir / "preview.png"
    acceptance_path = Path(args.out) if args.out else (run_dir / "acceptance.json")
    png = _png_info(preview) if preview.is_file() else None
    preview_meta = _load_optional(run_dir / "preview.meta.json") or {}
    renderer = preview_meta.get("renderer", "unknown")
    fallback = renderer == "builtin-fallback"
    missing = []
    if not ai_ok:
        missing.append("final.ai")
    if not final_svg.is_file() or final_svg.stat().st_size == 0:
        missing.append("final.svg")
    if png is None:
        missing.append("preview.png (missing, zero-byte, or not a real parseable PNG)")
    h2_ok = len(missing) == 0
    h2_status = "pass" if h2_ok else "fail"
    degradation = []
    if fallback and png is not None:
        degradation.append({"level": "L4", "reason": "PNG renderer candidates MISSING, builtin fallback used",
                            "renderer": "builtin-fallback", "note": msg["fallback_png_note"]})
    # A fallback PNG cannot support A/B image reading -> L2 status, never faked scores.
    # Fail-closed: a fallback preview that does not positively declare itself
    # "faithful" counts as limited, so omitting preview.meta.json.fidelity can
    # never buy a free pass on semantic / composition scoring.
    judge = _load_optional(run_dir / "judge.json")
    fidelity = str(preview_meta.get("fidelity", "") or "").lower()
    judge_unreadable = not (isinstance(judge, dict) and judge.get("readable") is True)
    if grading_entered and fallback and png is not None and (
            fidelity != "faithful" or judge_unreadable):
        if "A" not in degraded_dims:
            degraded_dims.append("A")
        if "B" not in degraded_dims:
            degraded_dims.append("B")
        l2 = True

    # ---- H3 / H4 ----
    # P0-2: selftest.py writes <log-dir>/selftest.json, and the log-dir is not the
    # run dir unless the caller says so. Look where the tools actually write, and
    # require the log to be internally consistent before calling H3 a pass.
    h3_source = ""
    selftest_log = None
    for cand in ([Path(args.selftest_log)] if args.selftest_log else []) + [
            run_dir / "selftest.json",
            run_dir / "selftest" / "selftest.json",
            run_dir / "logs" / "selftest.json"]:
        if cand.is_file():
            d = _load_optional(cand)
            if isinstance(d, dict):
                selftest_log, h3_source = d, str(cand)
                break
    h3_status = "unverified"
    h3_reason = "no selftest.json next to the run artifacts"
    # Assertions that legitimately may not run on a given machine. A skip in
    # anything NOT listed here means a guard silently did not run, which must
    # not be able to pass as H3. Keep this list explicit: a policy that lives
    # inside a counting formula cannot be reviewed.
    #
    # Registration condition: the capability itself is optional (an engine or an
    # external binary that may legitimately be absent). Never add an assertion
    # here to make a run go green -- that turns a guard into decoration.
    OPTIONAL_ASSERTIONS = ("A30-vectorcraft-chain-h1-h2",
                           "A31-deliverable-schema-no-drift")
    if isinstance(selftest_log, dict):
        n_pass = selftest_log.get("passed")
        n_fail = selftest_log.get("failed")
        n_all = len(selftest_log.get("assertions") or [])
        skipped_ids = list(selftest_log.get("skipped") or [])
        n_skip = len(skipped_ids)
        # The old formula (pass + fail == all) predates skips: with an optional
        # engine absent it read 29 + 0 != 31 and shut the gate regardless of
        # status. A count that cannot express "not evaluated" will always
        # misjudge one.
        consistent = (isinstance(n_pass, int) and isinstance(n_fail, int)
                      and n_pass + n_fail + n_skip == n_all)
        optional_only = all(x in OPTIONAL_ASSERTIONS for x in skipped_ids)
        status_ok = ((selftest_log.get("status") == "ok" and n_skip == 0) or
                     (selftest_log.get("status") == "partial" and optional_only))
        if status_ok and consistent and n_fail == 0 and n_pass >= 1:
            h3_status = "pass"
            h3_reason = ("selftest " + str(n_pass) + "/" + str(n_all) +
                         (" (+" + str(n_skip) + " optional skipped)" if n_skip else "") +
                         " from " + Path(h3_source).name)
        else:
            h3_status = "fail"
            why = ""
            if not status_ok and selftest_log.get("status") == "partial":
                why = " skipped=" + ",".join(x for x in skipped_ids
                                             if x not in OPTIONAL_ASSERTIONS) + " (not optional)"
            h3_reason = ("selftest log present but not a clean pass: status=" +
                         str(selftest_log.get("status")) + " passed=" + str(n_pass) +
                         " failed=" + str(n_fail) + " skipped=" + str(n_skip) +
                         " assertions=" + str(n_all) + why)
    creds = L.assert_no_credentials()
    h4_ok, h4_status = True, "pass"  # default chain never reads credential values

    # ---- evidence track: landing-only judges H1 and nothing else ----
    if not grading_entered:
        h2_ok, h2_status = None, "not_evaluated"
        h3_status = "not_evaluated"
        h3_reason = "landing-only track does not evaluate H3"
        h4_ok, h4_status = None, "not_evaluated"
        degradation = []

    # ---- F verification, generation budget, O4 data isolation ----
    batch = _load_optional(run_dir / "batch.json") or {}
    per_run = batch.get("per_run", []) if isinstance(batch, dict) else []
    f_verified = False
    if not args.quick and isinstance(batch, dict):
        f_verified = (batch.get("f_verified") is True and
                      batch.get("f_data_isolated") is True and
                      len(per_run) >= th["F"]["N_pass"])
    generation_count = batch.get("generation_count", 0) if isinstance(batch, dict) else 0
    if not isinstance(generation_count, int):
        generation_count = 0
    generation_budget = L.generation_budget(th)
    f_source_runs = batch.get("f_source_runs", []) if isinstance(batch, dict) else []
    c2_revisions = batch.get("c2_revisions", []) if isinstance(batch, dict) else []
    iso_probe = {"f_source_runs": f_source_runs, "c2_revisions": c2_revisions,
                 "f_data_isolated": True}
    iso_probs = _isolation_problems(iso_probe)
    if isinstance(batch, dict) and batch.get("f_data_isolated") is False:
        iso_probs.append("batch reported f_data_isolated false")
    f_data_isolated = not iso_probs

    # ---- P0-1: batch.F must reach scores.F, or grading can never see batch stability ----
    # batch_check.py emits F in the same shape as score.py emits a dimension
    # ({"score","measured","threshold"}); F is only admissible when the batch was
    # verified, otherwise it is a placeholder and must not be read as a number.
    f_source = "none"
    if grading_entered:
        f_raw = batch.get("F") if isinstance(batch, dict) else None
        if isinstance(f_raw, dict) and "score" in f_raw:
            f_source = "batch.json"
            if f_verified:
                scores["F"] = f_raw
            else:
                scores["F"] = "N/A"
                if "F" not in degraded_dims:
                    degraded_dims.append("F")
        elif isinstance(f_raw, str) and f_raw:
            f_source = "batch.json(unverified)"
            scores["F"] = "N/A"
        elif scores.get("F") in (None, ""):
            f_source = "missing"
            scores["F"] = "N/A"

    # ---- P1-3: an L2 run must carry A/B as the capability-missing status ----
    # Previously an L2 run whose score.json was absent kept scores={} while
    # reason=capability_missing, so the anti-fabrication check accused the log of
    # fabricating two scores that were never there. The status is materialised
    # here instead of being left blank, which is what "A/B 记 N/A" actually means.
    score_source = "score.json" if isinstance(score_doc, dict) else "none"
    discarded_scores = {}
    if l2 and grading_entered:
        for d in ("A", "B"):
            v = scores.get(d)
            if v != cap:
                # A/B numbers that exist were measured on something we just
                # declared unreadable. Overwriting (instead of erroring) keeps
                # delivery unblocked while making the log honest; the originals
                # stay in discarded_scores as an audit trail.
                if v not in (None, ""):
                    discarded_scores[d] = v
                scores[d] = cap
        for d in ("E", "F"):
            if scores.get(d) in (None, ""):
                scores[d] = "N/A"
    scores_missing = [d for d in ("A", "B", "C", "D", "F") if scores.get(d) in (None, "")]

    # ---- semi-manual branch ----
    landing = _load_optional(run_dir / "landing.json") or {}
    semi = landing.get("automation") == "semi-manual"
    if semi:
        degradation.append({"level": "L4", "reason": "COM not drivable; semi-manual JSX path",
                            "note": msg["semi_manual_note"]})

    # ---- grading (L2 still uses the 7.3 mechanical comparison; no third tier) ----
    hard_all_pass = bool(h1_ok and h2_ok and h3_status == "pass" and h4_ok and f_data_isolated)
    grade = msg["grade_fail"]
    headline = msg["l1_headline"]
    status = "fail"
    degraded_flag = bool(degradation)
    internal_excellent = False
    needs_human_review = True
    reason = msg["reason_quality_fail"]

    if not grading_entered:
        # H1-only evidence track: no tier, no gate bool, no score claim.
        grade = None
        tier = None
        headline = ("H1 取证专用：原生 .ai 结构回读通过，未进入评级" if h1_ok
                    else "H1 取证专用：原生 .ai 结构回读未通过")
        status = "ok" if h1_ok else "fail"
        degraded_flag = False
        needs_human_review = False
        reason = None
    elif l2:
        grade = msg["l2_grade"]
        headline = msg["l2_headline"]
        status = "degraded"
        degraded_flag = True
        needs_human_review = True
        reason = msg["reason_capability_missing"]
        degradation.insert(0, {"level": "L2",
                               "reason": "host LLM cannot read rendered PNG",
                               "note": "A/B capability_missing, human final review required; delivery not blocked"})
    else:
        dims = th["grade"]["pass_requires_dims"]
        vals = [scores.get(d) for d in dims]
        numeric = [v["score"] for v in vals if isinstance(v, dict)]
        no_zero = all(v != lv["fail"] for v in numeric) and len(numeric) == len(dims)
        e_val = scores.get("E")
        e_ok = (e_val == "N/A" or (isinstance(e_val, dict) and e_val["score"] >= lv["pass"]))
        passed = hard_all_pass and no_zero and e_ok and f_verified and not args.quick
        if passed:
            grade = msg["grade_pass"]
            headline = "已达质量阈值，可进入人工终审（可选）"
            status = "ok"
            needs_human_review = False
            reason = msg["reason_none"]
            er = th["grade"]["excellent_requires"]
            twos = sum(1 for v in scores.values() if isinstance(v, dict) and v["score"] == lv["excellent"])
            a_val, b_val = scores.get("A"), scores.get("B")
            internal_excellent = bool(
                isinstance(a_val, dict) and a_val["score"] == er["A"] and
                isinstance(b_val, dict) and b_val["score"] == er["B"] and
                twos >= er["min_twos"])
        elif args.quick:
            grade = msg["grade_fail"]
            headline = th["orchestration"]["quick_mode"]["headline"]
        elif not h2_ok or not h1_ok:
            headline = "L4 降级：" + msg["l4_note"] + "，需人工终审"
            degradation.append({"level": "L4", "reason": "native .ai not produced",
                                "note": msg["l4_note"]})
            degraded_flag = True
        else:
            # L1: quality below threshold with every capability present. Design
            # 4.3 / 7.4 list L1 as a degradation level, so `degraded` must not
            # read false here; leaving it false was the last contradiction
            # between the log and the design.
            degradation.append({"level": "L1", "reason": "quality below threshold",
                                "note": th["degradation_levels"]["L1"]})
            degraded_flag = True

    # ---- a degraded delivery must never auto-close human review ----
    # The fallback-PNG case is the sharp one: the preview a human (and the A/B
    # judge) would rely on is itself known to be low-fidelity, so a numeric pass
    # reached through it cannot be presented as a clean pass. Flags are fixed
    # here; the A-F scores and the two grade tiers are left untouched.
    if grading_entered and degraded_flag:
        needs_human_review = True
        if grade == msg["grade_pass"]:
            headline = "已达质量阈值，但存在降级，需人工终审"

    # ---- calibration consistency ----
    if grading_entered:
        tier = "pass" if grade == msg["grade_pass"] else "not-pass"
    calib_problems = _calibration_check(th)

    acceptance = {
        "headline": headline,
        "schema": "vector-ai-studio/acceptance-v1",
        "run_id": args.run_id,
        "mode": mode,
        "grade": grade,
        "internal_excellent": internal_excellent,
        "tier": tier,
        "degraded": degraded_flag,
        "needs_human_review": needs_human_review,
        "reason": reason,
        "capability_missing": bool(l2) if grading_entered else None,
        "status": status,
        "grading_entered": grading_entered,
        "quick": bool(args.quick),
        "f_verified": f_verified,
        "f_data_isolated": f_data_isolated,
        "generation_count": generation_count,
        "generation_budget": generation_budget,
        "f_source_runs": f_source_runs,
        "c2_revisions": c2_revisions,
        "automation": "semi-manual" if semi else ("auto" if h1_ok else "none"),
        "semi_manual_note": msg["semi_manual_note"] if semi else "",
        "scores": scores,
        "scores_source": {"score": score_source, "F": f_source, "missing": scores_missing},
        "degraded_dims": degraded_dims,
        "discarded_scores": discarded_scores,
        "rounds": rounds,
        "runs": per_run,
        "h1": h1_ok,
        "h2": h2_ok, "h2_status": h2_status,
        "h3": (h3_status == "pass") if grading_entered else None,
        "h4": h4_ok,
        "hard_gates": {
            "H1": {"status": h1_status, "pass": h1_ok, "reason": h1_reason},
            "H2": {"status": h2_status, "pass": h2_ok, "missing": missing,
                   "png": png, "renderer": renderer if png else "none",
                   "fidelity": (fidelity or "limited") if png else "none",
                   "fallback": fallback, "degraded": fallback,
                   "preview_fidelity_note": msg["fallback_png_note"] if fallback else ""},
            "H3": {"status": h3_status, "reason": h3_reason, "source": h3_source,
                   "pass": (h3_status == "pass") if grading_entered else None},
            "H4": {"pass": h4_ok, "status": h4_status,
                   "credential_names_present": creds,
                   "note": "default chain checks names only, never reads values"},
        },
        "degradation": degradation,
        "artifacts": {
            "final_ai": final_ai.name if ai_ok else "",
            "final_svg": final_svg.name if final_svg.is_file() else "",
            "preview_png": preview.name if png else "",
            "acceptance_json": acceptance_path.name,
        },
        "calibration_check": {"ok": not calib_problems,
                              "problems": [p["code"] for p in calib_problems]},
        # Measured-but-ungated findings have to reach a file someone actually
        # reads. score.json carries them already, but acceptance.json is the
        # artefact the delivery chain quotes; a warning that exists only in the
        # intermediate file is decoration, not a report.
        "perceptual_warnings": list((score_doc or {}).get("perceptual_warnings") or []),
        "perceptual_measured": ((score_doc or {}).get("scores", {})
                                .get("C", {}).get("measured", {})
                                .get("perceptual", {})),
    }

    # Mechanical acceptance-log validation (budget / isolation / anti-fabrication / quick).
    validation_problems = _validate_acceptance(acceptance, th)

    L.write_json_atomic(acceptance_path, acceptance)
    if not grading_entered:
        grade_note = "不参与评级（H1 取证专用）"
    elif l2:
        grade_note = msg["l2_outward_note"]
    else:
        grade_note = grade
    note_values = {
        "GRADE": grade_note,
        "HEADLINE": headline,
        "AI_FILE": acceptance["artifacts"]["final_ai"] or "未产出",
        "SVG_FILE": acceptance["artifacts"]["final_svg"] or "未产出",
        "PNG_FILE": acceptance["artifacts"]["preview_png"] or "未产出",
        "ACCEPTANCE": acceptance_path.name,
        "DEGRADATION_NOTE": (msg["l2_outward_note"] if l2 else
                             ("; ".join(d["note"] for d in degradation) if degradation else "无降级")),
    }
    L.write_text_atomic(run_dir / "delivery-note.md", _template_note(th, note_values))
    L.append_run_log(run_dir, SCRIPT + " mode=" + mode + " grade=" + str(grade) +
                     " h2=" + str(h2_ok))

    errors = calib_problems + validation_problems
    if grading_entered and not h2_ok:
        errors.append(L.err("E-H2-INCOMPLETE", "four artifacts incomplete: " + ", ".join(missing)))
    if grading_entered:
        ok = bool(h2_ok and h1_ok and grade == msg["grade_pass"] and
                  not calib_problems and not validation_problems)
    else:
        # H1-only track: the only thing it claims to prove is the structure readback.
        ok = bool(h1_ok and not calib_problems and not validation_problems)
    return L.emit(SCRIPT, status="ok" if ok else status,
                  code=L.EXIT_OK if ok else L.EXIT_FAIL,
                  run_id=args.run_id,
                  data={"headline": headline, "grade": grade,
                        "mode": mode, "grading_entered": grading_entered,
                        "h2_status": h2_status,
                        "degraded": degraded_flag, "f_verified": f_verified,
                        "f_data_isolated": f_data_isolated,
                        "generation_count": generation_count,
                        "generation_budget": generation_budget,
                        "needs_human_review": needs_human_review, "reason": reason,
                        "capability_missing": bool(l2) if grading_entered else None,
                        "hard_gates": acceptance["hard_gates"],
                        "scores": scores,
                        "discarded_scores": discarded_scores,
                        "scores_source": acceptance["scores_source"],
                        "rounds": rounds,
                        "validation_problems": [p["code"] for p in validation_problems]},
                  errors=errors,
                  degradation=degradation[0] if degradation else None,
                  artifacts={"acceptance": acceptance_path.name,
                             "delivery_note": "delivery-note.md"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
