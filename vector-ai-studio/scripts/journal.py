#!/usr/bin/env python3
"""journal.py — 证据哈希链（M2 / r73）：产物在生产时被钉进一条链。

Why this exists (M2, plan: 证据可重放): before this, the acceptance chain had
one direction of protection. The validator re-derives the VERDICT from the
evidence fields inside acceptance.json -- but nothing bound the evidence
FILES themselves to what the acceptance step actually read. A score.json
edited after aggregation, a preview.png swapped for another run's, a
selftest.json replaced -- none of that moved any gate, because every gate
read the (now fake) files as if they were the ones the log was built from.

The journal is the production-time binding: when the acceptance step runs,
it hashes EVERY file in the run directory into a hash chain and writes
journal.jsonl next to acceptance.json. replay.py (the consumer side) then:

  1. verifies the chain itself (prev links, seq order, per-entry hashes) --
     catches deletion, reordering, and in-place entry edits;
  2. re-hashes every journaled file and compares -- catches post-production
     tampering of the evidence;
  3. re-runs the PUBLIC aggregator (scripts/aggregate_log.py, the same tool
     the producer used -- not a second implementation) and byte-compares the
     fresh acceptance.json with the shipped one -- catches verdict tampering
     and any evidence edit that survives step 2.

Design invariants (do not weaken them):

  * NO wall-clock in the journal. The chain must be byte-identical for the
    same file set, or A3 (byte-idempotency) and the replay byte-compare both
    collapse. Wall-clock already has its one legal home: run.log -- which is
    therefore EXCLUDED from the chain, together with the two outputs
    (acceptance.json, journal.jsonl).
  * final.ai IS journaled although it is not byte-idempotent across runs:
    its hash lives only in journal.jsonl (never inside acceptance.json), so
    A3 stays intact while the delivered binary stays bound to this run.
  * Declared boundary (the honest one): the run directory is writable by the
    producer, so a producer who rewrites the evidence AND regenerates a
    consistent journal AND a consistent acceptance.json defeats all three
    steps. That is the same within-run self-attestation boundary the H3
    documentation already declares; the journal raises the cost from
    "edit one file" to "replay the whole forgery", and replay gives the
    CONSUMER a re-derivation path they can run themselves.

CLI:
    python scripts/journal.py --verify <journal.jsonl> --root <run-dir> --json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "journal.py"
GENESIS = "0" * 64
# Only the journal itself and the wall-clock log are excluded: everything
# else in the run directory is EVIDENCE and gets bound, including the shipped
# acceptance.json and deliverable.json. (r73, after the first replay run
# showed the verdict file itself must be part of the chain.)
EXCLUDED = {"journal.jsonl", "run.log"}


def canonical(obj):
    """The bytes an entry is hashed over: sorted keys, compact, UTF-8."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def entry_hash(entry):
    """sha256 over the entry WITHOUT its own 'hash' field."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


def journal_dir_files(root, extra_excluded=()):
    """Every file under root, relative posix paths, sorted, minus exclusions."""
    root = Path(root)
    excl = set(EXCLUDED) | set(extra_excluded)
    out = []
    for dp, dn, fn in os_walk(root):
        dn[:] = sorted(d for d in dn if d != "__pycache__")
        for f in sorted(fn):
            if f.endswith(".pyc") or f in excl:
                continue
            p = Path(dp) / f
            out.append((p.relative_to(root).as_posix(), p))
    return sorted(out)


def os_walk(root):
    import os
    return os.walk(root)


def build_entries(root, extra_excluded=()):
    """The full chain for a directory: one entry per file, sorted by path."""
    entries = []
    prev = GENESIS
    for rel, p in journal_dir_files(root, extra_excluded):
        e = {"seq": len(entries), "prev": prev, "kind": "artifact",
             "path": rel, "sha256": L.file_sha256(p),
             "bytes": p.stat().st_size}
        e["hash"] = entry_hash(e)
        entries.append(e)
        prev = e["hash"]
    return entries


def write_journal(journal_path, entries):
    lines = [json.dumps(e, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")) for e in entries]
    L.write_text_atomic(journal_path, "\n".join(lines) + "\n" if lines else "")


def read_journal(journal_path):
    p = Path(journal_path)
    if not p.is_file():
        raise L.ToolError("E-JOURNAL-BROKEN", "journal not found: " + p.name,
                          L.EXIT_USAGE)
    entries = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise L.ToolError("E-JOURNAL-BROKEN",
                              "journal line %d is not JSON: %s" % (i + 1, e),
                              L.EXIT_FAIL)
    return entries


def verify_chain(entries):
    """prev links + seq order + per-entry hashes. Returns E-* problems."""
    problems = []
    prev = GENESIS
    for i, e in enumerate(entries):
        if e.get("seq") != i:
            problems.append(L.err("E-JOURNAL-BROKEN",
                                  "entry %d has seq=%r (expected %d)" % (i, e.get("seq"), i)))
        if e.get("prev") != prev:
            problems.append(L.err("E-JOURNAL-BROKEN",
                                  "entry %d prev-link broken (expected %s..., got %s...)"
                                  % (i, prev[:12], str(e.get("prev"))[:12])))
        want = entry_hash(e)
        if e.get("hash") != want:
            problems.append(L.err("E-JOURNAL-BROKEN",
                                  "entry %d (%s) hash mismatch: declared %s..., computed %s..."
                                  % (i, e.get("path"), str(e.get("hash"))[:12], want[:12])))
        prev = e.get("hash") or prev
    return problems


def verify_artifacts(entries, root):
    """Every journaled file must still hash to what the journal recorded."""
    problems = []
    root = Path(root)
    for e in entries:
        p = root / str(e.get("path", ""))
        if not p.is_file():
            problems.append(L.err("E-JOURNAL-BROKEN",
                                  "journaled artifact missing: " + str(e.get("path"))))
            continue
        got = L.file_sha256(p)
        if got != e.get("sha256"):
            problems.append(L.err(
                "E-ARTIFACT-DRIFT",
                "artifact changed after journaling: %s (journaled %s..., now %s...)"
                % (e.get("path"), str(e.get("sha256"))[:12], got[:12])))
    return problems


def main():
    ap = argparse.ArgumentParser(description="evidence hash chain (verify / rebuild)")
    ap.add_argument("--verify", default="", help="journal.jsonl to verify")
    ap.add_argument("--root", default="", help="run directory the paths resolve against")
    ap.add_argument("--rebuild", default="", help="rebuild a journal for this directory")
    ap.add_argument("--out", default="", help="where to write the rebuilt journal")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.rebuild:
        entries = build_entries(args.rebuild)
        out = Path(args.out) if args.out else (Path(args.rebuild) / "journal.jsonl")
        write_journal(out, entries)
        return L.emit(SCRIPT, status="ok", code=L.EXIT_OK,
                      data={"journal": out.name, "entries": len(entries),
                            "tail": entries[-1]["hash"] if entries else GENESIS})

    if not args.verify:
        raise L.ToolError("E-USAGE", "nothing to do: pass --verify <journal> (or --rebuild)",
                          L.EXIT_USAGE)
    entries = read_journal(args.verify)
    problems = verify_chain(entries)
    if args.root:
        problems += verify_artifacts(entries, args.root)
    doc = {"entries": len(entries),
           "tail": entries[-1]["hash"] if entries else GENESIS,
           "problems": [p["msg"] for p in problems[:40]]}
    return L.emit(SCRIPT,
                  status="ok" if not problems else "fail",
                  code=L.EXIT_OK if not problems else L.EXIT_FAIL,
                  data=doc, errors=problems or None)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
