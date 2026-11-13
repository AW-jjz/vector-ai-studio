#!/usr/bin/env python3
"""replay.py — 消费侧证据重放（M2 / r73）：别信日志，重算一遍。

Why this exists (M2, plan: 证据可重放 + 角色分离): everything the delivery
chain asserts about a run is only as good as the files in
`output/<run-id>/`. Until now the consumer had two bad options: trust the
shipped acceptance.json, or re-implement the criteria (and then trust their
re-implementation). replay.py is the third option: it re-executes the PUBLIC
aggregator -- the same script, the same thresholds, the same flags the
producer used -- on a COPY of the run directory, and byte-compares what comes
out with what shipped. No second implementation of the criteria can drift,
because there is only one implementation and the consumer runs it themselves.

What it checks, in order:

  1. CHAIN    journal.jsonl's internal integrity (prev links, seq, hashes) --
              catches deleted / reordered / edited entries.
  2. EVIDENCE every journaled artifact still hashes to what the journal
              recorded -- catches post-production tampering of evidence files,
              including the shipped acceptance.json itself.
  3. REPLAY   re-aggregation on a COPY of the run dir (the original is never
              written to), then byte-compare both acceptance.json AND
              journal.jsonl -- catches any edit that flows into the verdict,
              and any evidence edit that survived step 2 even when the
              verdict did not change.

Flags are reconstructed from the shipped log itself (legacy_grading ->
--two-valued, grading_entered=false -> --landing-only, quick -> --quick, and
--run-id from the log's run_id field), so the replay reproduces the original
aggregation exactly.

Declared boundary (the honest one): the run directory is writable by the
producer. A producer who rewrites the evidence, regenerates a consistent
journal AND a consistent acceptance.json defeats replay -- the same
within-run self-attestation boundary H3 documents. What replay removes is
every CHEAP forgery: editing one file, swapping a preview, bumping a score.

    python scripts/replay.py --run-dir output/<run-id> --json

Exit codes: 0 = replayed and identical · 1 = replay/chain/artifact mismatch
· 2 = usage · 3 = n/a (not used here) · 4 = internal error.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import journal
import witness  # r74 (M2 slice 2): the keyless certification anchor

SCRIPT = "replay.py"


def _diff_json(old, new, prefix="", out=None, limit=40):
    """Recursive diff of two JSON docs -> list of dotted paths that differ."""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            if len(out) >= limit:
                break
            if k not in old:
                out.append(prefix + k + ": ADDED (" + json.dumps(new[k], ensure_ascii=False)[:80] + ")")
            elif k not in new:
                out.append(prefix + k + ": REMOVED (was " + json.dumps(old[k], ensure_ascii=False)[:80] + ")")
            else:
                _diff_json(old[k], new[k], prefix + k + ".", out, limit)
    elif old != new:
        out.append(prefix[:-1] + ": " + json.dumps(old, ensure_ascii=False)[:60]
                   + " -> " + json.dumps(new, ensure_ascii=False)[:60])
    return out


def main():
    ap = argparse.ArgumentParser(description="consumer-side evidence replay")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--acceptance", default="",
                    help="shipped acceptance.json (default: <run-dir>/acceptance.json)")
    ap.add_argument("--witness", default="",
                    help="the published witness trail (r74): the run's acceptance/journal "
                         "hashes are compared against the last record for this run_id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        raise L.ToolError("E-USAGE", "run-dir not found: " + run_dir.name, L.EXIT_USAGE)
    shipped_path = Path(args.acceptance) if args.acceptance else (run_dir / "acceptance.json")
    if not shipped_path.is_file():
        raise L.ToolError("E-USAGE", "shipped acceptance.json not found: " + shipped_path.name,
                          L.EXIT_USAGE)
    shipped_bytes = shipped_path.read_bytes()
    shipped = json.loads(shipped_bytes.decode("utf-8"))

    problems = []
    checks = []

    # ---- 1. the chain itself ------------------------------------------------
    journal_path = run_dir / "journal.jsonl"
    if not journal_path.is_file():
        problems.append(L.err("E-JOURNAL-BROKEN",
                              "no journal.jsonl next to the acceptance log -- this run "
                              "predates the evidence journal (r73); re-run the aggregation "
                              "to produce one"))
        checks.append({"id": "R1", "name": "journal 哈希链存在且自洽", "ok": False,
                       "detail": "journal.jsonl 缺失"})
        entries = []
    else:
        entries = journal.read_journal(journal_path)
        chain_problems = journal.verify_chain(entries)
        checks.append({"id": "R1", "name": "journal 哈希链存在且自洽",
                       "ok": not chain_problems,
                       "detail": "%d 条目，链尾 %s…" % (len(entries),
                                                       entries[-1]["hash"][:12] if entries else journal.GENESIS)})
        problems += chain_problems

    # ---- 2. the evidence, as it stands NOW ----------------------------------
    if entries:
        artifact_problems = journal.verify_artifacts(entries, run_dir)
        n_drift = sum(1 for p in artifact_problems if p["code"] == "E-ARTIFACT-DRIFT")
        checks.append({"id": "R2", "name": "证据文件与 journal 逐一比对",
                       "ok": not artifact_problems,
                       "detail": "%d 个工件复算，%d 个漂移" % (len(entries), n_drift)})
        problems += artifact_problems

    # ---- 3. replay: same aggregator, copied dir, byte compare ---------------
    tmp = Path(tempfile.mkdtemp(prefix="replay-"))
    try:
        copy = tmp / "run"
        shutil.copytree(run_dir, copy)
        cmd = [sys.executable, str(Path(__file__).resolve().parent / "aggregate_log.py"),
               "--run-dir", str(copy),
               "--out", str(copy / "acceptance.json"),
               "--run-id", str(shipped.get("run_id") or "")]
        if shipped.get("legacy_grading") is True:
            cmd.append("--two-valued")
        if shipped.get("grading_entered") is False:
            cmd.append("--landing-only")
        if shipped.get("quick") is True:
            cmd.append("--quick")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=600,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                               check=False)
            rc = r.returncode
        except (OSError, subprocess.SubprocessError) as e:
            rc = -1
            problems.append(L.err("E-REPLAY-MISMATCH", "re-aggregation failed to run: " + str(e)[:120]))

        fresh_path = copy / "acceptance.json"
        if fresh_path.is_file():
            fresh_bytes = fresh_path.read_bytes()
            same_acc = fresh_bytes == shipped_bytes
            checks.append({"id": "R3", "name": "验收日志逐字节重算比对",
                           "ok": same_acc,
                           "detail": ("逐字节一致（exit %d）" % rc) if same_acc else
                                     "重算结果与随包日志不一致（exit %d）" % rc})
            if not same_acc:
                try:
                    diff = _diff_json(json.loads(shipped_bytes.decode("utf-8")),
                                      json.loads(fresh_bytes.decode("utf-8")))
                except json.JSONDecodeError:
                    diff = ["(log is not valid JSON)"]
                for line in diff[:20]:
                    problems.append(L.err("E-REPLAY-MISMATCH", line))
        else:
            checks.append({"id": "R3", "name": "验收日志逐字节重算比对",
                           "ok": False, "detail": "重算没有产出 acceptance.json（exit %d）" % rc})

        fresh_journal = copy / "journal.jsonl"
        if journal_path.is_file() and fresh_journal.is_file():
            same_j = fresh_journal.read_bytes() == journal_path.read_bytes()
            checks.append({"id": "R4", "name": "journal 逐字节重算比对",
                           "ok": same_j,
                           "detail": "逐字节一致" if same_j else
                                     "重算的 journal 与随包 journal 不一致——有文件的字节变了而 journal 没有重新生成"})
            if not same_j:
                problems.append(L.err("E-ARTIFACT-DRIFT",
                                      "re-aggregated journal differs from the shipped one: some "
                                      "evidence file changed without the journal being regenerated"))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 5. the witness anchor (r74): outside the run dir, so the producer ---
    # ---- cannot rewrite it by rebuilding the directory ----------------------
    if args.witness:
        try:
            wenv = witness.verify(argparse.Namespace(
                run_dir=str(run_dir), witness=args.witness, json=True))
            wdata = wenv.get("data") or {}
            wok = wenv.get("code") == L.EXIT_OK and wdata.get("witnessed") is True
            checks.append({"id": "R5", "name": "见证锚核验（签收后未被改）",
                           "ok": wok,
                           "detail": ("已见证（%s，签收时 acceptance=%s…）"
                                      % (wdata.get("witness_time"),
                                         str(wdata.get("acceptance_sha256"))[:12])
                                      if wok else
                                      "; ".join(e["msg"][:90] for e in (wenv.get("errors") or [])[:2]) or
                                      "无该 run_id 的见证记录")})
            if not wok:
                problems += list(wenv.get("errors") or [])
        except L.ToolError as e:
            checks.append({"id": "R5", "name": "见证锚核验（签收后未被改）",
                           "ok": False, "detail": e.msg[:90]})
            problems.append(L.err(e.code, e.msg))

    ok = not problems
    doc = {"run_dir": run_dir.name,
           "verdict": "REPLAYED AND IDENTICAL" if ok else "REPLAY FAILED",
           "run_id": shipped.get("run_id") or "",
           "criteria_version": shipped.get("criteria_version"),
           "checks": checks,
           "counts": {"checks": len(checks), "ok": sum(1 for c in checks if c["ok"]),
                      "failed": sum(1 for c in checks if not c["ok"]),
                      "problems": len(problems)},
           "problems": [p["msg"] for p in problems[:40]]}
    return L.emit(SCRIPT, status="ok" if ok else "fail",
                  code=L.EXIT_OK if ok else L.EXIT_FAIL,
                  data=doc, errors=problems or None)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
