#!/usr/bin/env python3
"""Layered L1: pure-script selftest. Zero external deps, zero credentials, zero
Illustrator, zero renderer, zero network - MUST be runnable after install.

All sub-script calls use ABSOLUTE script paths while cwd is a temp dir, which
proves cwd independence. Real-machine checks live only in smoke_ai.py (L2).
"""

import argparse
import importlib.util
import os
import json
import re
import shutil
import subprocess
import zlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "selftest.py"
SCRIPTS = Path(__file__).resolve().parent
ROOT = L.skill_root()
FIX = L.fixtures_dir()
GOOD = FIX / "good"
BAD = FIX / "bad"

INLINE_FEATURES = ["def ", "#target illustrator", "param(", "New-Object -ComObject",
                   "import json", "import lib_common", "#!/usr/bin/env python"]
THRESHOLD_LITERALS = ["0.15", "0.25", "0.45", "0.6667", "0.70", "0.20", "0.10", "0.8", "0.6"]
CORE_SCRIPTS = ["score.py", "batch_check.py", "iterate.py", "aggregate_log.py"]
O3_BANNED = ["优秀", "Excellent"]
CLAIM_BANNED = ["达标", "优秀", "完美", "Excellent"]


def _call(name, args, cwd, flags=()):
    cmd = ([sys.executable] + list(flags) + [str(SCRIPTS / name)]
           + [str(a) for a in args])
    try:
        r = subprocess.run(cmd, cwd=str(cwd), timeout=90, capture_output=True,
                           text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return -1, {}, ""
    try:
        env = json.loads(r.stdout or "")
    except json.JSONDecodeError:
        env = {}
    return r.returncode, env, (r.stdout or "")


def _codes(env):
    return [e.get("code", "") for e in env.get("errors", []) if isinstance(e, dict)]


def _chain(work, cwd, svg=None):
    """Run the pure-script chain on `svg` (default: the circle-bearing sample).

    rect-only.svg -> builtin fallback is judged "faithful" -> non-L2, graded run.
    sample.svg    -> circles are rasterised as squares -> "limited" -> auto L2.
    """
    work.mkdir(parents=True, exist_ok=True)
    src_svg = Path(svg) if svg else (GOOD / "sample.svg")
    steps = [
        ("freeze_spec.py", ["--spec", str(GOOD / "spec.json"), "--validate",
                            "--out", str(work / "spec.json")], {0}),
        ("sanitize_svg.py", ["--in", str(src_svg),
                             "--out", str(work / "final.svg")], {0}),
        ("render_png.py", ["--in", str(work / "final.svg"),
                           "--out", str(work / "preview.png"), "--force-builtin"], {0}),
        ("llm_judge.py", ["--judge", str(GOOD / "judge.json"), "--validate",
                          "--spec", str(work / "spec.json"),
                          "--out", str(work / "judge.json")], {0}),
        ("score.py", ["--svg", str(work / "final.svg"), "--judge", str(work / "judge.json"),
                      "--spec", str(work / "spec.json"),
                      "--out", str(work / "score.json")], {0, 1}),
    ]
    info = []
    ok = True
    for name, args, want in steps:
        rc, env, _ = _call(name, args, cwd)
        good = (rc in want and env.get("schema") == L.SCHEMA and
                isinstance(env.get("code"), int) and env.get("code") == rc and
                isinstance(env.get("errors"), list) and isinstance(env.get("data"), dict))
        info.append({"script": name, "exit": rc, "status": env.get("status"), "ok": good})
        ok = ok and good
        if not good:
            # A failed step invalidates every later step. Stop here instead of
            # letting a downstream script crash on a missing artifact: a missing
            # input must turn this chain (and its assertions) red, never raise
            # E-INTERNAL and abort the whole selftest.
            break

    # preview.meta.json is left exactly as render_png.py wrote it. Its `fidelity`
    # field is computed by the rasteriser itself (rect-only + solid fill + no
    # stroke => "faithful", otherwise "limited"); never hand-write it here, that
    # would be fabricating preview evidence. A circle-bearing sample is therefore
    # "limited" and correctly flips to L2.
    # P2-1: pass a content-derived run-id so acceptance.run_id is never empty.
    # Same fixture text + version => same id, so the idempotency check holds.
    spec_text = ((work / "spec.json").read_text(encoding="utf-8")
                 if (work / "spec.json").is_file() else "")
    rid = L.sha_run_id("chain", spec_text, L.load_thresholds()["version"])
    rc, env, _ = _call("aggregate_log.py",
                       ["--run-dir", str(work), "--out", str(work / "acceptance.json"),
                        "--run-id", rid], cwd)
    good = (rc in {0, 1} and env.get("schema") == L.SCHEMA and
            isinstance(env.get("code"), int) and env.get("code") == rc and
            isinstance(env.get("errors"), list) and isinstance(env.get("data"), dict))
    info.append({"script": "aggregate_log.py", "exit": rc,
                 "status": env.get("status"), "ok": good})
    ok = ok and good
    return ok, info


def _jread(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _copy_if(src, dst):
    """Copy only when the source exists; a missing input must never raise."""
    if Path(src).is_file():
        shutil.copyfile(src, dst)
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description="pure-script selftest")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log-dir", default="")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    th = L.load_thresholds()
    msg = th["messages"]
    cap = L.capability_missing_value(th)
    rid = args.run_id or L.sha_run_id("selftest", th["version"])
    log_dir = Path(args.log_dir) if args.log_dir else (L.default_output_root() / "selftest" / rid)
    log_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="vector-ai-selftest-"))
    a = []

    def add(aid, ok, detail="", skipped=False):
        # ok is True / False / None. None means "not evaluated", matching how
        # this project already expresses an unevaluated gate (h2=null with
        # h2_status=not_evaluated). A skip must not read as a pass to anything
        # that aggregates assertions[].ok -- that was the hole: exit 0, status
        # ok, and ok=true on an assertion that never ran.
        a.append({"id": aid, "ok": (None if skipped else bool(ok)),
                  "detail": str(detail)[:220], "skipped": bool(skipped)})

    # A1 envelope / exit codes + A3 idempotency + A4 cwd independence
    # Graded assertions (A6/A7/A10) need a non-L2 run, so the main chains use the
    # rect-only fixture: the builtin fallback renders it faithfully. A16 keeps the
    # circle-bearing sample to exercise the "limited preview -> L2" road.
    RECT = GOOD / "rect-only.svg"
    w1, w2 = tmp / "chain-a", tmp / "chain-b"
    ok1, chain = _chain(w1, tmp, svg=RECT)
    ok_b, _ = _chain(w2, tmp, svg=RECT)
    add("A1-envelope-exit-codes", ok1 and ok_b, "pure-script chain envelopes valid")
    add("A4-cwd-independent", ok1 and ok_b and tmp.resolve() != ROOT.resolve(),
        "all sub-scripts called by absolute path with temp cwd")

    cmp_files = ["spec.json", "final.svg", "judge.json", "score.json",
                 "acceptance.json", "preview.png", "preview.meta.json"]
    idempotent, detail = True, []
    for f in cmp_files:
        p1, p2 = w1 / f, w2 / f
        if not p1.is_file() or not p2.is_file() or p1.read_bytes() != p2.read_bytes():
            idempotent = False
            detail.append(f)
    add("A3-idempotent-byte-identical", idempotent,
        "differing: " + ",".join(detail) if detail else "judgment JSON byte-identical twice")

    # A2 single source of truth + no hardcoded thresholds
    ok2 = all(k in th for k in ("A", "B", "C", "D", "E", "F", "grade", "iterate",
                                "orchestration", "messages", "calibration_policy",
                                "calibration_log", "score_levels"))
    hard = []
    for s in CORE_SCRIPTS:
        src = (SCRIPTS / s).read_text(encoding="utf-8")
        hits = [t for t in THRESHOLD_LITERALS if t in src]
        if hits or "load_thresholds" not in src:
            hard.append(s + (":" + ",".join(hits) if hits else ":no-load_thresholds"))
    add("A2-thresholds-single-source", ok2 and not hard, ";".join(hard))

    # A5 inline source scan + script/command mapping
    skill_md = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```[^\n]*\n(.*?)```", skill_md, re.S)
    inline = sorted({f for b in blocks for f in INLINE_FEATURES if f in b})
    all_scripts = sorted({p.name for p in SCRIPTS.iterdir()
                          if p.suffix in (".py", ".jsx", ".ps1")})
    unmentioned = [s for s in all_scripts if ("scripts/" + s) not in skill_md]
    add("A5-no-inline-source-and-mapping", not inline and not unmentioned,
        "inline=" + ",".join(inline) + " unmentioned=" + ",".join(unmentioned))

    # A6 O3 outward template / generated note
    tpl = (L.templates_dir() / "delivery-note.template.md").read_text(encoding="utf-8")
    dn = w2 / "delivery-note.md"
    dn_text = dn.read_text(encoding="utf-8") if dn.is_file() else ""
    banned = [w for w in O3_BANNED if w in tpl or w in dn_text]
    add("A6-O3-no-excellent-wording", not banned, ",".join(banned))

    # A7 H4 credential-free default chain
    cred_reads = []
    for p in SCRIPTS.glob("*.py"):
        if re.search(r"os\.environ\s*(?:\[|\.get\()\s*[\"']RECRAFT", p.read_text(encoding="utf-8")):
            cred_reads.append(p.name)
    L.assert_no_credentials()
    add("A7-H4-no-credential-value-read", not cred_reads, ",".join(cred_reads))

    # A8 dual-runtime consistency: same input under the default interpreter and
    # under `-I` isolated mode must yield identical scores. pycache is redirected
    # into the temp dir so isolated mode cannot leave residue in the package.
    s1, s2 = _jread(w1 / "score.json"), _jread(w2 / "score.json")
    iso_out = w1 / "score-isolated.json"
    _call("score.py",
          ["--svg", str(w1 / "final.svg"), "--judge", str(w1 / "judge.json"),
           "--spec", str(w1 / "spec.json"), "--out", str(iso_out)],
          tmp, flags=["-I", "-X", "pycache_prefix=" + str(tmp / "pycache")])
    s_iso = _jread(iso_out)
    iso_equal = bool(s_iso) and s_iso.get("scores") == s1.get("scores")
    optional = {"cairosvg": importlib.util.find_spec("cairosvg") is not None,
                "pyyaml": importlib.util.find_spec("yaml") is not None,
                "svgo": shutil.which("svgo") is not None}
    add("A8-dual-runtime-consistent",
        bool(s1) and s1.get("scores") == s2.get("scores") and iso_equal,
        "default_vs_isolated_equal=" + str(iso_equal) +
        " optional=" + json.dumps(optional, sort_keys=True))

    # A9 calibration policy whitelist
    cp = th.get("calibration_policy", {})
    locked = cp.get("locked", [])
    ok9 = ("calibratable" in cp and "A.*" in locked and "iterate.K_max" in locked
           and "H1" in locked and "F.N_*" in locked)
    for rec in th.get("calibration_log", []) or []:
        if rec.get("loosening") and not rec.get("ruling_ref"):
            ok9 = False
    add("A9-calibration-policy", ok9, "locked=" + str(len(locked)))

    # A10 quick must not claim a pass. L2 outranks quick: an unreadable preview
    # is more fundamental than "F not verified", so either non-pass headline is
    # acceptable. Never tweak the work dir's SVG to dodge L2 here.
    _call("aggregate_log.py", ["--run-dir", str(w1), "--quick", "--run-id", "quick-a10",
                               "--out", str(w1 / "quick-acceptance.json")], tmp)
    qa = _jread(w1 / "quick-acceptance.json")
    ok10 = (qa.get("f_verified") is False and
            qa.get("grade") != msg["grade_pass"] and
            qa.get("headline") in (th["orchestration"]["quick_mode"]["headline"],
                                   msg["l2_headline"]))
    add("A10-quick-f-not-verified", ok10,
        "grade=" + str(qa.get("grade")) + " headline=" + str(qa.get("headline")))

    # A11 real fallback PNG + zero-byte placeholder rejected by H2
    png = (w1 / "preview.png").read_bytes() if (w1 / "preview.png").is_file() else b""
    meta = _jread(w1 / "preview.meta.json")
    real_png = (len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n" and
                png[12:16] == b"IHDR" and png[-8:-4] == b"IEND" and
                meta.get("renderer") == "builtin-fallback" and meta.get("degraded") is True)
    zp = tmp / "zero-png"
    zp.mkdir(parents=True, exist_ok=True)
    for name in ("final.svg", "score.json"):
        if (w1 / name).is_file():
            shutil.copyfile(w1 / name, zp / name)
    (zp / "preview.png").write_bytes(b"")
    _call("aggregate_log.py", ["--run-dir", str(zp), "--out", str(zp / "acceptance.json")], tmp)
    za = _jread(zp / "acceptance.json")
    h2 = za.get("hard_gates", {}).get("H2", {})
    reject = (h2.get("pass") is False and
              any("preview.png" in m for m in h2.get("missing", [])))
    add("A11-fallback-png-strict-H2", real_png and reject,
        "real_png=" + str(real_png) + " zero_byte_rejected=" + str(reject))

    # A12 L2: no third grading tier, capability-missing status, delivery not blocked
    wl = tmp / "l2"
    wl.mkdir(parents=True, exist_ok=True)
    if (w1 / "spec.json").is_file():
        shutil.copyfile(w1 / "spec.json", wl / "spec.json")
    # A16 keeps the circle-bearing sample on purpose: the builtin fallback draws
    # circles as squares, so render_png judges the preview "limited" and aggregate
    # must auto-degrade to L2. The unreadable judge is the second, independent road
    # to the same status; both must land on capability_missing.
    _copy_if(GOOD / "sample.svg", wl / "final.svg")
    _call("render_png.py", ["--in", str(wl / "final.svg"),
                            "--out", str(wl / "preview.png"), "--force-builtin"], tmp)
    L.write_json_atomic(wl / "judge-src.json", {
        "schema": "vector-ai-studio/judge-v1", "readable": False,
        "items": [], "subject_conflict": False, "attributes": [], "align": []})
    _call("llm_judge.py", ["--judge", str(wl / "judge-src.json"), "--validate",
                           "--out", str(wl / "judge.json")], tmp)
    _call("score.py", ["--svg", str(wl / "final.svg"), "--judge", str(wl / "judge.json"),
                       "--spec", str(wl / "spec.json"), "--out", str(wl / "score.json")], tmp)
    _call("aggregate_log.py", ["--run-dir", str(wl),
                               "--out", str(wl / "acceptance.json")], tmp)
    la = _jread(wl / "acceptance.json")
    lblob = (wl / "acceptance.json").read_text(encoding="utf-8") if (wl / "acceptance.json").is_file() else ""
    l2_words = [w for w in CLAIM_BANNED if w in lblob.replace(msg["grade_fail"], "")]
    ldn = (wl / "delivery-note.md").read_text(encoding="utf-8") if (wl / "delivery-note.md").is_file() else ""
    outward_words = [w for w in CLAIM_BANNED if w in ldn]
    ok12 = (la.get("grade") == msg["l2_grade"] and
            la.get("headline") == msg["l2_headline"] and
            la.get("degraded") is True and
            la.get("needs_human_review") is True and
            la.get("reason") == msg["reason_capability_missing"] and
            la.get("capability_missing") is True and
            la.get("grading_entered") is True and
            la.get("scores", {}).get("A") == cap and
            la.get("scores", {}).get("B") == cap and
            not l2_words and not outward_words)
    add("A12-L2-not-pass-no-third-tier", ok12, "grade=" + str(la.get("grade")) +
        " banned=" + ",".join(l2_words + outward_words))

    # A14 runtime-derived generation budget + real generation_count + rejection
    budget = L.generation_budget(th)
    acc1 = _jread(w1 / "acceptance.json")
    ok14 = (budget == {"pass": th["F"]["N_pass"] * th["iterate"]["K_max"],
                       "excellent": th["F"]["N_excellent"] * th["iterate"]["K_max"]})
    ok14 = (ok14 and isinstance(acc1.get("generation_count"), int) and
            acc1["generation_count"] <= budget["excellent"] and
            acc1.get("generation_budget") == budget)
    rc14, env14, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_budget_exceeded.json")], tmp)
    ok14 = ok14 and rc14 == 1 and "E-BUDGET-EXCEEDED" in _codes(env14)
    add("A14-generation-budget-derived", ok14,
        "budget=" + json.dumps(budget, sort_keys=True) +
        " count=" + str(acc1.get("generation_count")))

    # A15 O4: f_source_runs / c2_revisions isolation, no cross-run F backfill
    iso = tmp / "o4-isolated"
    for i in range(1, th["F"]["N_pass"] + 1):
        run_id = "run-" + str(i)
        fs = iso / "f_source_runs" / run_id
        fs.mkdir(parents=True, exist_ok=True)
        _copy_if(w1 / "score.json", fs / "score.json")
        _copy_if(w1 / "final.svg", fs / "draft.svg")
        L.write_json_atomic(fs / "h1.json", {"pass": True})
        rev = iso / "c2_revisions" / run_id / "round-2"
        rev.mkdir(parents=True, exist_ok=True)
        L.write_json_atomic(rev / "revision.json",
                            {"run_id": run_id, "from_round": 2, "change_only": ["C"]})
    rc15, env15, _ = _call("batch_check.py",
                           ["--runs-dir", str(iso), "--out", str(iso / "batch.json")], tmp)
    bd = _jread(iso / "batch.json")
    iso_ok = (rc15 == 0 and bd.get("f_data_isolated") is True and
              len(bd.get("f_source_runs", [])) == th["F"]["N_pass"] and
              len(bd.get("c2_revisions", [])) == th["F"]["N_pass"] and
              isinstance(bd.get("generation_count"), int) and
              bd["generation_count"] <= budget["excellent"])
    rc15b, env15b, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_f_backfill.json")], tmp)
    neg_ok = rc15b == 1 and "E-F-DATA-ISOLATION" in _codes(env15b)
    # Same SVG hash must not appear both in c2_revisions and in F source stats.
    hc = _jread(GOOD / "good_o4_isolated.json")
    if hc.get("c2_revisions"):
        hc["c2_revisions"][0]["draft_sha256"] = hc["f_source_runs"][0]["draft_sha256"]
    hash_path = tmp / "hash-overlap.json"
    L.write_json_atomic(hash_path, hc)
    rc15c, env15c, _ = _call("aggregate_log.py",
                             ["--validate-case", str(hash_path)], tmp)
    hash_ok = rc15c == 1 and "E-F-DATA-ISOLATION" in _codes(env15c)
    add("A15-F-data-isolation", iso_ok and neg_ok and hash_ok,
        "positive=" + str(iso_ok) + " backfill_rejected=" + str(neg_ok) +
        " shared_hash_rejected=" + str(hash_ok) +
        " gen_count=" + str(bd.get("generation_count")))

    # A16 L2 anti-fabrication: A/B must be status enum, never numeric scores
    ls = _jread(wl / "score.json")
    ab_ok = (ls.get("scores", {}).get("A") == cap and
             ls.get("scores", {}).get("B") == cap and
             not isinstance(ls.get("scores", {}).get("A"), dict))
    rc16, env16, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_l2_fabricated.json")], tmp)
    ok16 = ab_ok and rc16 == 1 and "E-FABRICATED-SCORE" in _codes(env16)
    add("A16-L2-anti-fabrication", ok16,
        "ab_status=" + str(ab_ok) + " rejected=" + str("E-FABRICATED-SCORE" in _codes(env16)))

    # A17 quick mode must never participate in a pass judgement
    rc17, env17, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_quick_claimed_pass.json")], tmp)
    ok17 = rc17 == 1 and "E-QUICK-UNVERIFIED" in _codes(env17)
    add("A17-quick-not-graded", ok17, "rejected=" + str(ok17))

    # A18 F merged into scores -> the pass tier becomes mechanically reachable.
    # SYNTHETIC validator run: proves the F->scores wiring, not a real .ai.
    # The SVG is rect-only with solid fills and no stroke, so render_png's own
    # _builtin_fidelity() judges the fallback "faithful" and A/B stays numeric
    # (a circle-bearing SVG would be "limited" and correctly flip to L2).
    a18 = tmp / "a18"
    a18.mkdir(parents=True, exist_ok=True)
    rect_ok = RECT.is_file()
    if rect_ok:
        shutil.copyfile(RECT, a18 / "final.svg")
    # A missing fixture deliberately leaves final.svg absent: render_png then
    # returns exit 2 and this assertion goes red, instead of copyfile raising
    # FileNotFoundError and aborting the whole selftest as E-INTERNAL.
    _call("render_png.py", ["--in", str(a18 / "final.svg"),
                            "--out", str(a18 / "preview.png"), "--force-builtin"], tmp)
    meta18 = _jread(a18 / "preview.meta.json")
    _copy_if(GOOD / "judge.json", a18 / "judge.json")
    (a18 / "final.ai").write_bytes(
        b"synthetic-h1-file-existence-placeholder-not-a-real-ai\n")
    L.write_json_atomic(a18 / "structure_readback.json", {
        "ok": True, "raster": 0, "placed": 0, "paths": 5,
        "artboard_matches_viewBox": True})
    L.write_json_atomic(a18 / "selftest.json", {
        "status": "ok", "passed": 1, "failed": 0,
        "assertions": [{"id": "A0", "ok": True}]})
    L.write_json_atomic(a18 / "score.json", {
        "round": 2, "l2_degraded": False, "degraded_dims": [], "failed_dims": [],
        "scores": {"A": {"score": 2, "measured": {}, "threshold": {}},
                   "B": {"score": 2, "measured": {}, "threshold": {}},
                   "C": {"score": 1, "measured": {}, "threshold": {}},
                   "D": {"score": 1, "measured": {}, "threshold": {}},
                   "E": "N/A", "F": "N/A"}})
    L.write_json_atomic(a18 / "batch.json", {
        "schema": "vector-ai-studio/batch-v1", "quick": False,
        "f_verified": True, "f_data_isolated": True, "isolation_problems": [],
        "N": 3, "generation_count": 3,
        "F": {"score": 1, "measured": {"N": 3, "h1_pass_rate": 1.0,
                                       "ad_pass_rate": 0.8333,
                                       "palette_consistent": True},
              "threshold": {}, "degraded": False},
        "f_source_runs": [{"run_id": "run-1", "draft_sha256": "a" * 64},
                          {"run_id": "run-2", "draft_sha256": "b" * 64},
                          {"run_id": "run-3", "draft_sha256": "c" * 64}],
        "c2_revisions": [],
        "per_run": [{"run": "run-" + str(i), "h1_pass": True,
                     "ad_pass_rate": 0.85, "palette": ["#111", "#eee"]}
                    for i in (1, 2, 3)]})
    rc18, env18, _ = _call("aggregate_log.py",
                           ["--run-dir", str(a18), "--run-id", "a18", "--json"], tmp)
    d18 = env18.get("data", {}) or {}
    acc18 = _jread(a18 / "acceptance.json")
    f_dict = isinstance((acc18.get("scores") or {}).get("F"), dict)
    ok18 = (rect_ok and rc18 == 0 and acc18.get("grade") == msg["grade_pass"] and f_dict
            and acc18.get("needs_human_review") is True
            and meta18.get("fidelity") == "faithful")
    add("A18-F-merged-reachable", ok18,
        ("MISSING FIXTURE rect-only.svg; " if not rect_ok else "") +
        "synthetic rect-only run: fallback judged faithful by render_png itself; "
        "proves the F->scores wiring, not preview fidelity; "
        "fidelity=" + str(meta18.get("fidelity")) +
        " grade=" + str(acc18.get("grade")) +
        " needs_human_review=" + str(acc18.get("needs_human_review")) +
        " F_is_dict=" + str(f_dict) +
        " validation=" + str(d18.get("validation_problems")))

    # A18b a compliant full-mode pass-tier acceptance log passes validation
    rc18b, env18b, _ = _call("aggregate_log.py",
                             ["--validate-case", str(GOOD / "good_acceptance_reachable.json")], tmp)
    add("A18b-validator-accepts-pass", rc18b == 0 and not _codes(env18b),
        "codes=" + ",".join(_codes(env18b)))

    # A19 full track without C/D is a broken chain, never an L2 degradation
    rc19, env19, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_no_score.json")], tmp)
    ok19 = rc19 == 1 and "E-NO-SCORE" in _codes(env19)
    add("A19-full-track-no-score-rejected", ok19, "codes=" + ",".join(_codes(env19)))

    # A20 landing-only track: unevaluated (null), accepted, no tier word anywhere
    rc20, env20, _ = _call("aggregate_log.py",
                           ["--validate-case", str(GOOD / "good_landing_only.json")], tmp)
    lo_blob = (GOOD / "good_landing_only.json").read_text(encoding="utf-8")
    lo_words = [w for w in (CLAIM_BANNED + ["未验证"]) if w in lo_blob]
    ok20 = rc20 == 0 and not _codes(env20) and not lo_words
    add("A20-landing-only-unevaluated", ok20,
        "codes=" + ",".join(_codes(env20)) + " words=" + ",".join(lo_words))

    # A21 the exemption must be policed from both sides
    rc21a, env21a, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_exemption_abuse_full.json")], tmp)
    rc21b, env21b, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_exemption_abuse_lo.json")], tmp)
    ok21 = (rc21a == 1 and "E-EXEMPTION-ABUSE" in _codes(env21a) and
            rc21b == 1 and "E-EXEMPTION-ABUSE" in _codes(env21b))
    add("A21-exemption-abuse-both-ways", ok21,
        "full_track_exit=" + str(rc21a) + " lo_track_exit=" + str(rc21b))

    # A22 package hygiene: runtime output/ must never live inside the skill dir,
    # and no stray __pycache__ may be shipped. scripts/ bytecode cache is created
    # by this very run, so it is ignored here and excluded by the packager.
    stray = []
    if (ROOT / "output").exists():
        stray.append("output/")
    for p in ROOT.rglob("__pycache__"):
        if p.is_dir() and p.parent != SCRIPTS:
            stray.append(str(p.relative_to(ROOT)))
    add("A22-package-hygiene", not stray,
        ",".join(stray[:6]) if stray
        else "no output/ and no stray __pycache__ (scripts/ bytecode cache ignored)")

    # A23 L2 must carry all six score keys with the right per-key status.
    # Reverse side: dropping A or F must be rejected with E-NO-SCORE.
    l2_sc = la.get("scores") or {}
    l2_keys = set(l2_sc) >= {"A", "B", "C", "D", "E", "F"}
    l2_shape = (l2_sc.get("A") == cap and l2_sc.get("B") == cap
                and isinstance(l2_sc.get("C"), dict) and isinstance(l2_sc.get("D"), dict)
                and (l2_sc.get("E") == "N/A" or isinstance(l2_sc.get("E"), dict))
                and (l2_sc.get("F") == "N/A" or isinstance(l2_sc.get("F"), dict)))
    rc_mf, env_mf, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_missing_f.json")], tmp)
    rc_ma, env_ma, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_missing_a.json")], tmp)
    mf_rej = rc_mf == 1 and "E-NO-SCORE" in _codes(env_mf)
    ma_rej = rc_ma == 1 and "E-NO-SCORE" in _codes(env_ma)
    add("A23-L2-six-keys", l2_keys and l2_shape and mf_rej and ma_rej,
        "keys=" + str(sorted(l2_sc)) + " shape=" + str(l2_shape) +
        " missing_f_rejected=" + str(mf_rej) + " missing_a_rejected=" + str(ma_rej))

    # A24 a graded (full-track) log must carry every grade-required dimension.
    full_sc = acc18.get("scores") or {}
    req_missing = sorted({"A", "B", "C", "D", "F"} - set(full_sc))
    add("A24-full-required-dims", not req_missing and mf_rej and ma_rej,
        "keys=" + str(sorted(full_sc)) + " missing=" + str(req_missing))

    # A25 a passing (grade=达标) log must keep E visible: dropping E would hide
    # "E applicable but scored 0", which is the same fake-pass surface as a
    # missing graded dimension. Reverse-verified with bad_pass_missing_e.json.
    rc_me, env_me, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_pass_missing_e.json")], tmp)
    me_rej = rc_me == 1 and "E-NO-SCORE" in _codes(env_me)
    add("A25-pass-missing-e-rejected", me_rej,
        "pass_missing_e_rejected=" + str(me_rej) + " codes=" + ",".join(_codes(env_me)))

    # A26: the top-level contract fields and their metadata mirror must agree.
    # Two specifications read this file (internal contract.yaml wants them at
    # top level; the public Agent Skills spec puts author/version under
    # metadata). Keeping both copies is only safe if a machine compares them.
    def _fm():
        txt = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        body = txt.split("---", 2)[1]
        top, meta, cur = {}, {}, None
        for raw in body.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if raw[0] in " \t":
                if cur:
                    k, _, v = raw.strip().partition(":")
                    meta[k.strip()] = v.strip().strip('"').strip("'")
                continue
            k, _, v = raw.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v == "":
                cur = k
            else:
                cur = None
                top[k] = v
        return top, meta

    _top, _meta = _fm()
    pairs = [(k, _top.get(k), _meta.get(k)) for k in
             ("author", "version", "description_zh", "description_en")]
    mirror_ok = all(m is not None and t == m for _, t, m in pairs)
    add("A26-frontmatter-metadata-mirror", mirror_ok,
        "pairs=" + str([(k, t == m) for k, t, m in pairs]) +
        " | 解析器仅支持扁平 YAML：若改用块标量(>- / |)等结构会在此判红，属预期")

    # A27: the published JSON Schema must accept a real log and reject broken
    # ones. A schema nobody validates against is documentation, not a contract.
    schema_path = ROOT / "references" / "schemas" / "acceptance.schema.json"
    acc_path = w1 / "acceptance.json"
    sch_ok = False
    detail = "schema or acceptance missing"
    if schema_path.is_file() and acc_path.is_file():
        rc_ok, env_ok, _ = _call("schema_check.py",
                                 [str(schema_path), str(acc_path), "--json"], tmp)
        acc_doc = json.loads(acc_path.read_text(encoding="utf-8"))

        bad_dir = tmp / "schema-negative"
        bad_dir.mkdir(parents=True, exist_ok=True)
        missing_mode = dict(acc_doc)
        missing_mode.pop("mode", None)
        bad_mode_path = bad_dir / "missing-mode.json"
        bad_mode_path.write_text(json.dumps(missing_mode, ensure_ascii=False), encoding="utf-8")
        rc_miss, _, _ = _call("schema_check.py",
                              [str(schema_path), str(bad_mode_path), "--json"], tmp)

        bad_grade = dict(acc_doc)
        bad_grade["grade"] = "优秀"
        bad_grade_path = bad_dir / "bad-grade.json"
        bad_grade_path.write_text(json.dumps(bad_grade, ensure_ascii=False), encoding="utf-8")
        rc_grade, _, _ = _call("schema_check.py",
                               [str(schema_path), str(bad_grade_path), "--json"], tmp)

        sch_ok = (rc_ok == 0 and rc_miss == 1 and rc_grade == 1)
        detail = ("valid_exit=" + str(rc_ok) +
                  " missing_mode_exit=" + str(rc_miss) +
                  " bad_grade_exit=" + str(rc_grade))
    add("A27-schema-accepts-real-rejects-broken", sch_ok, detail)

    # A28: every error code emitted by the scripts must be registered, and the
    # registry must not list codes nobody emits. Checked in both directions --
    # a one-way check lets stale entries accumulate, which is how a registry
    # stops being trusted.
    reg_path = ROOT / "references" / "error-codes.md"
    code_set = set()
    for sp in sorted((ROOT / "scripts").glob("*.py")):
        code_set |= set(re.findall(r'"(E-[A-Z0-9-]+)"', sp.read_text(encoding="utf-8")))
    reg_set = set()
    if reg_path.is_file():
        reg_set = set(re.findall(r"`(E-[A-Z0-9-]+)`", reg_path.read_text(encoding="utf-8")))
    reg_ok = bool(code_set) and code_set == reg_set
    add("A28-error-code-registry", reg_ok,
        "in_code=" + str(len(code_set)) + " in_registry=" + str(len(reg_set)) +
        " unregistered=" + ",".join(sorted(code_set - reg_set)[:6]) +
        " stale=" + ",".join(sorted(reg_set - code_set)[:6]))

    # A29: the measurement function must actually measure.
    #
    # Round 6 shipped a colour-operator counter that returned None on *every*
    # real deliverable because zlib.decompress() rejected the trailing CR/LF.
    # The function existed, was called, and produced a plausible-looking field
    # ("undecodable") -- and no assertion noticed.
    #
    # A well-formed synthetic stream is not enough to catch that: strict
    # decompression handles it fine. The sample below truncates the trailing
    # adler32 so the stream is incomplete, which is what Illustrator actually
    # writes. Strict decompression fails on it; decompressobj succeeds. That
    # difference is the whole point, so it is the sample this assertion uses.
    a29_ok, a29_detail = False, "not run"
    try:
        _spec = importlib.util.spec_from_file_location("land_ai_under_test",
                                                       ROOT / "scripts" / "land_ai.py")
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)

        payload = b"0.2 0.4 0.6 rg\n0 0 0 1 K\n"
        complete = zlib.compress(payload)
        truncated = complete[:-4]                      # drop adler32 -> incomplete
        body = b"stream\n" + truncated + b"\nendstream\n"
        ops, decoded = _mod._count_ops([body])

        junk = b"stream\n" + b"\x00\x01\x02\x03" + b"\nendstream\n"
        ops_junk, decoded_junk = _mod._count_ops([junk])

        # Scope guard: counting every decodable stream inflated the number ~40x
        # on real files because ICC profiles and font programs contain k/K
        # bytes. A page must only count its /Contents, so this fixture puts a
        # decoy stream (20 k operators) in an object the page never references.
        decoy = zlib.compress(b"0 0 0 1 k\n" * 20)
        pdf = (b"%PDF-1.6\n"
               b"1 0 obj\n<< /Type /Page /Contents 3 0 R >>\nendobj\n"
               b"3 0 obj\n<< /Length 99 >>\n" + body[:-1] + b"endobj\n"
               b"5 0 obj\n<< /Subtype /Image >>\nstream\n" + decoy +
               b"\nendstream\nendobj\n")
        scoped, scoped_stats = _mod._content_ops(pdf)

        # The schema is the single source of truth for the operator key set.
        # r36 widened the code from 4 keys to 10 while the schema still declared
        # 4, and every gate stayed green because additionalProperties is open --
        # the drift was invisible. Comparing the two makes it red instead.
        _decl_doc = json.loads(
            (ROOT / "references" / "schemas" / "deliverable.schema.json")
            .read_text(encoding="utf-8"))
        _decl_keys = set(
            _decl_doc["properties"]["observed"]["properties"]["content_ops"]["properties"])

        # One Type0 font is two /BaseFont entries (the composite and its
        # descendant CIDFont) but a single embedded program. Counting entries
        # made the manifest disagree with vectorcraft's `info` on the same file.
        fonts = _mod._font_programs(
            b"1 0 obj << /BaseFont /Stone-Bold /DescendantFonts [2 0 R] >> endobj\n"
            b"2 0 obj << /BaseFont /Stone-Bold-CID /FontDescriptor 3 0 R >> endobj\n"
            b"3 0 obj << /FontFile2 4 0 R >> endobj\n")

        a29_ok = (ops["rg"] == 1 and ops["K"] == 1 and decoded == 1 and
                  ops_junk is None and decoded_junk == 0 and
                  scoped["k"] == 0 and scoped["K"] == 1 and scoped["rg"] == 1 and
                  scoped_stats.get("objects") == 1 and
                  scoped_stats.get("decoded") == 1 and fonts == 1 and
                  set(scoped) == _decl_keys)
        a29_detail = ("incomplete_stream_ops=" + str(ops) +
                      " decoded=" + str(decoded) +
                      " junk_ops=" + str(ops_junk) +
                      " scoped_ops=" + str(scoped) +
                      " objects=" + str(scoped_stats.get("objects")) +
                      " fonts=" + str(fonts) +
                      " keys_match_schema=" + str(set(scoped) == _decl_keys))
    except Exception as e:                      # noqa: BLE001 - report, do not crash
        a29_detail = type(e).__name__ + ": " + str(e)[:140]
    add("A29-content-ops-measurement", a29_ok, a29_detail)

    # A30: the alternate engine's artefacts must survive the DOWNSTREAM chain.
    #
    # The defects this guards against were all invisible to the other 30
    # assertions: a hardcoded engine version, a missing structure_readback.json
    # (which makes aggregate_log mark H1 "unverified" for a perfectly good
    # file), and missing final.svg/preview.png (which makes H2 fail). Every
    # gate was green while the chain was broken end to end.
    #
    # When the optional CLI is absent the check reports skipped, never passed.
    a30_ok, a30_detail, a30_skip = False, "not run", False
    try:
        _spec2 = importlib.util.spec_from_file_location("land_vc_under_test",
                                                        ROOT / "scripts" / "land_vc.py")
        _lv = importlib.util.module_from_spec(_spec2)
        _spec2.loader.exec_module(_lv)
        _cli = _lv.find_cli("")
        if _cli is None:
            a30_skip, a30_ok = True, True
            a30_detail = "skipped: vectorcraft-cli not found (optional engine)"
        else:
            # A SQUARE sample cannot detect a transposed or scaled artboard:
            # 200x200 -> [200,200] either way. Generate a non-square one so the
            # width/height comparison in this assertion actually discriminates.
            wide = tmp / "wide.svg"
            wide.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200" '
                'width="300" height="200">'
                '<rect x="10" y="10" width="120" height="80" fill="#2f6fed"/>'
                '<circle cx="220" cy="100" r="60" fill="#ffb020"/></svg>',
                encoding="utf-8")
            vdir = tmp / "vc-chain"
            rc_l, env_l, _ = _call("land_vc.py",
                                   ["--svg", str(wide),
                                    "--out", str(vdir), "--profile", "print",
                                    "--run-id", "a30", "--json"], tmp)
            rc_a, env_a, _ = _call("aggregate_log.py",
                                   ["--run-dir", str(vdir), "--run-id", "a30", "--json"], tmp)
            # aggregate_log puts the acceptance fields at data level; older
            # envelopes nested them under "acceptance". Accept both rather than
            # betting on one shape -- betting on one shape is exactly how this
            # assertion first reported a false red.
            _data = env_a.get("data") or {}
            _acc = _data.get("acceptance") or _data
            _gates = _acc.get("hard_gates") or _data.get("hard_gates") or {}
            h1 = (_gates.get("H1") or {}).get("pass") is True
            h2 = (_gates.get("H2") or {}).get("pass") is True
            # Read the artefact back and check the *emitted* numbers. The
            # in-code guard checked the raw rect, so reverting only the value
            # that gets written left every gate green with artboard=[200,-200].
            _sr = {}
            try:
                _sr = json.loads((vdir / "structure_readback.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            _ab = _sr.get("artboard") or []
            _vb = _sr.get("viewBox") or []
            ab_positive = (len(_ab) == 2 and all(isinstance(x, (int, float)) and x > 0 for x in _ab))
            # "> 0" only catches sign errors. A transposed or scaled value still
            # passes it, and nothing compared the emitted numbers to the source
            # dimensions -- so a 2% inflation or a swap was invisible to every
            # gate. Compare the artefact against viewBox with the tolerance the
            # in-code dims_ok already uses; that kills sign, transpose, scale and
            # unit errors at once without inventing a new threshold.
            ab_matches_vb = (len(_ab) == 2 and len(_vb) == 4 and
                             abs(_ab[0] - _vb[2]) <= 1 and abs(_ab[1] - _vb[3]) <= 1)
            deliv = (env_l.get("data") or {}).get("deliverable") or {}
            ver = str(deliv.get("engine_version") or "")
            # A version string that is not what the binary reports means the
            # manifest is guessing again.
            ver_ok = bool(ver) and ver != "unknown"
            a30_ok = (rc_l == 0 and h1 and h2 and ver_ok and ab_positive and ab_matches_vb)
            a30_detail = ("land=" + str(rc_l) + " h1=" + str(h1) + " h2=" + str(h2) +
                          " version=" + (ver or "?") +
                          " artboard=" + str(_ab) + " viewBox=" + str(_vb) +
                          " positive=" + str(ab_positive) +
                          " matches_viewBox=" + str(ab_matches_vb) +
                          " agg_exit=" + str(rc_a))
    except Exception as e:                      # noqa: BLE001
        a30_detail = type(e).__name__ + ": " + str(e)[:140]
    add("A30-vectorcraft-chain-h1-h2", a30_ok, a30_detail, skipped=a30_skip)

    # A31: the manifest must not drift from its own schema, for BOTH engines.
    #
    # Round 8 found 13 fields emitted but undeclared on one engine and absent on
    # the other, and the schema still validated because it never forbade extra
    # keys. A contract that validates while omitting half the payload is worse
    # than no contract: it certifies nothing. So compare both directions, per
    # engine, and treat a mismatch as a failure.
    a31_ok, a31_detail, a31_skip = False, "not run", False
    try:
        _spec3 = importlib.util.spec_from_file_location("land_ai_schema", ROOT / "scripts" / "land_ai.py")
        _la = importlib.util.module_from_spec(_spec3)
        _spec3.loader.exec_module(_la)
        _schema = json.loads((ROOT / "references" / "schemas" /
                              "deliverable.schema.json").read_text(encoding="utf-8"))

        def _type_ok(v, spec):
            t = spec.get("type")
            if t is None:
                return True
            names = [t] if isinstance(t, str) else list(t)
            for n in names:
                if n == "object" and isinstance(v, dict): return True
                if n == "array" and isinstance(v, list): return True
                if n == "string" and isinstance(v, str): return True
                if n == "boolean" and isinstance(v, bool): return True
                if n == "integer" and isinstance(v, int) and not isinstance(v, bool): return True
                if n == "number" and isinstance(v, (int, float)) and not isinstance(v, bool): return True
                if n == "null" and v is None: return True
            return False

        def _diff(doc, label):
            props = _schema.get("properties") or {}
            # Three dimensions, not one. Only checking `required` let three
            # declared-but-never-written fields pass unnoticed for a whole round.
            extra = sorted(set(doc) - set(props))
            missing = sorted(k for k in props if k not in doc)          # declared, absent
            typebad = sorted(k for k in props if k in doc and not _type_ok(doc[k], props[k]))
            nested = {}
            for obj in ("save_options", "observed", "structure", "source_probe"):
                sub = (props.get(obj) or {}).get("properties")
                if not sub:
                    continue
                val = doc.get(obj)
                if val is None:
                    continue
                if not isinstance(val, dict):
                    nested[obj] = "not-an-object"
                    continue
                nx = sorted(set(val) - set(sub))
                if nx:
                    nested[obj] = {"extra": nx}
            return extra, missing, typebad, nested

        # --- Illustrator shape: built through the same constructor the real
        # path uses, so no Illustrator install is needed to check the contract.
        _obs = {"bytes": 1000, "sha256": "0" * 64, "pdf_compatible": True,
                "pdf_header": "%PDF-1.6", "has_ai_private": True,
                "device_rgb": 1, "device_cmyk": 0, "output_intent": 0,
                "iccbased": 0, "fonts": 0, "images": 0, "layers": 1,
                "artboard_pt": [200.0, 200.0], "artboard_mm": [70.6, 70.6],
                "content_ops": {"rg": 1, "RG": 0, "k": 0, "K": 0},
                "content_streams": {"streams": 1, "decoded": 1},
                "color_mode": "rgb(operators)"}
        _kv = {"layers": "1", "trimmarks_ink": "GrayColor:gray=100:n=8",
               "artboard": "200x200", "open_paths": "5", "reopen_paths": "5",
               "raster": "0", "placed": "0", "viewBox": "0_0_200_200",
               "app_version": "28.3.0", "notes": "compat:app_default"}
        _opts = {"profile": "screen", "compat": "latest", "pdf": 1, "icc": 0,
                 "cmyk": 0, "bleed_mm": 0.0, "bleed_pt": 0.0, "layers": 0}
        # The Illustrator half is synthetic by default: building it needs no
        # Illustrator, so self-check stays fast. That proves the constructor's
        # output but NOT that the real chain feeds it -- if the JSX stopped
        # reporting app_version, the real run would say "unknown" and this would
        # still be green.
        #
        # VECTOR_AI_STUDIO_REAL_AI=1 switches that half to a real AI deliverable
        # when one exists, so a release can be gated on real evidence while the
        # everyday run stays cheap. Which one ran is written into the detail.
        # A real-artefact search must not use a field it is meant to guard.
        # Selecting on `engine` meant: delete `engine` -> candidate rejected ->
        # fall back to synthetic -> synthetic is produced by the constructor and
        # therefore always conforms -> green. The regression switched off the
        # check that exists to catch it. Pick by an INDEPENDENT signal instead:
        # each run dir carries a structure_readback.json whose `source` names the
        # engine that produced it. Once selected, a missing `engine` shows up as
        # missing like any other field.
        ai_label = "synthetic"
        ai_doc = None
        if os.environ.get("VECTOR_AI_STUDIO_REAL_AI") == "1":
            _root = Path(os.environ.get("VECTOR_AI_STUDIO_OUT")
                         or (Path.home() / ".vector-ai-studio" / "output"))
            _cands = []
            if _root.is_dir():
                # bounded: newest 200 only, so a large output tree cannot turn
                # the self-check into a directory walk
                _all = sorted(_root.rglob("deliverable.json"),
                              key=lambda q: q.stat().st_mtime, reverse=True)
                _cands = _all[:200]
            for _c in _cands:
                _sib = _c.parent / "structure_readback.json"
                try:
                    _srchk = json.loads(_sib.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if _srchk.get("source") != "illustrator-saveAs-reopen":
                    continue                     # that run was another engine
                try:
                    ai_doc = json.loads(_c.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                ai_label = "real:" + _c.parent.name
                break
            if ai_doc is None:
                # Asked for real coverage and did not get it: this must not be
                # green. A label that reads "not found" under a passing light is
                # the quietest form of the failure this whole assertion prevents.
                ai_label = "real requested, not found under " + str(_root)

        _real_missing = False
        if ai_doc is None:
            _real_missing = (os.environ.get("VECTOR_AI_STUDIO_REAL_AI") == "1")
            ai_doc = _la._deliverable(Path("final.ai"), _opts, _obs, _kv,
                                      engine="illustrator", engine_version="28.3.0")
        ai_extra, ai_missing, ai_typebad, ai_nested = _diff(ai_doc, "ai")

        # --- vectorcraft shape: the real thing, if the CLI is present.
        vc_extra = vc_missing = vc_typebad = vc_nested = []
        if _cli is None:
            # A missing optional engine normally downgrades this assertion to a
            # skip, which is honest: that half genuinely was not evaluated.
            # But "real coverage was requested and not found" is a known miss,
            # not an unknown. Skipping it would turn a definite failure into
            # "not evaluated", and an exit-code-only gate (0 vs non-zero) would
            # read the resulting exit 3 the same way it reads "optional engine
            # absent". Failure has to outrank skip here.
            a31_skip = not _real_missing
            a31_ok = not _real_missing
            a31_detail = ("ai(" + ai_label + ") extra=" + str(ai_extra or "-") +
                          " missing=" + str(ai_missing or "-") +
                          " typebad=" + str(ai_typebad or "-") +
                          " nested=" + str(ai_nested or "-") +
                          " | vectorcraft " +
                          ("skipped (no CLI) but REAL_AI was requested"
                           if _real_missing else "skipped (no CLI)"))
        else:
            vdir2 = tmp / "schema-diff-vc"
            _call("land_vc.py", ["--svg", str(GOOD / "sample.svg"),
                                 "--out", str(vdir2), "--profile", "print",
                                 "--run-id", "a31", "--json"], tmp)
            vc_doc = json.loads((vdir2 / "deliverable.json").read_text(encoding="utf-8"))
            vc_extra, vc_missing, vc_typebad, vc_nested = _diff(vc_doc, "vc")
            # Requested real coverage and did not get it => red, not green.
            a31_ok = (not _real_missing) and not (
                          ai_extra or ai_missing or ai_typebad or ai_nested or
                          vc_extra or vc_missing or vc_typebad or vc_nested)
            # "ai synthetic" is part of the detail on purpose: this half checks
            # the constructor's output, not the real Illustrator chain feeding it.
            a31_detail = ("ai(" + ai_label + ") extra=" + str(ai_extra or "-") +
                          " missing=" + str(ai_missing or "-") +
                          " typebad=" + str(ai_typebad or "-") +
                          " nested=" + str(ai_nested or "-") +
                          " | vc extra=" + str(vc_extra or "-") +
                          " missing=" + str(vc_missing or "-") +
                          " typebad=" + str(vc_typebad or "-") +
                          " nested=" + str(vc_nested or "-") +
                          " engine=" + str(vc_doc.get("engine")))
    except Exception as e:                      # noqa: BLE001
        a31_detail = type(e).__name__ + ": " + str(e)[:140]
    add("A31-deliverable-schema-no-drift", a31_ok, a31_detail, skipped=a31_skip)

    # Determinism: NOT added as a new assertion. QA asked for "same input twice,
    # identical numbers" when the perceptual layer brought floating point in.
    # A3-idempotent-byte-identical already runs the whole chain twice on one
    # input and byte-compares seven artefacts including score.json, which is
    # strictly stronger than re-running score.py alone.
    #
    # This was verified rather than assumed: injecting a set-ordered field into
    # score.json (string hash randomisation reorders it per process) turned BOTH
    # a candidate A32 and the existing A3 red. A second assertion for a property
    # the first one already catches would inflate the count without adding
    # coverage -- exactly the sort of decorative guard this suite exists to
    # avoid. A3 had simply never been mutation-tested before.

    # A33: the perceptual primitives still reproduce their published reference
    # vectors. Deliberately a re-run of the module's own self-check rather than
    # a copy of its numbers: a matrix edited in the module would otherwise be
    # checked against a fixture carrying the same mistake.
    try:
        import perceptual as _P
        problems = _P.selfcheck()
        add("A33-perceptual-reference-vectors", not problems,
            ("dE2000 max err %.3e over %d pairs; 4 contrast anchors; "
             "2 CVD pins; 9 matrix row sums = 1"
             % (_P.sharma_max_error(), len(_P.SHARMA_VECTORS)))
            if not problems else "; ".join(problems[:3]))
    except Exception as e:                      # noqa: BLE001
        add("A33-perceptual-reference-vectors", False,
            type(e).__name__ + ": " + str(e)[:140])

    # A34: the contrast path actually runs, and it catches the case that
    # motivated the whole perceptual layer.
    #
    # Without a declared pair the check reports not_applicable, which is honest
    # but means the code is never exercised -- a guard that no default run
    # reaches is a guard nobody would notice breaking. This drives a real pair
    # through score.py: pure red on black. WCAG alone passes it at 5.25:1; the
    # dichromatic observer sees 1.26:1. If the simulated-observation layer is
    # ever dropped or the matrices rot, this goes red.
    try:
        spec34 = json.loads((GOOD / "spec.json").read_text(encoding="utf-8"))
        spec34["contrast_pairs"] = [{"fg": "#FF0000", "bg": "#000000",
                                     "role": "meaning"}]
        p34 = tmp / "a34-spec.json"
        p34.write_text(json.dumps(spec34), encoding="utf-8")
        out34 = tmp / "a34-score.json"
        _call("score.py", ["--svg", str(GOOD / "sample.svg"),
                           "--judge", str(GOOD / "judge.json"),
                           "--spec", str(p34), "--out", str(out34),
                           "--run-id", "a34", "--json"], tmp)
        s34 = json.loads(out34.read_text(encoding="utf-8"))
        c34 = s34["scores"]["C"]["measured"]["perceptual"]["contrast"]
        pair = (c34.get("pairs") or [{}])[0]
        caught = (c34.get("status") == "warn"
                  and pair.get("status") == "fail"
                  and pair.get("min", 9) < 3.0
                  and pair.get("normal", 0) > 4.5)
        add("A34-contrast-catches-cvd-collapse", caught,
            "normal=" + str(pair.get("normal")) + " min=" + str(pair.get("min"))
            + " status=" + str(c34.get("status")))
    except Exception as e:                      # noqa: BLE001
        add("A34-contrast-catches-cvd-collapse", False,
            type(e).__name__ + ": " + str(e)[:140])

    # A35: a path that trips the short-segment escape valve must still leave
    # evidence -- in the FILE, not merely in the library return value.
    #
    # The escape valve (borrowed from fontspector) exists so that genuinely
    # detailed artwork is not punished for being detailed. The first
    # implementation wrote `short += count if not dense else 0`, which kept the
    # path out of the ratio -- correct -- and ALSO threw away the count, so
    # "a circle cut into 200 chords" and "nothing to report" looked identical.
    #
    # The first version of THIS assertion called analyze_svg() directly and
    # checked its return dict. That proved the library kept the numbers while
    # score.json still dropped them: the fix had reached the function and
    # stopped there. Same shape as the failures this project keeps closing --
    # measured, but not in any file a reader opens. So it now runs score.py
    # end to end and reads score.json, which is what a reviewer actually gets.
    try:
        import math as _m
        _n = 200
        _d = "M" + " L".join(
            "%.4f,%.4f" % (100 + 70 * _m.cos(2 * _m.pi * i / _n),
                           100 + 70 * _m.sin(2 * _m.pi * i / _n))
            for i in range(_n)) + " Z"
        _svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
                '<g id="a"><path d="' + _d + '" fill="none" stroke="#000"/></g></svg>')
        _p35 = tmp / "a35-flattened.svg"
        _p35.write_text(_svg, encoding="utf-8")
        _out35 = tmp / "a35-score.json"
        _call("score.py", ["--svg", str(_p35),
                           "--judge", str(GOOD / "judge.json"),
                           "--spec", str(GOOD / "spec.json"),
                           "--out", str(_out35),
                           "--run-id", "a35", "--json"], tmp)
        _d35 = json.loads(_out35.read_text(encoding="utf-8"))["scores"]["D"]["measured"]
        _turns = _d35.get("dense_turns_deg") or []
        kept = (_d35.get("dense_paths") == 1
                and _d35.get("dense_short_segments") == _n
                and len(_turns) == 1 and _turns[0] < 5.0
                and (_d35.get("dense_corner_fracs") or [1])[0] == 0.0)
        add("A35-dense-path-keeps-evidence", kept,
            "in score.json: dense=" + str(_d35.get("dense_paths"))
            + " kept_short=" + str(_d35.get("dense_short_segments"))
            + " turns=" + str(_turns)
            + " corner=" + str(_d35.get("dense_corner_fracs")))
    except Exception as e:                      # noqa: BLE001
        add("A35-dense-path-keeps-evidence", False,
            type(e).__name__ + ": " + str(e)[:140])

    # A36-A38: the three P2 items that survived five QA rounds (report
    # QA报告-第17轮.md §6). Each assertion is written so that reverting the
    # fix makes exactly that assertion red -- a guard that cannot fail is
    # decoration, not evidence. All three drive land_vc._build_deliverable,
    # the seam main() itself calls: the original defects lived in the wiring
    # (hardcoded icc, a second write after construction, `.split()[-1]`), and
    # guards aimed at the helpers stayed green when the wiring was reverted.
    # A36 additionally drives the AI side through land_ai._resolve_options and
    # land_ai._make_jsx, so the constant read and the substitution both have to
    # hold (QA round 18 M39, round 19 M40/M42: each kept a shallower guard green).
    try:
        _s36a = importlib.util.spec_from_file_location("r24_land_ai", SCRIPTS / "land_ai.py")
        _la36 = importlib.util.module_from_spec(_s36a)
        _s36a.loader.exec_module(_la36)
        _s36b = importlib.util.spec_from_file_location("r24_land_vc", SCRIPTS / "land_vc.py")
        _lv36 = importlib.util.module_from_spec(_s36b)
        _s36b.loader.exec_module(_lv36)

        _obs36 = {"has_ai_private": True, "content_ops": {}, "content_streams": {},
                  "color_mode": "cmyk", "device_cmyk": 0, "device_rgb": 1,
                  "iccbased": 0, "output_intent": 0, "artboard_pt": [200, 200],
                  "sha256": "0" * 64}
        _kv36 = {"layers": "5", "notes": ""}

        # A36: the same physical fact -- no /OutputIntent, no /ICCBased --
        # must be "a request not honoured" on BOTH engines for the same
        # profile. Before r24 the VC profile never declared the request, so
        # its answer lived in `warnings` and was incomparable.
        # The AI half is driven the way main() drives it: profile name in,
        # _resolve_options reads PROFILE_DEFAULTS, _make_jsx substitutes the
        # value Illustrator actually receives, _deliverable records it. A
        # hand-built opts dict (r25/r26) left both hops blind: QA round 18's
        # M39 set PROFILE_DEFAULTS["print"]["icc"]=0, round 19's M40 stopped
        # reading it there, M42 stopped substituting it into the JSX -- each
        # kept this line green while the JSX told Illustrator EMBED_ICC=0 and
        # the manifest still claimed true.
        # The args come from the parser a user drives, not a hand-written
        # namespace: QA round 20 (M48/M49) changed the real defaults and this
        # line stayed green. parse_args also proves "--profile print" is a
        # choice the CLI accepts.
        _parser36 = _la36._build_parser()
        _args36 = _parser36.parse_args(
            ["--out", str(tmp / "a.ai"), "--profile", "print"])
        # The documented defaults are part of the contract: "--profile screen"
        # is what a user gets with no flags. QA round 20's M49 changed that
        # default and every guard stayed green, because the old guard built its
        # own namespace and never looked at the parser.
        _def36 = _parser36.parse_args(["--out", str(tmp / "a.ai")])
        _def_ok36 = (_def36.profile == "screen" and _def36.embed_icc == ""
                     and _def36.cmyk == "" and _def36.pdf_compatible == "")
        _opts_ai36 = _la36._resolve_options(_args36)
        # main() copies the profile onto the opts at land_ai.py:441; without it
        # the AI half reports profile="" while the VC half reports "print"
        # (QA round 20, Q20-4: the detail line claimed "same profile" and no
        # assertion looked at the field).
        _opts_ai36["profile"] = _args36.profile
        _d_ai = _la36._deliverable(
            tmp / "a.ai", _opts_ai36, _obs36, _kv36,
            engine="illustrator", engine_version="28.3.0")
        # The substitution is the last hop before the engine: ai_bridge.jsx
        # reads `var EMBED_ICC = ("__EMBED_ICC__" === "1")` and passes it to
        # opts.embedICCProfile. Assert on the text handed to Illustrator, not
        # on the Python dict -- a dict-only guard cannot see M42.
        _jsx36 = _la36._make_jsx(tmp / "a.svg", tmp / "a.ai", False,
                                 tmp / "result.txt", _opts_ai36)
        _jsx_icc36 = 'var EMBED_ICC = ("1" === "1")' in _jsx36
        # The VC side goes through the seam main() itself calls, so reverting
        # the `icc` wiring (not just the profile constant) turns this red.
        _d_vc = _lv36._build_deliverable(tmp / "a.ai", "print",
                                         _lv36.PROFILE_DEFAULTS["print"],
                                         _obs36, _kv36,
                                         "vectorcraft-cli 0.22.0", [], None)
        _u_ai = [x for x in _d_ai["unverified"] if "OutputIntent" in x]
        _u_vc = [x for x in _d_vc["unverified"] if "OutputIntent" in x]
        _ok36 = (bool(_u_ai) and bool(_u_vc)
                 and _d_ai["save_options"]["embed_icc"] is True
                 and _d_vc["save_options"]["embed_icc"] is True
                 and _jsx_icc36
                 and _def_ok36
                 and _d_ai["profile"] == _d_vc["profile"] == "print"
                 and not any("未嵌入 ICC" in x for x in _d_ai["warnings"])
                 and not any("未嵌入 ICC" in x for x in _d_vc["warnings"]))
        add("A36-cross-engine-unverified-bucket", _ok36,
            "same profile print, same bytes: ai.unverified=" + str(len(_u_ai))
            + " vc.unverified=" + str(len(_u_vc))
            + " embed_icc=" + str((_d_ai["save_options"]["embed_icc"],
                                   _d_vc["save_options"]["embed_icc"]))
            + " jsx_icc=" + str(_jsx_icc36)
            + " defaults=" + str(_def_ok36))

        # A37: a warning emitted by three steps must appear once, and the
        # deliverable must carry that exact de-duplicated list (not a second
        # copy written after construction).
        # The steps are built by the parser that reads the CLI's own output,
        # because that is where the warnings enter the chain. QA round 19's M43
        # dropped `result.warnings` inside parse_jsonl and every assertion
        # stayed green while tool_warnings silently became [].
        _jsonl37 = "\n".join([
            '{"step": "import", "result": {"warnings": ["font missing: Helvetica"]}}',
            '{"step": "save", "result": {"warnings": ["font missing: Helvetica", '
            '"no output intent"]}}',
            "not a json line",
        ])
        _steps37 = _lv36.parse_jsonl(_jsonl37)
        _w37 = _lv36._collect_export_warnings(_steps37)
        # Through the seam: covers the de-duplication AND the `tool_warnings=`
        # wiring. Feeding the helper directly left the wiring blind, which is
        # precisely where the r23 defect lived (a second write after the fact).
        _d37 = _lv36._build_deliverable(tmp / "a.ai", "print",
                                        _lv36.PROFILE_DEFAULTS["print"],
                                        _obs36, _kv36,
                                        "vectorcraft-cli 0.22.0", _steps37, None)
        _ok37 = (_w37 == ["font missing: Helvetica", "no output intent"]
                 and len(_steps37) == 2          # the non-JSON line is not a step
                 and _d37["tool_warnings"] == _w37
                 and len(_d37["tool_warnings"]) == len(set(_d37["tool_warnings"])))
        add("A37-tool-warnings-deduplicated", _ok37,
            "in deliverable.json: tool_warnings=" + str(_d37["tool_warnings"]))

        # A38: a version string with a trailing build/platform suffix must
        # yield the dotted version, and an unparseable one must say unknown.
        _cases38 = [("vectorcraft-cli 0.22.0 (build 20261101 win-x64)", "0.22.0"),
                    ("tool 1.2", "1.2"), ("no version here", "unknown"),
                    ("", "unknown")]
        # Through the seam: the field a client reads is deliverable["engine_version"].
        # Calling the parser directly stays green even if the wiring goes back to
        # `.split()[-1]` -- and that call site was where the r23 defect lived.
        _got38 = []
        for _txt, _ in _cases38:
            _d38 = _lv36._build_deliverable(tmp / "a.ai", "print",
                                            _lv36.PROFILE_DEFAULTS["print"],
                                            _obs36, _kv36, _txt, [], None)
            _got38.append((_txt, _d38.get("engine_version")))
        _ok38 = all(g == e for (_, g), (_, e) in zip(_got38, _cases38))
        add("A38-engine-version-parser", _ok38,
            "parse=" + str(_got38))

        # A39: probe_source is the only place the CLI's "which fonts did you
        # substitute" answer enters the manifest -- invisible in file size, path
        # counts and layer counts alike. Two rounds of QA broke the first
        # version of this guard: M44 (return unavailable unconditionally) was
        # caught, but the payload was invented ({"name", "status":
        # "substituted"}), so land_vc's consumer -- `if f.get("missing")` -- had
        # no coverage at all (QA round 20, M50/M51), and the two error branches
        # could be flipped to available=True unnoticed (M45/M53). The payload
        # below is a trimmed copy of real `vectorcraft info` output.
        _real_run_cli39 = _lv36.run_cli

        def _fake39(payload, rc=0):
            def _run(cli, argv, timeout=300):
                return rc, payload, ""
            return _run

        _font39 = {"family": "DefinitelyMissingFontXYZ", "style": "Regular",
                   "status": "missing", "missing": True, "missingGlyphs": 0,
                   "objects": 1, "runs": 1,
                   "resolved": {"family": "Source Sans 3", "style": "Regular"}}
        _payload39 = json.dumps({"fonts": [_font39],
                                 "importWarnings": ["font substituted: DefinitelyMissingFontXYZ"],
                                 "info": {"objects": 12, "colorMode": "CMYK"}})
        try:
            _lv36.run_cli = _fake39(_payload39)
            _p39 = _lv36.probe_source(tmp / "vectorcraft-cli", tmp / "a.svg")
            _w39 = _lv36._font_warnings(_p39)
            # The CLI omits `resolved` when it could not name a replacement; the
            # fallback text is then what a client reads, so it is asserted too.
            _lv36.run_cli = _fake39(json.dumps({
                "fonts": [{"family": "NoResolveFont", "status": "missing", "missing": True}],
                "importWarnings": [], "info": {"objects": 1, "colorMode": "CMYK"}}))
            _p39b = _lv36.probe_source(tmp / "vectorcraft-cli", tmp / "a.svg")
            _w39b = _lv36._font_warnings(_p39b)
            _lv36.run_cli = _fake39("boom", rc=1)
            _rc39 = _lv36.probe_source(tmp / "vectorcraft-cli", tmp / "a.svg")
            _lv36.run_cli = _fake39("this is not json")
            _js39 = _lv36.probe_source(tmp / "vectorcraft-cli", tmp / "a.svg")
        finally:
            _lv36.run_cli = _real_run_cli39
        # Matched by regex + groups, not by exact text: QA round 21 found that
        # a punctuation change turns an exact-string check red for a reason no
        # client would ever notice. The substance -- which font, replaced by
        # what -- is what the assertion is about.
        _pat39 = r"源文件字体缺失：(.+?)，产物中已用 (.+?) 替代"
        _m39 = re.search(_pat39, _w39[0]) if _w39 else None
        _m39b = re.search(_pat39, _w39b[0]) if _w39b else None
        _ok39 = (_p39.get("available") is True
                 and _p39.get("fonts") == [_font39]
                 and _p39.get("import_warnings") == ["font substituted: DefinitelyMissingFontXYZ"]
                 and _p39.get("objects") == 12
                 and _p39.get("color_mode") == "CMYK"
                 and _m39 is not None
                 and _m39.group(1) == "DefinitelyMissingFontXYZ"
                 and _m39.group(2) == "Source Sans 3"
                 and _m39b is not None
                 and _m39b.group(1) == "NoResolveFont"
                 and _m39b.group(2) == "未知替代字体"
                 and _rc39.get("available") is False
                 and _js39.get("available") is False)
        add("A39-font-probe-carried", _ok39,
            "available=" + str(_p39.get("available"))
            + " fonts=" + str(len(_p39.get("fonts") or []))
            + " warn=" + str(len(_w39))
            + " fallback=" + str(len(_w39b))
            + " family=" + str(_m39.group(1) if _m39 else None)
            + " rc_branch=" + str(_rc39.get("available"))
            + " nonjson=" + str(_js39.get("available")))

        # Module-wide, value-flow based, and write-aware. QA round 21 showed
        # the r28 guard could be walked around three ways (the call moved into a
        # helper outside main(); a stray write into save_opts; a lookup through
        # globals()) and false-red on four legitimate shapes (renaming the
        # variable, passing a copy, passing an alias). The rules below keep the
        # promise without freezing the spelling:
        #   * every _make_jsx / _deliverable call in the module must receive an
        #     argument that still carries the resolved options;
        #   * that argument may be an alias or a copy -- what must not happen is
        #     a DIFFERENT value (PROFILE_DEFAULTS["screen"], a literal, ...);
        #   * nothing may write into the resolved options afterwards, except
        #     the one documented line that copies the profile name onto it.
        import ast as _ast
        _tree40 = _ast.parse((SCRIPTS / "land_ai.py").read_text(encoding="utf-8"))
        _assigns40 = {}
        for _n in _ast.walk(_tree40):
            if isinstance(_n, _ast.Assign) and len(_n.targets) == 1:
                _t = _n.targets[0]
                if isinstance(_t, _ast.Name):
                    _assigns40.setdefault(_t.id, []).append(_n.value)

        def _carries40(name, depth=0):
            """True when this name still holds the resolved options."""
            if depth > 6 or name not in _assigns40:
                return False
            for _v in _assigns40[name]:
                if isinstance(_v, _ast.Call):
                    _fn = getattr(_v.func, "id", "")
                    if _fn == "_resolve_options":
                        continue
                    if _fn in ("dict", "list") and _v.args and isinstance(_v.args[0], _ast.Name) \
                            and _carries40(_v.args[0].id, depth + 1):
                        continue
                    return False
                if isinstance(_v, _ast.Name):
                    if _carries40(_v.id, depth + 1):
                        continue
                    return False
                if isinstance(_v, _ast.Attribute) and _v.attr == "copy" \
                        and isinstance(_v.value, _ast.Name) \
                        and _carries40(_v.value.id, depth + 1):
                    continue
                return False
            return True

        def _arg_carries40(arg, depth=0):
            """Does this argument still carry the resolved options?

            A plain name, an alias, or a copy of them all do. A DIFFERENT value
            (PROFILE_DEFAULTS["screen"], a literal, a fresh dict) does not.
            """
            if depth > 6:
                return False
            if isinstance(arg, _ast.Name):
                return _carries40(arg.id, depth)
            if isinstance(arg, _ast.Call):
                _fn = getattr(arg.func, "id", "")
                if _fn in ("dict", "list") and arg.args and isinstance(arg.args[0], _ast.Name):
                    return _carries40(arg.args[0].id, depth + 1)
                return False
            if isinstance(arg, _ast.Attribute) and arg.attr == "copy" \
                    and isinstance(arg.value, _ast.Name):
                return _carries40(arg.value.id, depth + 1)
            return False

        _site40 = {"resolved_from_options": bool([n for n in _assigns40 if _carries40(n)]),
                   "profile_copied": False, "writes_after_resolve": [],
                   "dynamic_dispatch": [],
                   "make_jsx": [0, 0], "deliverable": [0, 0]}  # [calls, wired_calls]
        for _n in _ast.walk(_tree40):
            if isinstance(_n, _ast.Assign) and len(_n.targets) == 1:
                _t = _n.targets[0]
                if isinstance(_t, _ast.Subscript) \
                        and isinstance(_t.value, _ast.Name) \
                        and _carries40(_t.value.id):
                    _is_profile = (isinstance(_n.value, _ast.Attribute)
                                   and _n.value.attr == "profile"
                                   and isinstance(_n.value.value, _ast.Name)
                                   and _n.value.value.id == "args")
                    if _is_profile:
                        _site40["profile_copied"] = True
                    else:
                        _site40["writes_after_resolve"].append(_ast.unparse(_t))
            if isinstance(_n, _ast.Call):
                _fn = getattr(_n.func, "id", "")
                # A call the scanner cannot attribute (globals()["_make_jsx"]...)
                # would simply stop being counted, so dynamic dispatch is
                # refused outright: QA round 21's B8 walked straight past the
                # r28 guard this way.
                if _fn in ("globals", "eval", "exec", "getattr"):
                    _site40["dynamic_dispatch"].append(_ast.unparse(_n))
                if _fn in ("_make_jsx", "_deliverable"):
                    _site40[_fn.lstrip("_")][0] += 1
                    for _a in _n.args:
                        if _arg_carries40(_a):
                            _site40[_fn.lstrip("_")][1] += 1
                            break
        _ok40 = (_site40["resolved_from_options"] and _site40["profile_copied"]
                 and not _site40["writes_after_resolve"]
                 and not _site40["dynamic_dispatch"]
                 and _site40["make_jsx"][0] > 0
                 and _site40["make_jsx"][1] == _site40["make_jsx"][0]
                 and _site40["deliverable"][0] > 0
                 and _site40["deliverable"][1] == _site40["deliverable"][0])
        add("A40-module-call-site-wiring", _ok40, "static: " + str(_site40))

        # A41: E is the one dimension that is a set comparison with its own
        # thresholds, and until r28 every fixture recorded E="N/A" -- the branch
        # that computes Jaccard had never run in any test (QA round 20 found the
        # gap while reviewing what E covers). Three cases: an exact reference
        # must score excellent, a disjoint one must fail, no reference must stay
        # N/A (L5: restricted capability, not a silent pass).
        # The reference is written out by hand from reading sample.svg, NOT
        # produced by score.py's own extractor: QA round 21 showed that a
        # reference built from _svg_tokens() passes even when the extractor is
        # broken (both sides rot together). The expected Jaccard values below
        # are hand-computed from the intersection/union definition.
        _svg41 = FIX / "good" / "sample.svg"
        _exact41 = {"schema": "vector-ai-studio/reference-v1",
                    "palette": ["#111111", "#2f6fed", "#ffb020", "#ffffff"],
                    "stroke_width": ["3.0"], "corner_radius": [], "font_family": []}
        # 2 of 4 palette colours hit, stroke identical, the two empty buckets
        # deliberately non-empty in the reference so their Jaccard is 0.
        _partial41 = {"schema": "vector-ai-studio/reference-v1",
                      "palette": ["#2f6fed", "#ffb020"], "stroke_width": ["3.0"],
                      "corner_radius": ["4.0"], "font_family": ["SomeFont"]}
        # Everything identical except the palette (2 of 4): the average 0.875 is
        # above the excellent threshold, so only the palette rule can keep this
        # from being excellent. QA round 21's D7 deleted that rule and went
        # unnoticed.
        _half41 = {"schema": "vector-ai-studio/reference-v1",
                   "palette": ["#2f6fed", "#ffb020"], "stroke_width": ["3.0"],
                   "corner_radius": [], "font_family": []}
        _ref41 = tmp / "ref_exact.json"
        _pre41 = tmp / "ref_partial.json"
        _pha41 = tmp / "ref_half.json"
        L.write_json_atomic(_ref41, _exact41)
        L.write_json_atomic(_pre41, _partial41)
        L.write_json_atomic(_pha41, _half41)
        _common41 = ["--svg", str(_svg41), "--judge", str(FIX / "good" / "judge.json"),
                     "--spec", str(FIX / "good" / "spec.json"), "--json"]
        _e41 = {}
        for _name41, _ref41p in (("exact", _ref41), ("partial", _pre41), ("half", _pha41)):
            _rc41, _env41, _ = _call("score.py", _common41 + ["--reference", str(_ref41p)], tmp)
            _e41[_name41] = ((_env41.get("data") or {}).get("scores") or {}).get("E")
        _rc41n, _env41n, _ = _call("score.py", _common41, tmp)
        _e41["none"] = ((_env41n.get("data") or {}).get("scores") or {}).get("E")
        _th41 = L.load_thresholds()
        _lv41 = _th41["score_levels"]
        _ex41 = _e41["exact"] if isinstance(_e41["exact"], dict) else {}
        _pa41 = _e41["partial"] if isinstance(_e41["partial"], dict) else {}
        _ha41 = _e41["half"] if isinstance(_e41["half"], dict) else {}
        _jac41 = (_ex41.get("measured") or {}).get("jaccard", {})
        _n_tok41 = len(_th41["E"]["tokens"])
        _ok41 = (_ex41.get("score") == _lv41["excellent"]
                 and len(_jac41) == _n_tok41 == 4
                 and set(_jac41.values()) == {1.0}
                 # partial: 2/4 palette = 0.5, stroke = 1.0, two empty buckets = 0
                 and (_pa41.get("measured") or {}).get("jaccard") == {
                     "palette": 0.5, "stroke_width": 1.0,
                     "corner_radius": 0.0, "font_family": 0.0}
                 and abs(float((_pa41.get("measured") or {}).get("average", -1)) - 0.375) < 1e-9
                 and _pa41.get("score") == _lv41["fail"]
                 # half: average 0.875 >= excellent threshold, palette 0.5 < 1.0
                 # -> must be pass, not excellent
                 and _ha41.get("score") == _lv41["pass"]
                 and _e41["none"] == "N/A")
        add("A41-brand-reference-jaccard", _ok41,
            "E exact=" + str(_ex41.get("score")) + " partial=" + str(_pa41.get("score"))
            + " half=" + str(_ha41.get("score")) + " none=" + str(_e41["none"])
            + " tokens=" + str(len(_jac41)))
    except Exception as e:                      # noqa: BLE001
        # Only fill in assertions that never ran. Re-adding an ID that already
        # produced a result would give one guard two contradictory verdicts.
        for _id in ("A36-cross-engine-unverified-bucket",
                    "A37-tool-warnings-deduplicated",
                    "A38-engine-version-parser",
                    "A39-font-probe-carried",
                    "A40-main-call-site-wiring",
                    "A41-brand-reference-jaccard"):
            if not any(x["id"] == _id for x in a):
                add(_id, False, type(e).__name__ + ": " + str(e)[:140])

    # ---- A42/A43/A44: the "supplied photo -> print file" route -------------
    # Codifies what one real job taught: a traced photo keeps the studio
    # lighting, a vector has no physical size, and the tracer hides its settings
    # one level down and punches highlights out if told to ignore white.
    try:
        from PIL import Image as _Im

        def _run_script(name, argv):
            _p = subprocess.run([sys.executable, str(ROOT / "scripts" / name)] + argv,
                                capture_output=True, text=True, timeout=1200)
            try:
                return _p.returncode, json.loads(_p.stdout)
            except Exception:                      # noqa: BLE001
                return _p.returncode, {}

        # A42: delight's contract is now deterministic, not heuristic.
        #  (a) a lit ramp still flattens; (b) with an explicit mask, a blown
        #  specular is repaired; (c) WITHOUT a mask, EVERY near-white pixel is
        #  byte-identical for all five geometries QA found (axial slit,
        #  diagonal slit, curved slit, thin highlight, bridged highlight).
        #  The third check is the root-cause guarantee: no geometry can be a
        #  counterexample because unclassified pixels are never touched.
        import math as _math
        _pw, _ph = 420, 300
        _ramp = _Im.new("RGB", (_pw, _ph), (255, 255, 255))
        _rp = _ramp.load()
        for _y in range(20, _ph - 20):
            for _x in range(20, _pw - 20):
                _k = 0.55 + 0.60 * (_x - 20) / float(_pw - 40)
                _rp[_x, _y] = (int(198 * _k), int(34 * _k), int(40 * _k))
        _a42 = tmp / "a42-ramp.png"; _ramp.save(_a42)
        _rc1, _d1 = _run_script("delight.py",
                                ["--in", str(_a42), "--out", str(tmp / "a42-flat.png"),
                                 "--strength", "1.0", "--blur", "40", "--sat", "1.0", "--json"])
        _before = [_rp[x, 150][0] for x in range(60, _pw - 60)]
        _flat = _Im.open(tmp / "a42-flat.png").convert("RGB")
        _after = [_flat.load()[x, 150][0] for x in range(60, _pw - 60)]
        _r_before, _r_after = max(_before) - min(_before), max(_after) - min(_after)
        _flat_ok = (_rc1 == 0 and _r_before > 40 and _r_after <= 0.40 * _r_before)
        _warn_ok = bool((_d1.get("data") or {}).get("mask_mode") == "conservative")

        # explicit mask repairs a blown specular, and does so for a thin one too
        _masked_ok = True
        for _nm, _im in (("bridged", None), ("thin", None)):
            _sp = _Im.new("RGB", (240, 160), (255, 255, 255))
            _spp = _sp.load()
            for _y in range(40, 120):
                for _x in range(40, 200):
                    _spp[_x, _y] = (198, 34, 40)
            if _nm == "bridged":
                for _y in range(70, 100):
                    for _x in range(100, 140): _spp[_x, _y] = (255, 255, 255)
                for _y in range(0, 70):
                    for _x in range(110, 130): _spp[_x, _y] = (255, 255, 255)
            else:
                for _y in range(60, 110):
                    for _x in range(116, 126): _spp[_x, _y] = (255, 255, 255)
                for _y in range(0, 60):
                    for _x in range(110, 130): _spp[_x, _y] = (255, 255, 255)
            _src = tmp / ("a42-%s.png" % _nm); _sp.save(_src)
            _mk = _Im.new("L", _sp.size, 0)
            _ImDraw = __import__("PIL.ImageDraw", fromlist=["Draw"])
            _ImDraw.Draw(_mk).rectangle([40, 40, 199, 119], fill=255)
            _mkp = tmp / ("a42-%s-mask.png" % _nm); _mk.save(_mkp)
            _dsto = tmp / ("a42-%s-masked.png" % _nm)
            _rc, _dj = _run_script("delight.py",
                                   ["--in", str(_src), "--out", str(_dsto), "--mask", str(_mkp),
                                    "--strength", "0", "--blur", "20", "--json"])
            _c = _Im.open(_dsto).convert("RGB").load()[120, 85]
            _masked_ok = _masked_ok and (_rc == 0
                        and (_dj.get("data") or {}).get("mask_mode") == "explicit"
                        and (_dj.get("data") or {}).get("specular_repaired", 0) > 0
                        and _c[0] > 120 and _c[0] > _c[2] + 30)

        # conservative mode: near-white pixels never change, for every geometry
        _geoms = {}
        _g0 = _Im.new("RGB", (240, 160), (255, 255, 255)); _q = _g0.load()
        for _y in list(range(40, 70)) + list(range(90, 120)):
            for _x in range(40, 200): _q[_x, _y] = (198, 34, 40)
        _geoms["axial"] = _g0
        _g1 = _Im.new("RGB", (240, 160), (198, 34, 40)); _q = _g1.load()
        for _y in range(160):
            for _x in range(240):
                if abs(_x - _y) <= 6: _q[_x, _y] = (255, 255, 255)
        _geoms["diagonal"] = _g1
        _g2 = _Im.new("RGB", (240, 160), (198, 34, 40)); _q = _g2.load()
        for _x in range(240):
            _cy = 80 + 30 * _math.sin(_x / 28.0)
            for _y in range(160):
                if abs(_y - _cy) <= 6: _q[_x, _y] = (255, 255, 255)
        _geoms["curved"] = _g2
        _g3 = _Im.new("RGB", (240, 160), (255, 255, 255)); _q = _g3.load()
        for _y in range(40, 120):
            for _x in range(40, 200): _q[_x, _y] = (198, 34, 40)
        for _y in range(60, 110):
            for _x in range(116, 126): _q[_x, _y] = (255, 255, 255)
        for _y in range(0, 60):
            for _x in range(110, 130): _q[_x, _y] = (255, 255, 255)
        _geoms["thin"] = _g3
        _g4 = _Im.new("RGB", (240, 160), (255, 255, 255)); _q = _g4.load()
        for _y in range(40, 120):
            for _x in range(40, 200): _q[_x, _y] = (198, 34, 40)
        for _y in range(70, 100):
            for _x in range(100, 140): _q[_x, _y] = (255, 255, 255)
        for _y in range(0, 70):
            for _x in range(110, 130): _q[_x, _y] = (255, 255, 255)
        _geoms["bridged"] = _g4
        _invariant_ok = True
        _dye_report = []
        for _gn, _gi in _geoms.items():
            _gs = tmp / ("a42-g-%s.png" % _gn); _gi.save(_gs)
            _go = tmp / ("a42-g-%s-out.png" % _gn)
            _rc, _ = _run_script("delight.py",
                                 ["--in", str(_gs), "--out", str(_go),
                                  "--strength", "0.65", "--blur", "20", "--json"])
            _a, _b = _gi.load(), _Im.open(_go).convert("RGB").load()
            _dye = sum(1 for _y in range(160) for _x in range(240)
                       if min(_a[_x, _y]) >= 250 and _a[_x, _y] != _b[_x, _y])
            _dye_report.append("%s:%d" % (_gn, _dye))
            _invariant_ok = _invariant_ok and _rc == 0 and _dye == 0
        add("A42-delight-flattens-lighting", _flat_ok and _warn_ok and _masked_ok and _invariant_ok,
            "row-range " + str(_r_before) + "->" + str(_r_after)
            + " masked=" + str(_masked_ok) + " near-white-dyed=" + ",".join(_dye_report))

        # A43: physical size, device resolution and bleed must land exactly
        _rc3, _d3 = _run_script("export_print.py",
                                ["--in", str(_a42), "--out", str(tmp / "a43.jpg"),
                                 "--height-mm", "100", "--dpi", "300",
                                 "--bleed-mm", "3", "--json"])
        _e = _d3.get("data") or {}
        _bleed = int(round(3 / 25.4 * 300))
        _th = int(round(100 / 25.4 * 300))
        _tw = int(round(100 * (_pw / float(_ph)) / 25.4 * 300))
        _rc4, _ = _run_script("export_print.py",
                              ["--in", str(_a42), "--out", str(tmp / "a43b.jpg"),
                               "--height-mm", "100", "--dpi", "5", "--json"])
        # a full-bleed red job must not get a white bleed band (QA23 P2-1)
        _red = _Im.new("RGB", (200, 150), (198, 34, 40))
        _a43r = tmp / "a43-fullred.png"
        _red.save(_a43r)
        _rc6, _d6 = _run_script("export_print.py",
                                ["--in", str(_a43r), "--out", str(tmp / "a43-red.jpg"),
                                 "--height-mm", "100", "--dpi", "300", "--bleed-mm", "3",
                                 "--json"])
        _corner = _Im.open(tmp / "a43-red.jpg").convert("RGB").load()[2, 2]
        _bleed_ok = (_rc6 == 0 and _corner[0] > 120 and _corner[0] > _corner[2] + 40)
        # corrupt input must produce the standard envelope, not a traceback (P2-2)
        _bad = tmp / "a43-broken.png"
        _bad.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-png")
        _rc7, _d7 = _run_script("export_print.py",
                                ["--in", str(_bad), "--out", str(tmp / "a43-bad.jpg"),
                                 "--height-mm", "100", "--json"])
        _env_ok = (_rc7 == L.EXIT_ERROR and _d7.get("code") == L.EXIT_ERROR
                   and any(x.get("code") == "E-INTERNAL" for x in _d7.get("errors", [])))
        _ok43 = (_rc3 == 0
                 and _e.get("trim_px") == [_tw, _th]
                 and _e.get("canvas_px") == [_tw + 2 * _bleed, _th + 2 * _bleed]
                 and _e.get("dpi") == 300
                 and _rc4 == L.EXIT_USAGE
                 and _bleed_ok and _env_ok)
        add("A43-export-print-dimensions", _ok43,
            "trim=" + str(_e.get("trim_px")) + " canvas=" + str(_e.get("canvas_px"))
            + " bad-dpi-exit=" + str(_rc4)
            + " red-corner=" + str(_corner) + " corrupt=" + str(_rc7))

        # A44: nested settings + a missing engine must never read as success
        _spec2 = importlib.util.spec_from_file_location(
            "trace_ref_under_test", ROOT / "scripts" / "trace_ref.py")
        _tr = importlib.util.module_from_spec(_spec2)
        _spec2.loader.exec_module(_tr)
        _nested = _tr.trace_params(64, 85, 20, 2, False)
        _nesting_ok = ("params" in _nested and "colors" not in _nested
                       and _nested["params"].get("ignoreWhite") is False)
        _orig = _tr.find_cli
        _tr.find_cli = lambda: None
        _missing = False
        try:
            _tr.require_cli()
        except L.ToolError as _ex:
            _missing = (_ex.exit_code == L.EXIT_MISSING
                        and _ex.code == "E-CAPABILITY-MISSING")
        finally:
            _tr.find_cli = _orig
        add("A44-trace-ref-contract", _nesting_ok and _missing,
            "nested=" + str(_nesting_ok) + " missing-engine=" + str(_missing))

        _corrupt = tmp / "a45-corrupt.png"
        _corrupt.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-png")
        _envs = []
        for _nm, _argv in (("delight.py", ["--in", str(_corrupt), "--out", str(tmp / "a45-d.png")]),
                           ("export_print.py", ["--in", str(_corrupt), "--out",
                                                str(tmp / "a45-e.jpg"), "--height-mm", "100"]),
                           ("trace_ref.py", ["--in", str(_corrupt), "--out", str(tmp / "a45-t.ai")])):
            _rc, _env = _run_script(_nm, _argv + ["--json"])
            _err_codes = [x.get("code") for x in _env.get("errors", [])]
            _envs.append((_nm, _rc == L.EXIT_ERROR, _env.get("code") == L.EXIT_ERROR,
                          any(c in ("E-INTERNAL", "E-TRACE") for c in _err_codes)))
        _env_ok = all(a and b and c for _, a, b, c in _envs)
        add("A45-uniform-error-envelope", _env_ok,
            ";".join("%s:%s%s%s" % (n, a, b, c) for n, a, b, c in _envs))

        # A46: the Craft-only landing path must really produce a PDF/X master
        # with an output intent, bleed boxes and CMYK content -- not just claim
        # it in the deliverable. Skipped (as a skip, never as a pass) when the
        # VectorCraft CLI is absent.
        _lc_svg = tmp / "a46.svg"
        _lc_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">'
            '<rect x="0" y="0" width="200" height="100" fill="#D3122A"/>'
            '<circle cx="100" cy="50" r="30" fill="#F5B915"/></svg>', encoding="utf-8")
        _lc_out = tmp / "a46-run"
        _rc9, _d9 = _run_script("land_craft.py",
                                ["--svg", str(_lc_svg), "--out", str(_lc_out),
                                 "--profile", "print", "--bleed-mm", "3",
                                 "--pdf-standard", "pdfX1a", "--json"])
        _x = _d9.get("data") or {}
        if _d9.get("errors") and any(e.get("code") == "E-CAPABILITY-MISSING"
                                    for e in _d9["errors"]):
            add("A46-craft-only-landing", None, "VectorCraft CLI unavailable", skipped=True)
        else:
            _px = _x.get("pdf_x") or {}
            _ok46 = (_rc9 == 0 and _x.get("h1_pass") is True
                     and _x.get("raster") == 0 and _x.get("placed") == 0
                     and (_x.get("paths") or 0) > 0
                     and _px.get("gts_pdfx", 0) >= 1
                     and _px.get("output_intents", 0) >= 1
                     and bool(_px.get("trimbox")) and bool(_px.get("bleedbox"))
                     # two shapes on a 200x100 artboard: page content carries
                     # tens of operators. Counting ICC/font streams as page
                     # content inflates this into the hundreds (P3-6), so the
                     # ceiling is what makes the guard non-vacuous.
                     and 0 < _px.get("cmyk_ops", 0) <= 100
                     and _px.get("colour_ops", 0) > 0
                     and not _x.get("unverified"))
            # the delivery manifest is the client-facing contract: it must
            # pass the schema shipped in the same package, not only look right
            _sch = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "schema_check.py"),
                 str(ROOT / "references" / "schemas" / "deliverable.schema.json"),
                 str(_lc_out / "deliverable.json"), "--json"],
                capture_output=True, text=True)
            _ok46 = _ok46 and _sch.returncode == 0
            add("A46-craft-only-landing", _ok46,
                "h1=" + str(_x.get("h1_pass")) + " paths=" + str(_x.get("paths"))
                + " GTS=" + str(_px.get("gts_pdfx")) + " OI=" + str(_px.get("output_intents"))
                + " cmyk=" + str(_px.get("cmyk_ops")) + " schema=" + str(_sch.returncode == 0)
                + " unverified=" + str(_x.get("unverified")))
    except Exception as e:                          # noqa: BLE001
        for _id in ("A42-delight-flattens-lighting",
                    "A43-export-print-dimensions",
                    "A44-trace-ref-contract",
                    "A45-uniform-error-envelope",
                    "A46-craft-only-landing"):
            if not any(x["id"] == _id for x in a):
                add(_id, False, type(e).__name__ + ": " + str(e)[:140])

    # optional: fixtures regression (now includes the six v1.3 samples)
    if args.fixtures:
        frc, fenv, _ = _call("run_fixtures.py", ["--json", "--log-dir", str(log_dir)], tmp)
        add("A13-fixtures-regression", frc == 0 and fenv.get("data", {}).get("all_ok") is True,
            "exit=" + str(frc))

    passed = sum(1 for x in a if x["ok"] is True)
    skipped = [x["id"] for x in a if x.get("skipped")]
    # A skip is not a pass and not a failure: it is a capability that was not
    # present. It is reported separately and marks the run "partial", so it
    # can never be mistaken for coverage.
    failed = [x["id"] for x in a if x["ok"] is False]
    doc = {
        "schema": "vector-ai-studio/selftest-v1",
        # Three states, not two. "partial" means at least one guard could not
        # run; a caller that only checks exit code or status must not read that
        # as success.
        "status": "fail" if failed else ("partial" if skipped else "ok"),
        "run_id": rid,
        "passed": passed, "failed": len(failed), "skipped": skipped,
        "partial": bool(skipped),
        "assertions": a,
        "chain": chain,
        "optional_deps": optional,
        "generation_budget": budget,
        "cwd_independent": True,
        "note": "pure-script layer only; real-machine checks in smoke_ai.py",
    }
    L.write_json_atomic(log_dir / "selftest.json", doc)
    L.append_run_log(log_dir, SCRIPT + " passed=" + str(passed) + "/" + str(len(a)))
    _code = L.EXIT_FAIL if failed else (L.EXIT_MISSING if skipped else L.EXIT_OK)
    return L.emit(SCRIPT, status=doc["status"],
                  code=_code,
                  run_id=rid, data=doc,
                  errors=([L.err("E-SELFTEST", "failed assertions: " + ",".join(failed))]
                          if failed else
                          ([L.err("E-SELFTEST", "not evaluated (missing capability): " +
                                  ",".join(skipped))] if skipped else [])),
                  artifacts={"selftest": "selftest.json"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
