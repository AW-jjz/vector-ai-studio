#!/usr/bin/env python3
"""Layered L2: real-machine Illustrator smoke test (standalone command).

Fully decoupled from selftest.py. It performs a REAL COM probe via
detect_deps.py --probe-com. If COM is not drivable it degrades explicitly to
L4 (exit 3, status=degraded) and writes a real log. It NEVER writes ok and
NEVER fabricates a .ai artifact.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "smoke_ai.py"
SCRIPTS = Path(__file__).resolve().parent


def _run(cmd, timeout=240):
    try:
        r = subprocess.run(cmd, timeout=timeout, capture_output=True,
                           text=True, check=False)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", type(e).__name__ + ": " + str(e)[:160]


def main():
    ap = argparse.ArgumentParser(description="real-machine Illustrator smoke test")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log-dir", default="")
    ap.add_argument("--illustrator-exe", default="")
    ap.add_argument("--landing-only", action="store_true",
                    help="only take H1 evidence (mode=landing-only); default runs the full chain")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    th = L.load_thresholds()
    sample = L.fixtures_dir() / "good" / "sample.svg"
    if not sample.is_file():
        raise L.ToolError("E-SMOKE-NO-SAMPLE", "built-in good sample.svg missing", L.EXIT_USAGE)
    rid = args.run_id or L.sha_run_id("smoke", sample.read_text(encoding="utf-8"),
                                      th["version"])
    log_dir = Path(args.log_dir) if args.log_dir else (L.default_output_root() / "smoke" / rid)
    log_dir.mkdir(parents=True, exist_ok=True)

    deps_path = log_dir / "deps.json"
    cmd = [sys.executable, str(SCRIPTS / "detect_deps.py"), "--json",
           "--probe-com", "--out", str(deps_path)]
    if args.illustrator_exe:
        cmd += ["--illustrator-exe", args.illustrator_exe]
    rc, out, errout = _run(cmd)
    L.write_text_atomic(log_dir / "probe-raw.txt",
                        "exit=" + str(rc) + "\nstdout:\n" + out + "\nstderr:\n" + errout)

    com_status = "UNVERIFIED"
    missing = []
    summary = {}
    if deps_path.is_file():
        try:
            d = json.loads(deps_path.read_text(encoding="utf-8"))
            summary = d.get("summary", {})
            missing = d.get("missing", [])
            for item in d.get("dependencies", []):
                if item.get("name") == "illustrator_com":
                    com_status = str(item.get("status", "UNVERIFIED"))
        except (OSError, json.JSONDecodeError):
            pass

    smoke = {
        "schema": "vector-ai-studio/smoke-v1",
        "run_id": rid,
        "status": "degraded",
        "degradation": {"level": "L4", "reason": "", "missing": [], "substitute": []},
        "com_probe": {"attempted": True, "probe_exit_code": rc,
                      "illustrator_com": com_status,
                      "deps_summary": summary, "deps_missing": missing},
        "ai_produced": False,
        "h1_pass": False,
        "note": "",
        "log_files": ["smoke.json", "probe-raw.txt", "deps.json"],
    }

    if com_status != "FOUND":
        smoke["degradation"] = {
            "level": "L4",
            "reason": "Illustrator COM is not drivable on this machine (real probe result: " +
                      com_status + ")",
            "missing": ["illustrator_com"] + [m for m in missing if m != "illustrator_com"],
            "substitute": ["deliver sanitized SVG + PNG only, H1 fails",
                           "or user-selected semi-manual JSX path (one manual script execution)",
                           "install/enable Illustrator COM automation"],
        }
        smoke["note"] = th["messages"]["semi_manual_note"]
        L.write_json_atomic(log_dir / "smoke.json", smoke)
        L.append_run_log(log_dir, SCRIPT + " degraded L4 com=" + com_status)
        return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING, run_id=rid,
                      data=smoke,
                      degradation=smoke["degradation"],
                      artifacts={"smoke": "smoke.json", "probe_raw": "probe-raw.txt",
                                 "deps": "deps.json" if deps_path.is_file() else ""})

    # COM is drivable: run the real landing chain with the built-in sample.
    run_dir = log_dir / ("run-" + rid)
    rc2, out2, err2 = _run([sys.executable, str(SCRIPTS / "land_ai.py"),
                            "--svg", str(sample), "--out", str(run_dir),
                            "--deps", str(deps_path), "--run-id", rid])
    L.write_text_atomic(log_dir / "land-raw.txt",
                        "exit=" + str(rc2) + "\nstdout:\n" + out2 + "\nstderr:\n" + err2)
    sr = {}
    p = run_dir / "structure_readback.json"
    if p.is_file():
        try:
            sr = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sr = {}
    ai_ok = (run_dir / "final.ai").is_file() and (run_dir / "final.ai").stat().st_size > 0
    h1 = bool(sr.get("ok")) and ai_ok

    if not h1:
        smoke["degradation"] = {"level": "L4",
                               "reason": "landing ran but H1 readback assertion failed",
                               "missing": [], "substitute": ["inspect structure_readback.json"]}
        smoke["ai_produced"] = ai_ok
        smoke["note"] = "H1 未通过，未宣称达标"
        L.write_json_atomic(log_dir / "smoke.json", smoke)
        L.append_run_log(log_dir, SCRIPT + " H1 failed")
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL, run_id=rid,
                      data=smoke,
                      errors=[L.err("E-H1-STRUCTURE", "real .ai or readback assertion failed")])

    # ---- evidence track: H1 from landing; H2/H3/H4 from the full graded chain ----
    if args.landing_only:
        agg_cmd = [sys.executable, str(SCRIPTS / "aggregate_log.py"),
                   "--run-dir", str(run_dir), "--landing-only", "--run-id", rid]
    else:
        good = L.fixtures_dir() / "good"
        _run([sys.executable, str(SCRIPTS / "freeze_spec.py"),
              "--spec", str(good / "spec.json"), "--validate",
              "--out", str(run_dir / "spec.json")])
        _run([sys.executable, str(SCRIPTS / "llm_judge.py"),
              "--judge", str(good / "judge.json"), "--validate",
              "--spec", str(run_dir / "spec.json"), "--out", str(run_dir / "judge.json")])
        _run([sys.executable, str(SCRIPTS / "score.py"),
              "--svg", str(run_dir / "final.svg"), "--judge", str(run_dir / "judge.json"),
              "--spec", str(run_dir / "spec.json"), "--out", str(run_dir / "score.json")])
        _run([sys.executable, str(SCRIPTS / "selftest.py"), "--fixtures",
              "--log-dir", str(run_dir)])
        agg_cmd = [sys.executable, str(SCRIPTS / "aggregate_log.py"),
                   "--run-dir", str(run_dir), "--run-id", rid]
    agg_exit, agg_out, agg_err = _run(agg_cmd)
    L.write_text_atomic(log_dir / "aggregate-raw.txt",
                        "exit=" + str(agg_exit) + "\nstdout:\n" + agg_out +
                        "\nstderr:\n" + agg_err)
    acc = {}
    acc_path = run_dir / "acceptance.json"
    if acc_path.is_file():
        try:
            acc = json.loads(acc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            acc = {}
    smoke["acceptance"] = {
        "exit": agg_exit, "mode": acc.get("mode"),
        "h1": acc.get("h1"), "h2": acc.get("h2"),
        "h2_status": acc.get("h2_status"), "grade": acc.get("grade"),
        "scores_keys": sorted((acc.get("scores") or {}).keys()),
    }

    smoke["status"] = "ok"
    smoke["degradation"] = None
    smoke["ai_produced"] = True
    smoke["h1_pass"] = True
    smoke["structure_readback"] = sr
    smoke["note"] = "真实产出 .ai 并通过 H1 重开回读"
    L.write_json_atomic(log_dir / "smoke.json", smoke)
    L.append_run_log(log_dir, SCRIPT + " ok h1_pass=true")
    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK, run_id=rid,
                  data=smoke,
                  artifacts={"smoke": "smoke.json", "final_ai": "final.ai",
                             "structure_readback": "structure_readback.json"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
