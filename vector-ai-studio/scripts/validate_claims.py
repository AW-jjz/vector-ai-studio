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
    if not path.is_file():
        raise L.ToolError("E-SCHEMA", "claims registry not found: " + path.name,
                          L.EXIT_USAGE)
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise L.ToolError("E-SCHEMA", "malformed claims registry: " + str(e),
                          L.EXIT_USAGE)
    if reg.get("schema") != CLAIMS_SCHEMA:
        raise L.ToolError("E-SCHEMA",
                          "claims schema mismatch: expected " + CLAIMS_SCHEMA,
                          L.EXIT_USAGE)

    claims = reg.get("claims") or []
    if not isinstance(claims, list) or not claims:
        raise L.ToolError("E-SCHEMA", "claims registry is empty", L.EXIT_USAGE)

    ids = selftest_ids()
    attacks = attack_ids()
    results = []
    passed = {}
    if args.selftest_json and Path(args.selftest_json).is_file():
        try:
            doc = json.loads(Path(args.selftest_json).read_text(encoding="utf-8"))
            for a in (doc.get("data") or doc).get("assertions", []):
                passed[str(a.get("id"))] = a.get("ok")
        except (OSError, json.JSONDecodeError):
            pass

    problems = []
    seen_ids = set()
    registered_anchors = []
    def _test_ran_ok(entry, t):
        """One referenced assertion must have passed in the supplied log.

        Skipped is not failed -- but only for an id the project has declared
        an optional capability (L.CAPABILITY_SKIPS, the same list H3 accepts
        skips from). A missing id, a failed id, or a skipped guard is a
        problem either way.
        """
        if t not in ids:
            entry["problems"].append("unknown test id: " + t)
            return
        if not passed:
            return  # no log supplied: the static registry checks ran above
        if t not in passed:
            entry["problems"].append(
                "test missing from the supplied selftest log: " + t)
            return
        ok = passed.get(t)
        if ok is True:
            return
        if ok is None and t in L.CAPABILITY_SKIPS:
            entry.setdefault("skipped_optional", []).append(t)
            return
        if ok is None:
            entry["problems"].append(
                "test was skipped but is not a declared capability skip: " + t)
        else:
            entry["problems"].append("test did not pass: " + t)

    for claim in claims:
        cid = str(claim.get("id", "?"))
        entry = {"id": cid, "mechanism": claim.get("mechanism"), "ok": True,
                 "problems": []}
        if cid in seen_ids:
            entry["problems"].append("duplicate claim id")
        seen_ids.add(cid)
        fname = str(claim.get("file", ""))
        fpath = ROOT / fname if fname else None
        if not fpath or not fpath.is_file():
            entry["problems"].append("file not found: " + fname)
        else:
            text = fpath.read_text(encoding="utf-8")
            anchor = str(claim.get("anchor", ""))
            sentence = _sentence_with(text, anchor)
            if sentence is None:
                entry["problems"].append("anchor not found in " + fname +
                                         ": " + anchor[:40])
            else:
                registered_anchors.append((fname, sentence))
                want = str(claim.get("text_hash", ""))
                got = _sha(sentence)
                if want and want != got:
                    entry["problems"].append(
                        "text changed: registry says " + want + ", file says " + got)
        mech = claim.get("mechanism")
        if mech == "assertion":
            tests = claim.get("tests") or []
            if not tests:
                entry["problems"].append("assertion claim names no test")
            for t in tests:
                _test_ran_ok(entry, str(t))
        elif mech == "boundary-declaration":
            proof = str(claim.get("boundary_proof", ""))
            if not proof:
                entry["problems"].append("boundary claim names no proof")
            elif proof.startswith("attack:"):
                aid = proof.split(":", 1)[1]
                atk = attacks.get(aid)
                if atk is None:
                    entry["problems"].append("unknown attack: " + aid)
                elif atk.get("expect") != "leak":
                    entry["problems"].append(
                        "attack " + aid + " is expected to be blocked, so it "
                        "cannot prove a boundary")
            elif proof.startswith("test:"):
                _test_ran_ok(entry, proof.split(":", 1)[1])
            else:
                entry["problems"].append(
                    "boundary_proof must be attack:<id> or test:<id>")
        elif mech == "unverified":
            if not str(claim.get("note", "")).strip():
                entry["problems"].append(
                    "unverified claim needs a note saying why it is not checked")
        else:
            entry["problems"].append("unknown mechanism: " + str(mech))
        entry["ok"] = not entry["problems"]
        results.append(entry)
        for p in entry["problems"]:
            problems.append(cid + ": " + p)

    # ---- coverage: how many of the outward promises are registered at all ----
    # A set, not a list: the same sentence may appear twice in a document, and
    # a promise is registered once however many times it is repeated.
    mandatory = set()
    for fname in C.CLAIMS.SCAN_FILES:
        p = ROOT / fname
        if not p.is_file():
            continue
        for s in _sentences(p.read_text(encoding="utf-8")):
            if len(s) <= 8:
                continue
            if s.startswith("|") or s.startswith("#") or s.startswith("```"):
                continue
            if any(w in s for w in C.CLAIMS.MANDATORY_WORDS):
                mandatory.add((fname, s))
    covered = set()
    for fname, s in mandatory:
        for rf, rs in registered_anchors:
            if rf == fname and _norm(rs) == _norm(s):
                covered.add((fname, s))
                break
    ratio = (len(covered) / float(len(mandatory))) if mandatory else 1.0
    if ratio < C.CLAIMS.COVERAGE_FLOOR:
        problems.append("claim coverage %.3f is below the declared floor %.3f" %
                        (ratio, C.CLAIMS.COVERAGE_FLOOR))

    by_mech = {}
    for r in results:
        by_mech[str(r["mechanism"])] = by_mech.get(str(r["mechanism"]), 0) + 1
    doc = {
        "schema": "vector-ai-studio/claims-result-v1",
        "claims": len(results),
        "ok": sum(1 for r in results if r["ok"]),
        "by_mechanism": by_mech,
        "coverage": {"mandatory_sentences": len(mandatory),
                     "registered": len(covered),
                     "ratio": round(ratio, 4),
                     "floor": C.CLAIMS.COVERAGE_FLOOR},
        "problems": problems[:60],
        "results": results,
    }
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
