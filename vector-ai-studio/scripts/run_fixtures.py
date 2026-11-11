#!/usr/bin/env python3
"""fixtures regression: good all pass, bad hit expected E-* codes.

Materializes fixtures.json into a temp dir and runs each case as a real
subprocess. Never skips a case silently; prints want/got differences.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "run_fixtures.py"
SCRIPTS = Path(__file__).resolve().parent
FIX_SCHEMA = "vector-ai-studio/fixtures-v1"


def main():
    ap = argparse.ArgumentParser(description="run fixtures regression")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fixtures", default="")
    ap.add_argument("--log-dir", default="")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    fx_path = Path(args.fixtures) if args.fixtures else (L.fixtures_dir() / "fixtures.json")
    if not fx_path.is_file():
        raise L.ToolError("E-SCHEMA", "fixtures definition not found: " + fx_path.name, L.EXIT_USAGE)
    try:
        fx = json.loads(fx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise L.ToolError("E-SCHEMA", "malformed fixtures.json: " + str(e), L.EXIT_USAGE)
    if fx.get("schema") != FIX_SCHEMA or not isinstance(fx.get("cases"), list):
        raise L.ToolError("E-SCHEMA", "fixtures.json schema/cases invalid", L.EXIT_USAGE)
    # A truncated definition used to report total=0, all_ok=true and exit 0 --
    # a green run over no cases. The expected count is declared in the file
    # (like EXPECTED_ASSERTIONS in selftest.py) so a shrink is visible.
    want_total = fx.get("cases_expected")
    if isinstance(want_total, bool) or not isinstance(want_total, int):
        raise L.ToolError("E-SCHEMA", "fixtures.json must declare cases_expected",
                          L.EXIT_USAGE)
    if len(fx["cases"]) != want_total:
        raise L.ToolError(
            "E-SCHEMA", "fixtures.json has %d cases, declared %d"
            % (len(fx["cases"]), want_total), L.EXIT_USAGE)

    fdir = fx_path.parent
    tmp = Path(tempfile.mkdtemp(prefix="vector-ai-fixtures-"))
    results = []
    for case in fx["cases"]:
        cid = str(case.get("id", "unnamed"))
        script = str(case.get("script", ""))
        raw_args = [str(a) for a in case.get("args", [])]
        expanded = []
        for a in raw_args:
            a = a.replace("@F@", str(fdir)).replace("@OUT@", str(tmp))
            expanded.append(a)
        cmd = [sys.executable, str(SCRIPTS / script)] + expanded
        try:
            r = subprocess.run(cmd, timeout=90, capture_output=True, text=True, check=False)
            rc, out = r.returncode, (r.stdout or "")
        except (OSError, subprocess.SubprocessError):
            rc, out = -1, ""
        got_codes = []
        try:
            env = json.loads(out)
            got_codes = [e.get("code", "") for e in env.get("errors", [])]
            got_codes += [w.split(":")[0] for w in env.get("warnings", []) if ":" in w]
        except json.JSONDecodeError:
            got_codes = []
        want_exit = [int(x) for x in case.get("want_exit", [0])]
        want_codes = [str(x) for x in case.get("want_codes", [])]
        ok = (rc in want_exit and all(c in got_codes for c in want_codes))
        results.append({"id": cid, "script": script, "ok": ok,
                        "want_exit": want_exit, "got_exit": rc,
                        "want_codes": want_codes, "got_codes": got_codes})

    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    doc = {
        "schema": "vector-ai-studio/fixtures-result-v1",
        "run_id": args.run_id,
        "total": len(results), "passed": passed, "failed": len(failed),
        "all_ok": not failed,
        "cases": results,
    }
    artifacts = {}
    if args.log_dir:
        log_dir = Path(args.log_dir)
        L.write_json_atomic(log_dir / "fixtures-result.json", doc)
        artifacts["fixtures_result"] = "fixtures-result.json"
        L.append_run_log(log_dir, SCRIPT + " passed=" + str(passed) + "/" + str(len(results)))
    return L.emit(SCRIPT, status="ok" if not failed else "fail",
                  code=L.EXIT_OK if not failed else L.EXIT_FAIL,
                  run_id=args.run_id, data=doc,
                  errors=([L.err("E-FIXTURE-MISMATCH",
                                 "cases not matching expectation: " +
                                 ",".join(r["id"] for r in failed))] if failed else []),
                  artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
