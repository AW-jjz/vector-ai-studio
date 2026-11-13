#!/usr/bin/env python3
"""H2 / grading / O2 / O3 / generation budget / F data isolation.

Grading is a mechanical numeric comparison driven by config, never declared by
the LLM. Handles the L2 capability-missing status (no third grading tier),
strict fallback-PNG H2 counting and runtime-derived generation budget.
acceptance.json puts headline on the first line.

--validate-case runs the acceptance-log validator against a fixture sample so
run_fixtures.py can assert every E-* rejection code mechanically.

r72 (criteria 2.0.0) changes three things in here, all of them about HOW the
verdict is spoken, none about the thresholds themselves:

1. The vocabulary comes from the contract (scripts/_contract.py, generated
   from contract/contract.json). A producer and a judge can no longer spell
   the same field differently -- that is what put every real machine in L2
   forever while the whole suite stayed green.
2. The verdict is THREE-valued: pass / fail / indeterminate. "We could not
   measure it" is no longer spelled "不达标" (a quality claim). A dim that was
   not measured is reported as not measured, with the reason and the fix.
3. The degradation reason is DERIVED from the trigger that caused it, and it
   carries a loss list (what was lost, what was kept, how to fix it). A
   degradation note that points at the wrong thing sends the user to fix the
   wrong thing.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import lib_pdf  # r70 #2: byte-only H1 probe, no engine subprocess
import _contract as C
import journal  # r73 (M2): production-time evidence hash chain
import thresholds_guard as TG  # r79 (M5): executable promotion policy

SCRIPT = "aggregate_log.py"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# Design S4.5: every graded log must carry these dims. E is optional because
# "no style reference" legitimately records it as N/A.
REQUIRED_DIMS = ("A", "B", "C", "D", "F")
# Words that would claim a pass / excellent / perfect result in degraded output.
# The vocabulary lives in the contract: one list, one place.
BANNED_CLAIM_WORDS = tuple(C.BANNED_CLAIM_WORDS)
# Deliverability is a separate question from quality (r72 / plan 4.7).
DELIVERABLE_OK = "deliverable"
DELIVERABLE_REVIEW = "deliverable_with_human_review"
DELIVERABLE_NO = "not_deliverable"
DELIVERABLE_EVIDENCE_ONLY = "evidence_only"



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


def _calibration_check(th):
    """r79 (M5): the policy is executable, not aspirational.

    Every calibration_log entry is a POST-CHANGE record, so it is checked in
    `entry` mode (after == current). The guard enforces the whitelist, the
    locked list, the direction map, sample size, provenance and the real-run
    log reference; two legacy codes keep their old names so existing consumers
    do not have to relearn the vocabulary.
    """
    problems = []
    alias = {"E-CALIB-STALE": "E-CALIB-MISMATCH"}
    for rec in th.get("calibration_log", []) or []:
        res = TG.check(th, rec, mode="entry")
        for p in res.get("problems") or []:
            code = alias.get(p["code"], p["code"])
            problems.append(L.err(code, p["message"]))
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


def _capability_trigger(evidence):
    """Why exactly can this run not judge A/B? Derived, never hand-written.

    The r71 degradation note said "host LLM cannot read rendered PNG" for
    EVERY L2 -- including the case where the real cause was a preview whose
    fidelity the criteria do not accept. Users then went and fixed the wrong
    thing. Here the trigger is computed from the evidence that is actually in
    the log, so the fix hint points at the real cause.
    """
    gates = evidence.get("hard_gates") or {}
    h2 = gates.get("H2") or {}
    fidelity = h2.get("fidelity")
    judge_readable = h2.get("judge_readable") is True
    base = {"actual": fidelity, "required": C.FIDELITY.FAITHFUL,
            "judge_readable": judge_readable, "source": "hard_gates.H2"}
    if fidelity == "none":
        base["kind"] = "no_preview"
        base["actual"] = "none"
        return base
    if fidelity is not None and fidelity != C.FIDELITY.FAITHFUL:
        base["kind"] = "fidelity"
        return base
    if not judge_readable:
        base["kind"] = "judge"
        base["actual"] = "readable=false"
        base["required"] = "readable=true"
        base["source"] = "judge.json"
        return base
    base["kind"] = "capability_missing"
    base["actual"] = "A/B capability_missing"
    base["required"] = "A/B numeric scores"
    base["source"] = "scores.A/B"
    return base


def _loss_list(trigger):
    """What a degradation actually cost, and what to do about it (plan 4.6).

    Every item is derived from the trigger, so the note can never blame the
    host LLM for a fidelity problem (or the other way round).
    """
    kind = trigger.get("kind")
    lost = ["A 维（语义命中）不进判定", "B 维（构图/留白）不进判定"]
    kept = ["C 维（调色板/描边）", "D 维（几何/路径）",
            "H1 取证（原生 .ai 字节回读）", "H4 取证（零凭据链路）",
            "原生 .ai 交付"]
    if kind == "fidelity":
        why = ("预览保真度未达判据要求（显示值 " + str(trigger.get("actual")) +
               "，要求 " + str(trigger.get("required")) + "）")
        fix = list(C.COVERAGE_FIX_HINTS["fidelity"].split("；"))
    elif kind == "judge":
        why = "宿主 LLM 读不了预览图（judge.readable=false）"
        fix = list(C.COVERAGE_FIX_HINTS["judge"].split("；"))
    elif kind == "no_preview":
        why = "没有真实预览图（preview.png 缺失或不是可解析 PNG）"
        fix = list(C.COVERAGE_FIX_HINTS["fidelity"].split("；"))
    else:
        why = "A/B 判读能力缺失（capability_missing）"
        fix = ["检查 score.py 的 A/B 输入是否齐全（judge.json 与 preview）"]
    return {"trigger": dict(trigger), "why": why, "lost": lost,
            "kept": kept, "fix_hint": fix}


def _coverage(scores, evidence, th):
    """Per dimension: was it MEASURED, and if not, why not and how to fix it.

    This is the field that makes "未判定" honest: it separates "we looked and
    it was bad" from "we never looked". The strings are derived from the
    evidence in the log, so the validator can re-derive them and a log cannot
    claim a coverage it did not earn.
    """
    cap = L.capability_missing_value(th)
    dims = tuple(th["grade"]["pass_requires_dims"])
    gates = evidence.get("hard_gates") or {}
    h2 = gates.get("H2") or {}
    fidelity = h2.get("fidelity")
    judge_readable = h2.get("judge_readable") is True
    l2 = evidence.get("capability_missing") is True
    quick = evidence.get("quick") is True
    cov = {}
    measured = 0
    for d in dims:
        v = scores.get(d)
        if isinstance(v, dict) and "score" in v:
            measured += 1
            cov[d] = {"measured": True, "why": "", "fix": ""}
            continue
        if d in ("A", "B"):
            if l2 or v == cap:
                if fidelity and fidelity != C.FIDELITY.FAITHFUL:
                    why = ("预览保真度未达判据要求（显示值 " + str(fidelity) +
                           "，要求 " + C.FIDELITY.FAITHFUL + "）")
                    fix = C.COVERAGE_FIX_HINTS["fidelity"]
                elif not judge_readable:
                    why = "宿主 LLM 读不了预览图（judge.readable=false）"
                    fix = C.COVERAGE_FIX_HINTS["judge"]
                else:
                    why = "A/B 判读能力缺失（capability_missing）"
                    fix = C.COVERAGE_FIX_HINTS["judge"]
            else:
                why = "本轮没有这一维的分数"
                fix = "跑 score.py 生成该维分数"
        elif d == "F":
            if quick:
                why = "quick 模式：F（批量稳定性）未验证"
                fix = C.COVERAGE_FIX_HINTS["quick"]
            else:
                why = "F（批量稳定性）未验证：需要 N>=3 个独立 run 的 batch"
                fix = C.COVERAGE_FIX_HINTS["f_unverified"]
        else:
            why = "本轮没有这一维的分数"
            fix = "跑 score.py 生成该维分数"
        cov[d] = {"measured": False, "why": why, "fix": fix}
    cov["coverage"] = round(measured / float(len(dims)), 4) if dims else 0.0
    cov["required_dims"] = list(dims)
    return cov


def _derive_grade(evidence, th, legacy=False):
    """Single source of truth for EVERY graded verdict field (r70 #5, r72 4.7).

    Pure function: no I/O, no wall-clock, does not mutate `evidence`, and never
    reads the declared ``grade`` / ``tier`` / ``internal_excellent`` -- those
    are outputs, not inputs. Both ``main()`` (which assembles `evidence` from
    the run artifacts) and ``_validate_acceptance()`` (which passes the
    acceptance-shaped case straight in) call THIS function, so a consumer can
    re-derive the verdict from evidence fields alone and compare it with the
    declared values. That is what closes the "judgement fields may self-attest"
    hole the r69 audit found.

    r72: the verdict has THREE values, not two.

      pass          every required dim was measured and none of them failed
      fail          something was measured and it failed, or the evidence chain
                    itself is broken (the .ai does not read back, the self-test
                    log is broken, a credential name leaked into the chain)
      indeterminate the chain is intact but a required dim could not be
                    measured (A/B unreadable, F unverified, quick mode, the
                    four artifacts are not all there)

    "indeterminate" is the whole point: a tool that cannot read a PNG must not
    tell its user the drawing is bad. It says "I could not measure this, here
    is why, here is how to fix it" -- and ``deliverable_status`` answers the
    separate question of whether the files can be handed over at all.

    ``legacy=True`` maps indeterminate onto the old two-valued wording for
    downstream consumers that cannot take a third value yet. Every OTHER
    decision (pass / fail) must be identical in both modes -- a compat switch
    that also changed the pass/fail boundary would be a criteria change
    wearing a compat flag.

    Boundary: the whole former inline grading block -- landing-only / L2 /
    L1+quick+L4 / pass + internal_excellent -- plus the degraded-delivery
    override and the tier derivation. All tier words, the dimension list and
    the score levels come from `th`; nothing is hardcoded here (O2). Returns
    the verdict fields plus ``degradation_add`` / ``degradation_front`` so
    main() can build the evidence chain without a second judgement branch.
    """
    msg = th["messages"]
    lv = th["score_levels"]
    scores = evidence.get("scores") or {}
    gates = evidence.get("hard_gates") or {}
    entered = evidence.get("grading_entered", True)
    f_verified = evidence.get("f_verified") is True
    f_data_isolated = evidence.get("f_data_isolated") is True
    quick = evidence.get("quick") is True

    def _gate_state(name, top_key):
        """True / False / None (not evaluated) for one hard gate.

        A MISSING gate is not a failed gate. Before r72 the two were the same
        thing, which made an incomplete log look like a broken one. The
        distinction matters now: "we did not measure this" (None) must not be
        reported as "this is bad" (False).
        """
        g = gates.get(name)
        if isinstance(g, dict) and "pass" in g:
            return g.get("pass")
        if top_key in evidence:
            return evidence.get(top_key)
        return None

    h1_state = _gate_state("H1", "h1")
    h2_state = _gate_state("H2", "h2")
    h3_state = _gate_state("H3", "h3")
    h4_state = _gate_state("H4", "h4")
    h1_ok = h1_state is True
    h2_ok = h2_state is True
    h3_ok = h3_state is True
    h4_ok = h4_state is True
    hard_all_pass = bool(h1_ok and h2_ok and h3_ok and h4_ok and f_data_isolated)
    # "unverified" is not "failed": a missing self-test log means the chain
    # was never proven, not that it misbehaved.
    h3_status = (gates.get("H3") or {}).get("status")
    artifacts = evidence.get("artifacts") or {}
    ai_present = bool(artifacts.get("final_ai"))

    cap = L.capability_missing_value(th)
    l2 = (evidence.get("capability_missing") is True
          or evidence.get("reason") == msg["reason_capability_missing"]
          or scores.get("A") == cap or scores.get("B") == cap)

    # Initial degraded state comes from the already-accumulated evidence chain
    # (e.g. the builtin-fallback PNG L4 entry). L2 / L4 / L1 branches may raise
    # it; the pass branch keeps it. Never mutate the caller's list.
    degraded_flag = bool(evidence.get("degraded")) or bool(evidence.get("degradation"))

    dims = tuple(th["grade"]["pass_requires_dims"])
    zero_dims = [d for d in dims
                 if isinstance(scores.get(d), dict)
                 and scores[d].get("score") == lv["fail"]]
    e_val = scores.get("E")
    e_ok = (e_val == "N/A" or (isinstance(e_val, dict)
                               and e_val.get("score", 0) >= lv["pass"]))
    e_failed = bool(e_val != "N/A" and isinstance(e_val, dict)
                    and e_val.get("score") == lv["fail"])
    no_zero = (len(zero_dims) == 0)

    coverage = _coverage(scores, evidence, th)

    grade = msg["grade_fail"]
    headline = msg["l1_headline"]
    status = "fail"
    internal_excellent = False
    needs_human_review = True
    reason = msg["reason_quality_fail"]
    tier = None
    degradation_add = None
    degradation_front = False
    deliverable = DELIVERABLE_NO
    indet_kind = None

    if not entered:
        # H1-only evidence track: no tier, no gate bool, no score claim.
        grade = None
        headline = ("H1 取证专用：原生 .ai 结构回读通过，未进入评级" if h1_ok
                    else "H1 取证专用：原生 .ai 结构回读未通过")
        status = "ok" if h1_ok else "fail"
        degraded_flag = False
        needs_human_review = False
        reason = None
        deliverable = DELIVERABLE_EVIDENCE_ONLY
    else:
        # ---- what we could measure, and what the chain itself says ----------
        # A definite failure is one we can point at: a measured dim that
        # failed, a gate that is explicitly False (the .ai is there but does
        # not read back, the self-test log is broken, a credential leaked).
        # A gate that was never evaluated, or an artefact that was never
        # produced, is NOT a failure -- that is "not measured", and it lands
        # on 未判定 with the deliverable status saying the files are not
        # shippable.
        measured_failed = (not no_zero) or e_failed
        chain_broken = (h3_state is False and h3_status != "unverified") or \
                       (h4_state is False) or \
                       (h1_state is False and ai_present)
        h2_missing = list((gates.get("H2") or {}).get("missing") or [])

        if measured_failed or chain_broken:
            grade = msg["grade_fail"]
            tier = C.TIER.NOT_PASS
            status = "fail"
            reason = msg["reason_quality_fail"]
            needs_human_review = True
            degraded_flag = True
            if measured_failed:
                headline = msg["l1_headline"]
                degradation_add = {"level": "L1",
                                   "reason": "measured dimension failed: " +
                                             ",".join(zero_dims + (["E"] if e_failed else [])),
                                   "note": th["degradation_levels"]["L1"]}
            elif not h1_ok:
                headline = "原生 .ai 结构回读未通过（H1），需人工终审"
                degradation_add = {"level": "L4",
                                   "reason": "H1 结构回读未通过",
                                   "note": "final.ai 的字节与结构回读对不上"}
            elif not h3_ok:
                headline = "自检证据不可用（H3），需人工终审"
                degradation_add = {"level": "L4",
                                   "reason": "H3 自检证据不可用",
                                   "note": "selftest.json 缺失、未绑定本 run 或未全绿"}
            else:
                headline = "凭据链路异常（H4），需人工终审"
                degradation_add = {"level": "L4", "reason": "H4 凭据检查未通过",
                                   "note": "默认链路不得读取凭据值"}
        elif l2:
            # We cannot read the picture. That is NOT a quality verdict.
            indet_kind = "l2"
            trigger = _capability_trigger(evidence)
            loss = _loss_list(trigger)
            grade = msg["grade_indeterminate"]
            tier = C.TIER.INDETERMINATE
            status = "degraded"
            degraded_flag = True
            needs_human_review = True
            reason = msg["reason_capability_missing"]
            headline = msg["l2_headline"]
            degradation_add = {"level": "L2",
                               "reason": loss["why"],
                               "note": "A/B 记 N/A (capability_missing)、grade 记未判定、交付不阻断",
                               "loss": loss}
            degradation_front = True
        elif quick or not f_verified:
            # F (batch stability) was not verified. The single run may be
            # perfect; we simply do not know how it behaves across runs.
            indet_kind = "f"
            grade = msg["grade_indeterminate"]
            tier = C.TIER.INDETERMINATE
            status = "degraded"
            degraded_flag = True
            needs_human_review = True
            reason = msg["reason_insufficient_evidence"]
            headline = (th["orchestration"]["quick_mode"]["headline"] if quick
                        else msg["indeterminate_headline"])
            degradation_add = {"level": "L1" if not quick else "L4",
                               "reason": ("quick 模式：F 未验证" if quick
                                          else "F 未验证：本轮没有可验证的 batch 汇总"),
                               "note": msg["indeterminate_outward_note"],
                               "loss": {"trigger": {"kind": "f_unverified",
                                                    "actual": "quick" if quick else "f_verified=false",
                                                    "required": "batch.f_verified=true (N>=3)",
                                                    "source": "batch.json"},
                                        "why": "F（批量稳定性）未验证",
                                        "lost": ["F 维不进判定", "整链不进入达标结论"],
                                        "kept": ["A/B/C/D 维的单轮读数", "H1 取证"],
                                        "fix_hint": [C.COVERAGE_FIX_HINTS["f_unverified"]]}}
            degradation_front = True
        elif not h2_ok:
            # The four artifacts are not all there, so nothing can be judged.
            # The chain is intact; the evidence is not. That is "not measured",
            # and the deliverable status says the files are not shippable.
            indet_kind = "h2"
            grade = msg["grade_indeterminate"]
            tier = C.TIER.INDETERMINATE
            status = "degraded"
            degraded_flag = True
            needs_human_review = True
            reason = msg["reason_insufficient_evidence"]
            headline = "L4 降级：四件产物不齐（" + (", ".join(h2_missing) or "H2") + "），需人工终审"
            degradation_add = {"level": "L4",
                               "reason": "四件产物不齐（H2）",
                               "note": ", ".join(h2_missing) or "H2 未通过",
                               "loss": {"trigger": {"kind": "artifacts",
                                                    "actual": ", ".join(h2_missing),
                                                    "required": "final.ai / final.svg / preview.png",
                                                    "source": "hard_gates.H2"},
                                        "why": "四件产物不齐，无法进入判定",
                                        "lost": ["A/B/C/D/F 全部不进判定"],
                                        "kept": ["H1 取证（若 final.ai 在场）"],
                                        "fix_hint": ["先让四件产物齐备再重跑聚合"]}}
            degradation_front = True
        elif h3_state is not True:
            # The self-test evidence is missing or unproven. The chain never
            # showed it behaves, so this run cannot be certified -- but that
            # is a gap in the evidence, not a defect in the artwork.
            indet_kind = "h3"
            grade = msg["grade_indeterminate"]
            tier = C.TIER.INDETERMINATE
            status = "degraded"
            degraded_flag = True
            needs_human_review = True
            reason = msg["reason_insufficient_evidence"]
            headline = msg["indeterminate_headline"]
            degradation_add = {"level": "L4",
                               "reason": "H3 自检证据缺失或未绑定本 run",
                               "note": msg["indeterminate_outward_note"],
                               "loss": {"trigger": {"kind": "h3",
                                                    "actual": str(h3_status or "unverified"),
                                                    "required": "selftest.json 与本 run 绑定且全绿",
                                                    "source": "hard_gates.H3"},
                                        "why": "自检证据不可用，整链无法被证明",
                                        "lost": ["整链不进入达标结论"],
                                        "kept": ["A/B/C/D 维的单轮读数", "H1 取证"],
                                        "fix_hint": ["用同一个 --run-id 跑 selftest.py --log-dir <run-dir>"]}}
            degradation_front = True
        else:
            passed = hard_all_pass and no_zero and e_ok and f_verified and not quick
            if passed:
                grade = msg["grade_pass"]
                tier = C.TIER.PASS
                headline = "已达质量阈值，可进入人工终审（可选）"
                status = "ok"
                needs_human_review = False
                reason = msg["reason_none"]
                er = th["grade"]["excellent_requires"]
                twos = sum(1 for v in scores.values()
                           if isinstance(v, dict) and v.get("score") == lv["excellent"])
                a_val, b_val = scores.get("A"), scores.get("B")
                internal_excellent = bool(
                    isinstance(a_val, dict) and a_val.get("score") == er["A"] and
                    isinstance(b_val, dict) and b_val.get("score") == er["B"] and
                    twos >= er["min_twos"])
            else:
                # hard_all_pass is the only thing left that can be false here.
                grade = msg["grade_fail"]
                tier = C.TIER.NOT_PASS
                status = "fail"
                reason = msg["reason_quality_fail"]
                headline = msg["l1_headline"]
                degradation_add = {"level": "L1", "reason": "quality below threshold",
                                   "note": th["degradation_levels"]["L1"]}
                degraded_flag = True

    # A degraded delivery must never auto-close human review. The fallback-PNG
    # case is the sharp one: a numeric "pass" reached through a low-fidelity
    # preview cannot be presented as a clean pass. (former lines 571-579)
    if entered and degraded_flag:
        needs_human_review = True
        if grade == msg["grade_pass"]:
            headline = "已达质量阈值，但存在降级，需人工终审"

    if entered:
        if grade == msg["grade_pass"]:
            tier = C.TIER.PASS
        elif grade == msg["grade_indeterminate"]:
            tier = C.TIER.INDETERMINATE
        else:
            tier = C.TIER.NOT_PASS

    # Deliverability is a separate question from quality: the files can be
    # shippable while the quality verdict is still unknown.
    if entered:
        if not (h1_ok and h2_ok):
            deliverable = DELIVERABLE_NO
        elif grade == msg["grade_pass"] and not degraded_flag:
            deliverable = DELIVERABLE_OK
        else:
            deliverable = DELIVERABLE_REVIEW

    # Legacy two-valued mode: the third value collapses onto the old wording.
    # Nothing else changes -- the pass/fail boundary must be identical, and
    # the wording must be the r71 wording, so a consumer that switches the
    # flag on sees exactly the log it saw before.
    if legacy and grade == msg["grade_indeterminate"]:
        grade = msg[C.VERDICT.LEGACY_GRADE_KEY]
        tier = C.VERDICT.LEGACY_TIER
        if indet_kind == "l2":
            headline = msg["l2_headline"]
            reason = msg["reason_capability_missing"]
            status = "degraded"
        elif indet_kind == "h2":
            headline = "L4 降级：" + msg["l4_note"] + "，需人工终审"
            reason = msg["reason_quality_fail"]
            status = "fail"
            degradation_add = {"level": "L4", "reason": "native .ai not produced",
                               "note": msg["l4_note"]}
        else:
            headline = msg["l1_headline"]
            reason = msg["reason_quality_fail"]
            status = "fail"
            degradation_add = {"level": "L1", "reason": "quality below threshold",
                               "note": th["degradation_levels"]["L1"]}

    return {
        "grade": grade, "tier": tier, "headline": headline, "status": status,
        "degraded": degraded_flag, "needs_human_review": needs_human_review,
        "reason": reason, "internal_excellent": internal_excellent,
        "degradation_add": degradation_add,
        "degradation_front": degradation_front,
        "evidence_coverage": coverage,
        "deliverable_status": deliverable,
    }



# Verdict fields the validator re-derives and compares field-by-field (R-1-a:
# internal_excellent is included, never excluded to let a sample through).
# r72: evidence_coverage and deliverable_status joined the list -- a log may
# not claim a coverage it did not earn, nor a deliverable status it does not
# have.
_VERDICT_FIELDS = ("grade", "tier", "headline", "status", "degraded",
                   "needs_human_review", "reason", "internal_excellent",
                   "evidence_coverage", "deliverable_status")


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
    # r72: a log may opt into the old two-valued wording, but only by saying
    # so. The flag is part of the log, so the re-derivation below uses the
    # same mapping and the two modes stay comparable.
    legacy = a.get("legacy_grading") is True

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
        # r72: capability_missing is a MEASUREMENT gap, so the verdict is
        # 未判定 (indeterminate), not 不达标. Saying "not good enough" about a
        # drawing we could not look at is a quality claim we cannot support.
        want_l2_grade = msg[C.VERDICT.LEGACY_GRADE_KEY] if legacy else msg["grade_indeterminate"]
        if a.get("grade") != want_l2_grade:
            problems.append(L.err("E-L2-GRADE",
                                  "L2 grade must be " + repr(want_l2_grade) +
                                  " (capability_missing is a measurement gap, not a "
                                  "quality verdict); got " + repr(a.get("grade"))))
        if a.get("degraded") is not True or a.get("needs_human_review") is not True:
            problems.append(L.err("E-L2-FIELDS", "L2 requires degraded and needs_human_review true"))
        if a.get("headline") != msg["l2_headline"]:
            problems.append(L.err("E-L2-HEADLINE", "L2 headline must be the requirements text"))
        blob = json.dumps(a, ensure_ascii=False).replace(msg["grade_fail"], "")
        if any(w in blob for w in BANNED_CLAIM_WORDS):
            problems.append(L.err("E-L2-WORDING", "L2 output must not claim pass / excellent / perfect"))

    # r72: an indeterminate verdict may not smuggle a tier word or a clean
    # deliverable status in. "未判定" means "we do not know" -- nothing more.
    if a.get("grade") == msg["grade_indeterminate"] and not legacy:
        if a.get("tier") != C.TIER.INDETERMINATE:
            problems.append(L.err("E-INDETERMINATE-FIELDS",
                                  "an indeterminate verdict must carry tier=" +
                                  C.TIER.INDETERMINATE + ", got " + repr(a.get("tier"))))
        if a.get("deliverable_status") not in (DELIVERABLE_REVIEW, DELIVERABLE_NO,
                                                DELIVERABLE_EVIDENCE_ONLY):
            problems.append(L.err("E-INDETERMINATE-FIELDS",
                                  "an indeterminate verdict must not claim deliverable_status=" +
                                  repr(a.get("deliverable_status"))))
        if msg["grade_pass"] in str(a.get("headline", "")):
            problems.append(L.err("E-INDETERMINATE-FIELDS",
                                  "an indeterminate verdict must not headline a pass"))
    if a.get("deliverable_status") not in (DELIVERABLE_OK, DELIVERABLE_REVIEW,
                                           DELIVERABLE_NO, DELIVERABLE_EVIDENCE_ONLY,
                                           None):
        problems.append(L.err("E-INDETERMINATE-FIELDS",
                              "unknown deliverable_status: " + repr(a.get("deliverable_status"))))

    if a.get("quick") is True or a.get("f_verified") is False:
        if a.get("grade") == msg["grade_pass"] or a.get("grade") == "优秀":
            problems.append(L.err("E-QUICK-UNVERIFIED",
                                  "unverified F (quick mode) must not claim a pass"))

    # ---- #5: consumer re-derives the verdict from evidence alone (r70) -------
    # Only for a full-track log that declares the complete new-shape judgement
    # block (mode=full + grading_entered=true) and has passed every contract
    # check above. That gate is deliberate:
    #   * V1/V2/V3/V7/V8/V9 (declared 达标 or internal_excellent=true with
    #     contradictory evidence) pass the old field/budget/isolation checks,
    #     so this is the ONLY check that can reject them -> E-GRADE-MISMATCH.
    #   * V4/V5/V6 and all pre-v1.2 samples (no `mode`, or an existing
    #     E-BUDGET-EXCEEDED / E-QUICK-UNVERIFIED / E-NO-SCORE) keep their
    #     original single rejection code, so the exact go/want match holds.
    #   * landing-only / grading_entered=false never reaches here (the
    #     166-188 early-return above), so grade=null is never misjudged.
    # Every field of _VERDICT_FIELDS is compared -- notably
    # internal_excellent, which is what makes the rebuilt V9 a real negative.
    if (not problems and a.get("mode") == "full"
            and a.get("grading_entered") is True):
        rederived = _derive_grade(a, th, legacy=legacy)
        bad = [f for f in _VERDICT_FIELDS
               if a.get(f) != rederived[f]]
        if bad:
            problems.append(L.err(
                "E-GRADE-MISMATCH",
                "declared verdict disagrees with evidence-derived verdict on " +
                ", ".join(f + ": " + repr(a.get(f)) + "!=" + repr(rederived[f])
                          for f in bad)))
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
    ap.add_argument("--two-valued", action="store_true",
                    help="legacy mode: map 未判定 onto the old 不达标 wording "
                         "(the log is marked legacy_grading=true; every pass/fail "
                         "decision is unchanged)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    th = L.load_thresholds(args.thresholds or None)
    msg = th["messages"]
    lv = th["score_levels"]
    cap = L.capability_missing_value(th)
    mode = "landing-only" if args.landing_only else "full"
    grading_entered = mode == "full"
    # The verdict is only valid for the criteria that produced it, so the
    # acceptance log carries both the criteria version and the hash of the
    # threshold file it was made with (r72 / plan section 7).
    thresholds_sha256 = L.file_sha256(Path(args.thresholds) if args.thresholds
                                       else L.config_path())

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

    # r72 (plan P1): the narrow track must not be able to launder a failed
    # full run into "exit 0, no grade". If this directory already holds the
    # grading chain's evidence, the verdict exists and the narrow track is
    # not a fallback -- it is a way to hide one. The narrow track is for
    # machines that could NOT run the full chain, so those artefacts are
    # absent there by construction.
    if args.landing_only:
        _chain_evidence = [n for n in ("score.json", "judge.json",
                                       "selftest.json", "batch.json")
                           if (run_dir / n).is_file()]
        if _chain_evidence:
            raise L.ToolError(
                "E-EXEMPTION-ABUSE",
                "--landing-only refused: this run directory already holds the "
                "grading chain's evidence (" + ", ".join(_chain_evidence) +
                "); the narrow track may not replace a full verdict",
                L.EXIT_USAGE)

    score_doc, rounds = _collect_scores(run_dir)
    scores = (score_doc or {}).get("scores", {})
    degraded_dims = list((score_doc or {}).get("degraded_dims", []))
    l2 = (bool((score_doc or {}).get("l2_degraded")) or
          bool((score_doc or {}).get("capability_missing")) or
          "A" in degraded_dims or "B" in degraded_dims)

    # ---- H1: real-machine structure readback + independent byte probe ----
    # r70 #2: the readback alone can be hand-written. Reopen final.ai's OWN
    # bytes here (pure lib_pdf.ai_h1_probe, no engine subprocess) and AND the
    # result onto the existing raster/placed/paths/artboard/ok gate (QA-03 /
    # C-5). The probe writes only booleans + small ints into hard_gates.H1 --
    # never a sha256 or /CreationDate, which would break byte idempotency.
    sr = _load_optional(run_dir / "structure_readback.json")
    final_ai = run_dir / "final.ai"
    ai_ok = final_ai.is_file() and final_ai.stat().st_size > 0
    probe = lib_pdf.ai_h1_probe(final_ai) if ai_ok else {
        "pdf_ok": False, "page_count": 0, "image_count": 0}
    probe_pdf_ok = probe["pdf_ok"]
    probe_pages_ok = probe["page_count"] >= 1
    probe_images_ok = (isinstance(sr, dict) and probe["image_count"] == sr.get("raster"))
    probe_bytes_ok = probe_pdf_ok and probe_pages_ok and probe_images_ok
    h1_ok, h1_status, h1_reason = False, "unverified", "no structure readback in this run"
    if isinstance(sr, dict) and ai_ok:
        raster, placed, paths = sr.get("raster"), sr.get("placed"), sr.get("paths")
        dims_ok = sr.get("artboard_matches_viewBox") is True
        readback_ok = (raster == 0 and placed == 0 and isinstance(paths, int)
                       and paths > 0 and dims_ok and sr.get("ok") is True)
        h1_ok = readback_ok and probe_bytes_ok
        h1_status = "pass" if h1_ok else "fail"
        # The reason must follow the verdict: a fixed "raster/placed=0..." line
        # reads like a pass even when the readback failed.
        if h1_ok:
            h1_reason = "readback: raster/placed=0, paths>0, artboard==viewBox; byte probe: %PDF-, >=1 Page, images==raster"
        elif not probe_pdf_ok:
            h1_reason = "H1 byte probe: final.ai is not a %PDF- container"
        elif not probe_pages_ok:
            h1_reason = "H1 byte probe: final.ai has no /Type /Page object"
        elif not probe_images_ok:
            h1_reason = ("H1 byte probe: embedded image count " +
                         str(probe["image_count"]) + " != structure_readback.raster " +
                         str(sr.get("raster")))
        elif sr.get("stale") is True:
            h1_reason = "stale readback from an earlier run; this run has no readback"
        else:
            h1_reason = "readback did not satisfy H1: raster/placed/paths/artboard"

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
    # r70 #1: fidelity evidence is DECOUPLED from renderer identity.
    # A/B semantic / composition reading needs (a) the preview to positively
    # declare itself "faithful" and (b) a readable judge. Both must be explicit.
    # The old gate keyed on `fallback`: deleting preview.meta.json made
    # renderer default to "unknown" -> fallback=False -> the whole L2 check was
    # skipped, so removing evidence bought a free pass (report P0-2). Now the
    # ABSENCE of meta / fidelity / a readable judge is itself a fail-closed L2,
    # for every renderer (unknown, resvg+limited, builtin, ...). `full` is a
    # display enum in pipeline-contract, NOT the判据 -- the code-实存 value is
    # "faithful" (render_png._builtin_fidelity / A18 both use it).
    judge = _load_optional(run_dir / "judge.json")
    fidelity = str(preview_meta.get("fidelity", "") or "").lower()
    judge_readable = isinstance(judge, dict) and judge.get("readable") is True
    # The accepted set lives in the CONTRACT, not in a literal here: the
    # producer (render_png / land_vc) and the judge (this function) read the
    # same generated table, so they cannot spell the same field differently.
    # r71's P0-1 was exactly that: producers wrote `full`, the judge accepted
    # `faithful`, and every real machine landed in L2 forever -- with the
    # whole suite green, because nothing compared the two.
    fidelity_ok = (C.fidelity_is_judged("aggregate_log", "A", fidelity)
                   and judge_readable)
    # What a reader sees: the accepted value, or the fail-closed default for
    # everything else. Never a word the criteria do not know.
    fidelity_display = ((fidelity if C.fidelity_is_judged("aggregate_log", "A", fidelity)
                         else C.FIDELITY.FAIL_CLOSED_DEFAULT) if png else "none")
    if grading_entered and png is not None and not fidelity_ok:
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
                # r73 (M2): record WHERE the log is relative to the run dir,
                # never the absolute path. acceptance.json must be determined
                # by the run's CONTENT, not by where the directory happens to
                # live -- an absolute path here made the same bytes replay to
                # different bytes from a different location, which is an
                # idempotency violation the replay byte-compare caught.
                h3_source = (cand.relative_to(run_dir).as_posix()
                             if cand.is_relative_to(run_dir) else cand.name)
                selftest_log = d
                break
    h3_status = "unverified"
    h3_reason = "no selftest.json next to the run artifacts"
    # r70 #3: the allowed-skip whitelist comes from the SINGLE shared constant
    # L.CAPABILITY_SKIPS, NEVER from the log being validated. A self-written
    # `capability_skips` in selftest.json used to be trusted and could declare
    # its own guards optional (report P0-3). That field is now display/audit
    # only: if it disagrees with L, we emit a warning candidate, not a verdict.
    #
    # Registration condition: the capability itself is optional (an engine or
    # an external library that may legitimately be absent). Never add an
    # assertion to L.CAPABILITY_SKIPS to make a run go green -- that turns a
    # guard into decoration.
    OPTIONAL_ASSERTIONS = frozenset(L.CAPABILITY_SKIPS)
    self_whitelist_warning = ""  # candidate signal only; QA/human adjudicates
    if isinstance(selftest_log, dict):
        declared_skips = selftest_log.get("capability_skips")
        if (isinstance(declared_skips, list)
                and set(declared_skips) != set(OPTIONAL_ASSERTIONS)):
            self_whitelist_warning = (
                "selftest.json.capability_skips differs from the shared "
                "L.CAPABILITY_SKIPS policy (display-only; judgment uses L)")
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
        # Assertion-count lower bound: a hand-written "1/1 ok" log (report
        # §5.3) can never stand in for a real full self-test.
        count_ok = n_all >= L.EXPECTED_ASSERTIONS
        # Bind the log to THIS run: both ids are content-derived
        # (L.sha_run_id), never a nonce / timestamp / signature, so the
        # judgment JSON stays byte-idempotent. An empty --run-id fails closed.
        bound = bool(args.run_id) and selftest_log.get("run_id") == args.run_id
        if status_ok and consistent and n_fail == 0 and n_pass >= 1 and count_ok and bound:
            h3_status = "pass"
            h3_reason = ("selftest " + str(n_pass) + "/" + str(n_all) +
                         (" (+" + str(n_skip) + " optional skipped)" if n_skip else "") +
                         " from " + Path(h3_source).name + " [run_id bound]")
        else:
            h3_status = "fail"
            why = ""
            if not status_ok and selftest_log.get("status") == "partial":
                why = " skipped=" + ",".join(x for x in skipped_ids
                                             if x not in OPTIONAL_ASSERTIONS) + " (not optional)"
            if not count_ok:
                why += (" assertions=" + str(n_all) + " < expected " +
                        str(L.EXPECTED_ASSERTIONS))
            if not bound:
                why += (" run_id mismatch: log=" + repr(selftest_log.get("run_id")) +
                        " expected=" + repr(args.run_id))
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

    # ---- grading: single source of truth (r70, #5) ----------------------------
    # main() and _validate_acceptance() both call _derive_grade(). Nothing here
    # assigns a grade/tier/headline/... by hand any more: a half-migration that
    # left one branch inline would let a forged judgement field attest itself.
    # `evidence` is acceptance-shaped, so the validator can pass a case verbatim
    # and re-derive exactly the verdict this run wrote.
    evidence = {
        "scores": scores,
        "hard_gates": {
            "H1": {"pass": h1_ok},
            # r72: the H2 gate carries the preview's fidelity and whether the
            # judge could read it, because those two facts are WHY A/B can or
            # cannot be judged. The verdict is derived from them, so the
            # degradation note can name the real cause instead of guessing.
            "H2": {"pass": h2_ok, "fidelity": fidelity_display,
                   "judge_readable": judge_readable, "missing": list(missing)},
            "H3": {"pass": (h3_status == "pass") if grading_entered else None,
                   "status": h3_status},
            "H4": {"pass": h4_ok},
        },
        # Whether the native .ai was produced at all: a missing artefact is a
        # capability gap (未判定), while a present artefact that does not read
        # back is a real defect (不达标). The two must not be confused.
        "artifacts": {"final_ai": final_ai.name if ai_ok else "",
                      "final_svg": final_svg.name if final_svg.is_file() else ""},
        "h1": h1_ok, "h2": h2_ok,
        "f_verified": f_verified, "f_data_isolated": f_data_isolated,
        "quick": bool(args.quick),
        "degraded": bool(degradation),
        "degradation": [dict(d) for d in degradation],
        "capability_missing": bool(l2) if grading_entered else None,
        "mode": mode, "grading_entered": grading_entered,
    }
    verdict = _derive_grade(evidence, th, legacy=bool(args.two_valued))
    grade = verdict["grade"]
    tier = verdict["tier"]
    headline = verdict["headline"]
    status = verdict["status"]
    degraded_flag = verdict["degraded"]
    needs_human_review = verdict["needs_human_review"]
    reason = verdict["reason"]
    internal_excellent = verdict["internal_excellent"]
    evidence_coverage = verdict["evidence_coverage"]
    deliverable_status = verdict["deliverable_status"]
    if verdict["degradation_add"]:
        if verdict["degradation_front"]:
            degradation.insert(0, verdict["degradation_add"])
        else:
            degradation.append(verdict["degradation_add"])

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
        # r72: which dimensions were MEASURED, and which were not (with the
        # reason and the fix). "We did not look" must never be spelled
        # "not good enough".
        "evidence_coverage": evidence_coverage,
        # ... and whether the files can be handed over at all, which is a
        # different question from whether the quality is acceptable.
        "deliverable_status": deliverable_status,
        # r72: the criteria this verdict was made under. A conclusion is only
        # valid for the criteria version that produced it.
        "criteria_version": C.CRITERIA_VERSION,
        "thresholds_sha256": thresholds_sha256,
        "legacy_grading": bool(args.two_valued),
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
        "automation": "auto" if h1_ok else "none",
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
            "H1": {"status": h1_status, "pass": h1_ok, "reason": h1_reason,
                   # r70 #2: booleans + small ints only. No sha256 / dates:
                   # acceptance.json must stay byte-identical across two runs.
                   "probe": {"pdf_ok": probe_pdf_ok, "pages_ok": probe_pages_ok,
                             "images_match_raster": bool(probe_images_ok),
                             "page_count": probe["page_count"],
                             "image_count": probe["image_count"]}},
            "H2": {"status": h2_status, "pass": h2_ok, "missing": missing,
                   "png": png, "renderer": renderer if png else "none",
                   # r72: fail-closed display. Only a value the contract
                   # accepts is shown as itself; everything else (missing
                   # field, a value nobody produces, a fake one) shows the
                   # fail-closed default, so H2.fidelity can never advertise
                   # an unverified pass.
                   "fidelity": fidelity_display,
                   "judge_readable": judge_readable,
                   "fallback": fallback, "degraded": fallback,
                   "preview_fidelity_note": msg["fallback_png_note"] if fallback else ""},
            "H3": {"status": h3_status, "reason": h3_reason, "source": h3_source,
                   "pass": (h3_status == "pass") if grading_entered else None},
            "H4": {"pass": h4_ok, "status": h4_status,
                   "credential_names_present": creds,
                   "note": "default chain checks names only, never reads values"},
        },
        "degradation": degradation,
        # r73 (M2): the name of the production-time evidence journal, written
        # next to this file by the code below. The name only -- the journal's
        # content varies with the run (it binds final.ai / run.log-era files),
        # and acceptance.json must stay byte-idempotent (A3).
        "evidence_journal": "journal.jsonl",
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
    elif grade == msg["grade_indeterminate"]:
        grade_note = msg["indeterminate_outward_note"]
    else:
        grade_note = grade
    note_values = {
        "GRADE": grade_note,
        "HEADLINE": headline,
        "AI_FILE": acceptance["artifacts"]["final_ai"] or "未产出",
        "SVG_FILE": acceptance["artifacts"]["final_svg"] or "未产出",
        "PNG_FILE": acceptance["artifacts"]["preview_png"] or "未产出",
        "ACCEPTANCE": acceptance_path.name,
        "DEGRADATION_NOTE": (msg["indeterminate_outward_note"]
                             if grade == msg["grade_indeterminate"] else
                             ("; ".join(d["note"] for d in degradation) if degradation else "无降级")),
        "COVERAGE": ("已测 " + str(evidence_coverage.get("coverage", 0)) +
                     "（" + ", ".join(d + ("✓" if (evidence_coverage.get(d) or {}).get("measured") else "✗")
                                     for d in (evidence_coverage.get("required_dims") or [])) +
                     "）"),
    }
    L.write_text_atomic(run_dir / "delivery-note.md", _template_note(th, note_values))

    # r73 (M2): production-time evidence binding. EVERY file in the run dir
    # (minus the journal itself and the wall-clock run.log) is hashed into a
    # chain. replay.py verifies the chain, re-hashes the artifacts, and
    # byte-compares a fresh aggregation -- so a verdict or an evidence file
    # edited after this point is detectable by the consumer, not just by us.
    # Written AFTER acceptance.json / delivery-note.md so the chain binds the
    # exact bytes that ship.
    journal_entries = journal.build_entries(run_dir)
    journal.write_journal(run_dir / "journal.jsonl", journal_entries)
    L.append_run_log(run_dir, SCRIPT + " mode=" + mode + " grade=" + str(grade) +
                     " h2=" + str(h2_ok) +
                     " journal_entries=" + str(len(journal_entries)))

    errors = calib_problems + validation_problems
    # r70 #2: reuse E-H1-STRUCTURE for a failed independent byte probe (both
    # evidence tracks). A hand-written structure_readback.json alone can no
    # longer buy H1 a pass. This gives the run-dir negative fixture a concrete
    # code to hit instead of merely a non-zero exit. Only when a real final.ai
    # was opened and its OWN bytes contradicted the readback; a missing .ai is
    # already covered by the "no structure readback" path.
    if ai_ok and isinstance(sr, dict) and not probe_bytes_ok:
        errors.append(L.err("E-H1-STRUCTURE",
                            "final.ai byte probe did not satisfy H1: " + h1_reason))
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
                        "tier": tier,
                        "mode": mode, "grading_entered": grading_entered,
                        "h2_status": h2_status,
                        "degraded": degraded_flag, "f_verified": f_verified,
                        "f_data_isolated": f_data_isolated,
                        "generation_count": generation_count,
                        "generation_budget": generation_budget,
                        "needs_human_review": needs_human_review, "reason": reason,
                        "evidence_coverage": evidence_coverage,
                        "deliverable_status": deliverable_status,
                        "criteria_version": C.CRITERIA_VERSION,
                        "legacy_grading": bool(args.two_valued),
                        "capability_missing": bool(l2) if grading_entered else None,
                        "hard_gates": acceptance["hard_gates"],
                        "scores": scores,
                        "discarded_scores": discarded_scores,
                        "scores_source": acceptance["scores_source"],
                        "rounds": rounds,
                        "validation_problems": [p["code"] for p in validation_problems]},
                  errors=errors,
                  # r70 #3: a log whose self-declared capability_skips differs
                  # from L.CAPABILITY_SKIPS is only a candidate signal for a
                  # human/QA to review, never a verdict. No "E-XXX:" prefix so
                  # the exact go/want code match in run_fixtures is untouched.
                  warnings=([self_whitelist_warning] if self_whitelist_warning else []),
                  degradation=degradation[0] if degradation else None,
                  artifacts={"acceptance": acceptance_path.name,
                             "delivery_note": "delivery-note.md"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
