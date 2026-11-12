#!/usr/bin/env python3
"""The shipped attack set: try to make this skill claim a pass it did not earn.

Why this exists (r72, plan 4.2): the honest parts of this project were only
checkable by reading 3,800 lines of `selftest.py`. A user could not verify
them. So the attacks are DATA (`tests/attacks/*.json`), the runner is generic,
and anybody can run:

    python scripts/redteam.py --json

Exit 0 means: every attack marked `block` was blocked. An attack that gets
through is a hole, and the script says so by name.

Two kinds of attack:

  expect="block"  the skill must refuse this. If it does not, the script
                  exits 1. These are the guards.
  expect="leak"   a DECLARED boundary -- an attack the skill cannot catch
                  today (a self-written log inside the very same run, a
                  review sheet submitted under a second name). The script
                  reports it, and `scripts/validate_claims.py` requires a
                  claim file to declare it. A leak that gets closed is
                  reported too ("boundary closed -- update the claim").

Every attack file carries its own `why`: what it attacks and what it proves.
An attack without a reason is decoration.
"""

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "redteam.py"
ATTACK_SCHEMA = "vector-ai-studio/attack-v1"
SCRIPTS = Path(__file__).resolve().parent
ROOT = L.skill_root()
ATTACK_DIR = ROOT / "tests" / "attacks"

# r70 #2: H1 re-opens final.ai's OWN bytes, so the base run needs a genuinely
# parseable minimal PDF container. Synthetic on purpose -- it proves the
# guards, not a real engine landing.
_MIN_PDF_AI = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>\nendobj\n"
    b"%%EOF\n")


def base_run(dest, run_id):
    """Build a COMPLETE, honest run that the judge accepts as a pass.

    Every attack mutates a copy of this, so each one breaks exactly the thing
    it is about. If the base run does not pass, the whole suite is measuring
    nothing -- so the base is checked first and reported as its own case.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    good = L.fixtures_dir() / "good"
    shutil.copyfile(good / "rect-only.svg", dest / "final.svg")
    shutil.copyfile(good / "judge.json", dest / "judge.json")
    # A real PNG plus the meta the renderer itself wrote (faithfulness for a
    # rect-only drawing). Never hand-write it: that would fabricate evidence.
    subprocess.run([sys.executable, str(SCRIPTS / "render_png.py"),
                    "--in", str(dest / "final.svg"),
                    "--out", str(dest / "preview.png"), "--force-builtin"],
                   capture_output=True, check=False, timeout=90)
    (dest / "final.ai").write_bytes(_MIN_PDF_AI)
    L.write_json_atomic(dest / "structure_readback.json", {
        "ok": True, "raster": 0, "placed": 0, "paths": 5,
        "artboard_matches_viewBox": True})
    n = L.EXPECTED_ASSERTIONS
    L.write_json_atomic(dest / "selftest.json", {
        "schema": "vector-ai-studio/selftest-v1",
        "status": "ok", "passed": n, "failed": 0, "skipped": [],
        "run_id": run_id,
        "capability_skips": sorted(L.CAPABILITY_SKIPS),
        "assertions": [{"id": "A-base-%03d" % i, "ok": True, "detail": "",
                        "skipped": False} for i in range(n)]})
    L.write_json_atomic(dest / "score.json", {
        "round": 1, "l2_degraded": False, "degraded_dims": [], "failed_dims": [],
        "scores": {"A": {"score": 2, "measured": {}, "threshold": {}},
                   "B": {"score": 2, "measured": {}, "threshold": {}},
                   "C": {"score": 1, "measured": {}, "threshold": {}},
                   "D": {"score": 1, "measured": {}, "threshold": {}},
                   "E": "N/A", "F": "N/A"}})
    L.write_json_atomic(dest / "batch.json", {
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
    return dest


def _apply(dest, mutations):
    """Apply the attack's mutations to the run directory."""
    dest = Path(dest)
    for m in mutations or []:
        op = m.get("op")
        target = dest / m["path"] if m.get("path") else None
        if op == "delete":
            if target and target.exists():
                target.unlink()
        elif op == "write_text":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(m.get("text", "")), encoding="utf-8")
        elif op == "write_bytes_b64":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(m["b64"]))
        elif op == "write_json":
            target.parent.mkdir(parents=True, exist_ok=True)
            L.write_json_atomic(target, m["doc"])
        elif op == "patch_json":
            # `path` is the FILE; `json_path` (dotted) is where inside it.
            # Keeping them apart matters: a file called selftest.json would
            # otherwise be read as the JSON path "selftest" -> "json".
            doc = json.loads(target.read_text(encoding="utf-8"))
            jpath = str(m.get("json_path", "")).split(".") if m.get("json_path") else []
            if not jpath:
                doc[m["key"]] = m["value"]
            else:
                cur = doc
                for seg in jpath[:-1]:
                    cur = cur.setdefault(seg, {})
                cur[jpath[-1]] = m["value"]
            L.write_json_atomic(target, doc)
        elif op == "copy":
            shutil.copyfile(dest / m["from"], dest / m["to"])
        else:
            raise L.ToolError("E-USAGE", "unknown attack op: " + str(op), L.EXIT_USAGE)


def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _dotted(doc, dotted):
    cur = doc
    for seg in str(dotted).split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _run_attack(spec, work):
    """Materialise the attack, run it, and report what happened."""
    cid = str(spec.get("id", "unnamed"))
    # The run id is fixed, not derived from the attack: H3 binds the self-test
    # log to it, and an attack that failed because of a mismatched id would be
    # measuring the wrong thing.
    run_id = "attack"
    run_dir = Path(work) / cid
    base_run(run_dir, run_id)
    _apply(run_dir, spec.get("mutations"))

    run = spec.get("run") or {"script": "aggregate_log.py"}
    args = [str(a).replace("@RUN@", str(run_dir)) for a in run.get("args", [])]
    cmd = [sys.executable, str(SCRIPTS / run["script"])] + args
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, check=False)
    try:
        env = json.loads(p.stdout or "")
    except json.JSONDecodeError:
        env = {}
    codes = sorted({str(e.get("code", "")) for e in (env.get("errors") or [])
                    if isinstance(e, dict)})
    acceptance = _read_json(run_dir / "acceptance.json")
    return {"id": cid, "exit": p.returncode, "codes": codes,
            "acceptance": acceptance, "env": env, "run_dir": str(run_dir)}


def _evaluate(spec, result):
    """Does the observed behaviour match what the attack declares?"""
    problems = []
    codes = result["codes"]
    acceptance = result["acceptance"]
    want_exit = spec.get("expect_exit")
    if want_exit is not None and result["exit"] not in want_exit:
        problems.append("exit code is " + str(result["exit"]) +
                        ", expected one of " + ",".join(str(x) for x in want_exit))
    want = spec.get("expect_codes")
    if want:
        for c in want:
            if c not in codes:
                problems.append("expected code " + c + " not emitted (got " +
                                (",".join(codes) or "none") + ")")
    reject = spec.get("reject_codes") or []
    for c in reject:
        if c in codes:
            problems.append("unexpected code " + c)
    if spec.get("expect_no_pass"):
        if acceptance.get("grade") == "达标" or acceptance.get("tier") == "pass":
            problems.append("the attack still claims a pass (grade=" +
                            str(acceptance.get("grade")) + ")")
    eg = spec.get("expect_grade")
    if eg is not None and acceptance.get("grade") != eg:
        problems.append("grade is " + repr(acceptance.get("grade")) +
                        ", expected " + repr(eg))
    for dotted, want_value in (spec.get("expect_field") or {}).items():
        got = _dotted(acceptance, dotted)
        if got != want_value:
            problems.append(dotted + " is " + repr(got) + ", expected " +
                            repr(want_value))
    return problems


def main():
    ap = argparse.ArgumentParser(description="run the shipped attack set")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--attack-dir", default="")
    ap.add_argument("--only", default="", help="run one attack by id")
    args = ap.parse_args()

    attack_dir = Path(args.attack_dir) if args.attack_dir else ATTACK_DIR
    if not attack_dir.is_dir():
        raise L.ToolError("E-SCHEMA", "attack set not found: " + str(attack_dir),
                          L.EXIT_USAGE)
    specs = []
    for p in sorted(attack_dir.glob("*.json")):
        doc = _read_json(p)
        if doc.get("schema") != ATTACK_SCHEMA:
            raise L.ToolError("E-SCHEMA",
                              "attack file schema mismatch: " + p.name, L.EXIT_USAGE)
        doc["_file"] = p.name
        specs.append(doc)
    if args.only:
        specs = [s for s in specs if s.get("id") == args.only]
    if not specs:
        raise L.ToolError("E-SCHEMA", "no attacks to run", L.EXIT_USAGE)

    work = Path(tempfile.mkdtemp(prefix="vector-ai-redteam-"))
    results = []
    blocked = leaked = known = closed = 0
    failures = []
    try:
        for spec in specs:
            res = _run_attack(spec, work)
            problems = _evaluate(spec, res)
            expect = spec.get("expect", "block")
            if expect == "leak":
                # A declared boundary: the attack is expected to get through.
                # If it is blocked after all, that is good news -- it is
                # reported so the claim file can be updated.
                got_through = not problems
                if got_through:
                    known += 1
                    status = "known-boundary"
                else:
                    closed += 1
                    status = "boundary-closed"
                results.append({"id": spec.get("id"), "name": spec.get("name"),
                                "expect": expect, "status": status,
                                "why": spec.get("why"), "codes": res["codes"],
                                "detail": "; ".join(problems)})
                continue
            if problems:
                leaked += 1
                failures.append({"id": spec.get("id"), "problems": problems})
                results.append({"id": spec.get("id"), "name": spec.get("name"),
                                "expect": expect, "status": "leaked",
                                "why": spec.get("why"), "codes": res["codes"],
                                "detail": "; ".join(problems)})
            else:
                blocked += 1
                results.append({"id": spec.get("id"), "name": spec.get("name"),
                                "expect": expect, "status": "blocked",
                                "why": spec.get("why"), "codes": res["codes"],
                                "detail": ""})
    finally:
        shutil.rmtree(work, ignore_errors=True)

    doc = {
        "schema": "vector-ai-studio/redteam-result-v1",
        "attacks": len(results),
        "blocked": blocked,
        "leaked": leaked,
        "known_boundaries": known,
        "boundaries_closed": closed,
        "all_blocked": leaked == 0,
        "results": results,
        "note": ("expect=block 的攻击必须全部被拦下（leaked=0）；"
                 "expect=leak 的是已声明的边界，必须在 claims 文件里登记"),
    }
    errors = []
    if failures:
        errors.append(L.err("E-ATTACK-PASSED",
                            "attacks that were not blocked: " +
                            ", ".join(f["id"] for f in failures)))
    return L.emit(SCRIPT, status="fail" if failures else "ok",
                  code=L.EXIT_FAIL if failures else L.EXIT_OK,
                  data=doc, errors=errors)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
