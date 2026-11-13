#!/usr/bin/env python3
"""witness.py — 见证层（M2 第二片 / r74）：把「签收」变成一个可核验的事实。

Why this exists (M2, plan: 角色分离): replay.py closes every CHEAP forgery,
and its own docstring declares the hole it cannot close: the run directory is
writable by the producer, so a producer who rewrites the evidence, the journal
AND the acceptance log together still defeats replay -- everything replay
checks lives inside the run dir, and the producer owns that directory.

The only way out of a within-run hole is an anchor OUTSIDE it. The project
already has one it cannot retroactively rewrite: the REPOSITORY -- an
append-only, pushed, tagged history (H4's zero-credential principle rules out
key signing; the repo IS the identity this project has). So the signature
layer is keyless by design:

  * `witness create` appends a record to a witness trail (JSONL, append-only,
    lives OUTSIDE the run directory, gets COMMITTED to the repo and pushed):
        {seq, time, run_id, acceptance_sha256, journal_sha256,
         journal_tail, criteria_version, note}
  * `witness verify` re-hashes the run's acceptance.json / journal.jsonl and
    compares against the LAST record for that run_id:
        match    -> "witnessed": these bytes are the ones that were certified
        mismatch -> E-WITNESS-MISMATCH: the run changed after certification
  * replay.py wires it in as check R5 (--witness <file>), so a consumer runs
    one command against a published anchor.

The declared boundary, stated honestly: the anchor is the repo history, so
whoever can force-push that history can also forge the anchor -- the tag
lowers that to "visible in the tag", and the trail files are append-only by
discipline. This is exactly the trust a published git repository carries;
nothing more is claimed.

Wall-clock is ALLOWED here (unlike journal.jsonl): the witness file is an
append-only TRAIL like sha256.txt -- a record of certifications, not a
reproducible artifact; determinism claims do not apply to it.

    python scripts/witness.py create --run-dir <dir> --witness <file> [--note ...]
    python scripts/witness.py verify --run-dir <dir> --witness <file> [--json]
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import journal

SCRIPT = "witness.py"


def _run_facts(run_dir):
    run_dir = Path(run_dir)
    acc = run_dir / "acceptance.json"
    jl = run_dir / "journal.jsonl"
    if not acc.is_file():
        raise L.ToolError("E-USAGE", "acceptance.json not found in " + run_dir.name,
                          L.EXIT_USAGE)
    if not jl.is_file():
        raise L.ToolError("E-JOURNAL-BROKEN",
                          "journal.jsonl not found -- run an aggregation first "
                          "(scripts/aggregate_log.py writes it)", L.EXIT_USAGE)
    try:
        acc_doc = json.loads(acc.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise L.ToolError("E-SCHEMA", "acceptance.json is not valid JSON: " + str(e),
                          L.EXIT_FAIL)
    entries = journal.read_journal(jl)
    chain_problems = journal.verify_chain(entries)
    if chain_problems:
        raise L.ToolError("E-JOURNAL-BROKEN",
                          "the journal does not verify; refusing to witness a broken "
                          "chain: " + chain_problems[0]["msg"], L.EXIT_FAIL)
    return {
        "run_id": str(acc_doc.get("run_id") or ""),
        "criteria_version": acc_doc.get("criteria_version"),
        "acceptance_sha256": L.file_sha256(acc),
        "journal_sha256": L.file_sha256(jl),
        "journal_tail": entries[-1]["hash"] if entries else journal.GENESIS,
        "journal_entries": len(entries),
    }


def _records(witness_path):
    p = Path(witness_path)
    if not p.is_file():
        return []
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise L.ToolError("E-SCHEMA",
                              "witness line %d is not JSON: %s" % (i + 1, e), L.EXIT_FAIL)
    return out


def create(args):
    facts = _run_facts(args.run_dir)
    recs = _records(args.witness)
    rec = {"seq": len(recs), "time": time.strftime("%Y-%m-%d %H:%M:%S"), **facts}
    if args.note:
        rec["note"] = args.note
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")) for r in recs]
    lines.append(json.dumps(rec, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")))
    L.write_text_atomic(args.witness, "\n".join(lines) + "\n")
    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK,
                  data={"witnessed": True, "record_seq": rec["seq"],
                        "run_id": rec["run_id"],
                        "acceptance_sha256": rec["acceptance_sha256"],
                        "journal_sha256": rec["journal_sha256"],
                        "witness_file": str(args.witness)})


def verify(args):
    facts = _run_facts(args.run_dir)
    recs = [r for r in _records(args.witness) if r.get("run_id") == facts["run_id"]]
    if not recs:
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL,
                      data={"witnessed": False, "run_id": facts["run_id"],
                            "reason": "no witness record for this run_id"},
                      errors=[L.err("E-WITNESS-MISMATCH",
                                    "no witness record for run_id " + repr(facts["run_id"]))])
    last = recs[-1]
    problems = []
    if last.get("acceptance_sha256") != facts["acceptance_sha256"]:
        problems.append(L.err("E-WITNESS-MISMATCH",
                              "acceptance.json changed after certification: witnessed %s..., now %s..."
                              % (str(last.get("acceptance_sha256"))[:12],
                                 facts["acceptance_sha256"][:12])))
    if last.get("journal_sha256") != facts["journal_sha256"]:
        problems.append(L.err("E-WITNESS-MISMATCH",
                              "journal.jsonl changed after certification: witnessed %s..., now %s..."
                              % (str(last.get("journal_sha256"))[:12],
                                 facts["journal_sha256"][:12])))
    doc = {"witnessed": not problems, "run_id": facts["run_id"],
           "witness_time": last.get("time"),
           "criteria_version": facts["criteria_version"],
           "acceptance_sha256": facts["acceptance_sha256"],
           "journal_sha256": facts["journal_sha256"],
           "witness_records_for_run": len(recs)}
    return L.emit(SCRIPT, status="ok" if not problems else "fail",
                  code=L.EXIT_OK if not problems else L.EXIT_FAIL,
                  data=doc, errors=problems or None)


def main():
    ap = argparse.ArgumentParser(description="keyless certification witness")
    ap.add_argument("action", choices=["create", "verify"])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--witness", required=True,
                    help="the append-only witness trail (outside the run dir; commit it)")
    ap.add_argument("--note", default="", help="why this run is being certified")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    return create(args) if args.action == "create" else verify(args)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
