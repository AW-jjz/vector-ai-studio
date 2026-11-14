#!/usr/bin/env python3
"""fixtures regression: good all pass, bad hit expected E-* codes.

Materializes fixtures.json into a temp dir and runs each case as a real
subprocess. Never skips a case silently; prints want/got differences.

Phase 2-3 (r93) splits the mechanics from the vocabulary: placeholder
expansion, ``setup_dir`` materialisation, running one case as a subprocess,
the exact code match and the declared-count guard all live in the vendored
``verdict_kit.fixtures``. What stays here is the part only this skill knows --
the schema name, where fixtures.json and the scripts live, the CLI flags, the
envelope and the code a mismatch is reported as.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

# Phase 2-3: the vendored fixture runner owns expand / materialize / run /
# match / summarize. Importing the package would create
# scripts/_vendor/.../__pycache__ (A22), so bytecode writing is disabled for
# this CLI process.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor" / "verdict-kit"))
import verdict_kit.fixtures as VF  # noqa: E402

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
    try:
        VF.check_declared_count(fx["cases"], fx.get("cases_expected"))
    except VF.FixtureError as e:
        raise L.ToolError("E-SCHEMA", str(e), L.EXIT_USAGE)

    fdir = fx_path.parent
    tmp = Path(tempfile.mkdtemp(prefix="vector-ai-fixtures-"))
    results = []
    for case in fx["cases"]:
        # Each case gets its own @RUN@ directory; a case that declares a
        # setup_dir gets a COMPLETE run directory (final.ai / preview.png /
        # JSON logs / ...) before its arguments are expanded.
        try:
            results.append(VF.run_case(case, scripts_dir=SCRIPTS,
                                       fixtures_dir=fdir, out_dir=tmp))
        except VF.FixtureError as e:
            raise L.ToolError("E-SCHEMA", str(e), L.EXIT_USAGE)

    doc = {
        "schema": "vector-ai-studio/fixtures-result-v1",
        "run_id": args.run_id,
    }
    doc.update(VF.summarize(results))
    failed = [r for r in results if not r["ok"]]
    artifacts = {}
    if args.log_dir:
        log_dir = Path(args.log_dir)
        L.write_json_atomic(log_dir / "fixtures-result.json", doc)
        artifacts["fixtures_result"] = "fixtures-result.json"
        L.append_run_log(log_dir, SCRIPT + " passed=" + str(doc["passed"]) +
                         "/" + str(doc["total"]))
    return L.emit(SCRIPT, status="ok" if not failed else "fail",
                  code=L.EXIT_OK if not failed else L.EXIT_FAIL,
                  run_id=args.run_id, data=doc,
                  errors=([L.err("E-FIXTURE-MISMATCH",
                                 "cases not matching expectation: " +
                                 ",".join(r["id"] for r in failed))] if failed else []),
                  artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
