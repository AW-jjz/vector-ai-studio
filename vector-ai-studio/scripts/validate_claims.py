#!/usr/bin/env python3
"""Check that every outward promise is either tested, or declared a boundary.

Why this exists (r72, plan 4.1): the skill's honesty was documented in prose
and enforced in code, and the two could drift apart silently. r71's audit found
exactly that: `smoke_ai` was documented as "NEVER writes ok" and the code
wrote ok anyway. Nothing in CI compared the two.

So the promises become DATA (`contract/claims.json`) and this script checks
them:

  assertion           the promise must name at least one real, passing
                      selftest assertion. A promise with no test behind it is
                      decoration, and the script says so.
  boundary-declaration the promise must name a PROOF that the limit is real:
                      either an attack that really gets through
                      (`attack:<id>`) or an assertion that pins the boundary
                      (`test:<id>`). Declaring a boundary you have not
                      demonstrated is the other kind of dishonesty: it
                      understates what the tool cannot do.
  unverified          the promise is registered but not machine-checked. This
                      is allowed and it is COUNTED: the number of unverified
                      promises is a number the project has to push down, so
                      hiding them in a file nobody reads is not an option.

Every claim also carries the hash of the sentence it registers. Edit the
document without updating the registry and the run goes red -- which is the
point: a changed promise has to be re-examined, not silently inherited.

    python scripts/validate_claims.py --json

When a selftest log is supplied with --selftest-json, each named assertion must
have passed in it. One exception, mirroring the H3 policy exactly: an
assertion that was NOT EVALUATED (ok=null) because its optional capability is
absent on this machine is acceptable iff the id is in
L.CAPABILITY_SKIPS -- the same whitelist H3 accepts skips from. A guard that
was skipped is still a problem, and so is any assertion that actually failed.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import _contract as C

# Phase 1b: the vendored registry owns the checks. Importing the
# package would create scripts/_vendor/.../__pycache__ (A22), so
# bytecode writing is disabled for this CLI process.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor" / "verdict-kit"))
import verdict_kit.registry as VK  # noqa: E402

SCRIPT = "validate_claims.py"
CLAIMS_SCHEMA = "vector-ai-studio/claims-v1"
ROOT = L.skill_root()
SENTENCE_SPLIT = re.compile(r"[。！？\n]")


def _norm(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def _sentences(text):
    """Sentences of a document, with fenced code blocks removed.

    A command line inside a code block is not an outward promise; a shell
    comment that happens to contain 必须 is not one either. Keeping code out
    of the scan keeps the registry about promises, not about syntax.
    """
    out = []
    in_fence = False
    for line in str(text).splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for raw in SENTENCE_SPLIT.split(line):
            s = _norm(raw)
            if s:
                out.append(s)
    return out


def _sha(text):
    return "sha256:" + hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _sentence_with(text, anchor):
    """The sentence that contains the anchor (or None)."""
    for s in _sentences(text):
        if anchor and anchor in s:
            return s
    return None


def selftest_ids():
    """The assertion ids the selftest can report (its own registry)."""
    src = (ROOT / "scripts" / "selftest.py").read_text(encoding="utf-8")
    return set(re.findall(r"add\(\"([^\"]+)\"", src))


def attack_ids():
    """The shipped attacks, with their declared expectation."""
    out = {}
    for p in sorted((ROOT / "tests" / "attacks").glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict) and doc.get("id"):
            out[str(doc["id"])] = doc
    return out


def main():
    ap = argparse.ArgumentParser(description="validate the outward-claim registry")
    ap.add_argument("--claims", default="")
    ap.add_argument("--selftest-json", default="",
                    help="selftest.json to check the referenced assertions against")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = Path(args.claims) if args.claims else (ROOT / C.CLAIMS.REGISTRY)
    try:
        reg = VK.ClaimRegistry.load(path, schema=CLAIMS_SCHEMA)
    except VK.ClaimRegistryError as e:
        raise L.ToolError("E-SCHEMA", str(e), L.EXIT_USAGE)

    passed = {}
    if args.selftest_json and Path(args.selftest_json).is_file():
        try:
            doc = json.loads(Path(args.selftest_json).read_text(encoding="utf-8"))
            for a in (doc.get("data") or doc).get("assertions", []):
                passed[str(a.get("id"))] = a.get("ok")
        except (OSError, json.JSONDecodeError):
            pass

    report = reg.validate(
        ROOT,
        assertion_ids=selftest_ids(),
        attacks=attack_ids(),
        capability_skips=L.CAPABILITY_SKIPS,
        selftest_results=passed,
        scan_files=C.CLAIMS.SCAN_FILES,
        mandatory_words=C.CLAIMS.MANDATORY_WORDS,
        coverage_floor=C.CLAIMS.COVERAGE_FLOOR,
    )
    doc = {"schema": "vector-ai-studio/claims-result-v1"}
    doc.update(report.as_dict())
    problems = list(report.problems)
    errors = []
    if problems:
        errors.append(L.err("E-CLAIMS-DRIFT",
                            "claim registry does not hold: " +
                            "; ".join(problems[:6])))
    return L.emit(SCRIPT, status="fail" if problems else "ok",
                  code=L.EXIT_FAIL if problems else L.EXIT_OK,
                  data=doc, errors=errors)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
