"""Outward-claim registry: hashes, mechanisms, and coverage.

A promise in prose is not a contract unless something breaks when it changes.
This module turns each outward promise into data:

    {id, file, anchor, text_hash, mechanism, ...}

and verifies four things:

1. the anchor still exists in the document;
2. the sentence still hashes to what the registry recorded (edit the doc
   without updating the registry -> red);
3. the declared mechanism is backed by proof (a passing assertion, a
   declared-boundary attack, or an explicit ``unverified`` note);
4. the fraction of mandatory sentences that are registered does not fall
   below the declared floor (a ratchet, not a one-time audit).

Skill-specific inputs are injected: which files to scan, which words make a
sentence mandatory, which assertions exist, which attacks are declared
leaks, and which assertion ids may be skipped for a missing capability.
Nothing here reads a global root or a skill's source tree on import.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

DEFAULT_SCHEMA = "verdict-kit/claims-v1"
DEFAULT_SENTENCE_SPLIT = r"[。！？\n]"
DEFAULT_ASSERTION_RE = r"""add\(\s*["']([^"']+)["']"""
_WS = re.compile(r"\s+")


class ClaimRegistryError(ValueError):
    """The registry itself is not a usable registry."""


def normalize(text: object) -> str:
    """Collapse all whitespace runs to one space and trim."""
    return _WS.sub(" ", str(text)).strip()


def sentences(text: str, split: str = DEFAULT_SENTENCE_SPLIT,
              fence: str = "```") -> list[str]:
    """Sentences of a document, with fenced code blocks removed.

    A command inside a code block is not an outward promise. Sentence
    splitting is configurable because not every skill writes Chinese prose,
    but the default matches the original registry exactly.
    """
    pattern = re.compile(split)
    out: list[str] = []
    in_fence = False
    for line in str(text).splitlines():
        if line.lstrip().startswith(fence):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for raw in pattern.split(line):
            s = normalize(raw)
            if s:
                out.append(s)
    return out


def text_hash(text: str) -> str:
    """Hash of the NORMALIZED sentence, prefixed like the original registry."""
    return "sha256:" + hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def scan_assertion_ids(source: str, pattern: str = DEFAULT_ASSERTION_RE) -> set[str]:
    """Assertion ids declared by a harness source file.

    This is a text scan on purpose: importing the consumer's selftest would
    execute it. It matches the original ``add("A-...")`` convention; a skill
    with a different declaration style passes its own ``pattern``.
    """
    return set(re.findall(pattern, str(source)))


def _resolve(root: Path, rel: str) -> Path:
    p = Path(str(rel))
    if p.is_absolute():
        raise ClaimRegistryError("claim file must be relative: " + str(rel))
    root = root.resolve()
    target = (root / p).resolve()
    if target != root and not target.is_relative_to(root):
        raise ClaimRegistryError("claim file escapes the skill root: " + str(rel))
    return target


@dataclass
class ClaimResult:
    id: str
    mechanism: str
    ok: bool
    problems: list[str] = field(default_factory=list)
    skipped_optional: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {"id": self.id, "mechanism": self.mechanism, "ok": self.ok,
               "problems": list(self.problems)}
        if self.skipped_optional:
            out["skipped_optional"] = list(self.skipped_optional)
        return out


@dataclass
class Coverage:
    mandatory_sentences: int
    registered: int
    ratio: float
    floor: float

    @property
    def ok(self) -> bool:
        return self.ratio >= self.floor

    def as_dict(self) -> dict:
        return {"mandatory_sentences": self.mandatory_sentences,
                "registered": self.registered,
                "ratio": round(self.ratio, 4),
                "floor": self.floor}


@dataclass
class Report:
    claims: int
    ok: int
    by_mechanism: dict
    coverage: Coverage
    problems: list[str]
    results: list[ClaimResult]

    @property
    def healthy(self) -> bool:
        return not self.problems and self.coverage.ok

    def as_dict(self) -> dict:
        return {
            "claims": self.claims,
            "ok": self.ok,
            "by_mechanism": dict(sorted(self.by_mechanism.items())),
            "coverage": self.coverage.as_dict(),
            "problems": list(self.problems[:60]),
            "results": [r.as_dict() for r in self.results],
        }


class ClaimRegistry:
    """A loaded, schema-checked claims registry."""

    def __init__(self, doc: Mapping, schema: str = DEFAULT_SCHEMA):
        if not isinstance(doc, Mapping):
            raise ClaimRegistryError("registry must be a JSON object")
        if doc.get("schema") != schema:
            raise ClaimRegistryError(
                "claims schema mismatch: expected %r, got %r"
                % (schema, doc.get("schema")))
        claims = doc.get("claims")
        if not isinstance(claims, list) or not claims:
            raise ClaimRegistryError("claims registry is empty")
        self.schema = schema
        self.claims: list[dict] = [dict(c) for c in claims if isinstance(c, Mapping)]
        if len(self.claims) != len(claims):
            raise ClaimRegistryError("every claim must be a JSON object")

    @classmethod
    def load(cls, path: str | Path, schema: str = DEFAULT_SCHEMA) -> "ClaimRegistry":
        p = Path(path)
        if not p.is_file():
            raise ClaimRegistryError("claims registry not found: " + p.name)
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ClaimRegistryError("malformed claims registry: " + str(e))
        return cls(doc, schema=schema)

    # -- validation ------------------------------------------------------
    def validate(
        self,
        root: str | Path,
        *,
        assertion_ids: Iterable[str],
        attacks: Mapping[str, Mapping] | None = None,
        capability_skips: Iterable[str] = (),
        selftest_results: Mapping[str, object] | None = None,
        scan_files: Iterable[str] = (),
        mandatory_words: Iterable[str] = (),
        coverage_floor: float = 1.0,
        documents: Mapping[str, str] | None = None,
        require_hash: bool = True,
    ) -> Report:
        """Check every claim and the coverage ratchet.

        ``documents`` lets a caller supply text instead of reading the root;
        that is how the smoke test runs without a real skill checkout.
        """
        root = Path(root)
        ids = set(assertion_ids)
        attacks = dict(attacks or {})
        skips = set(capability_skips)
        passed = dict(selftest_results or {})
        docs = dict(documents or {})
        seen: set[str] = set()
        registered_anchors: list[tuple[str, str]] = []
        results: list[ClaimResult] = []
        problems: list[str] = []

        def doc_text(fname: str) -> str | None:
            if fname in docs:
                return docs[fname]
            p = _resolve(root, fname)
            if not p.is_file():
                return None
            return p.read_text(encoding="utf-8")

        def check_test(entry: ClaimResult, tid: object) -> None:
            t = str(tid)
            if t not in ids:
                entry.problems.append("unknown test id: " + t)
                return
            if not passed:
                return  # no log supplied: static checks only
            if t not in passed:
                entry.problems.append("test missing from the supplied selftest log: " + t)
                return
            ok = passed.get(t)
            if ok is True:
                return
            if ok is None and t in skips:
                entry.skipped_optional.append(t)
                return
            if ok is None:
                entry.problems.append(
                    "test was skipped but is not a declared capability skip: " + t)
            else:
                entry.problems.append("test did not pass: " + t)

        for claim in self.claims:
            cid = str(claim.get("id", "?"))
            entry = ClaimResult(id=cid, mechanism=str(claim.get("mechanism")), ok=True)
            if cid in seen:
                entry.problems.append("duplicate claim id")
            seen.add(cid)

            fname = str(claim.get("file", ""))
            text = doc_text(fname) if fname else None
            if text is None:
                entry.problems.append("file not found: " + fname)
            else:
                anchor = str(claim.get("anchor", ""))
                sentence = next((s for s in sentences(text) if anchor and anchor in s), None)
                if sentence is None:
                    entry.problems.append(
                        "anchor not found in " + fname + ": " + anchor[:40])
                else:
                    registered_anchors.append((fname, sentence))
                    want = str(claim.get("text_hash", ""))
                    got = text_hash(sentence)
                    if require_hash and not want:
                        entry.problems.append("claim has no text_hash")
                    elif want and want != got:
                        entry.problems.append(
                            "text changed: registry says " + want + ", file says " + got)

            mech = claim.get("mechanism")
            if mech == "assertion":
                tests = claim.get("tests") or []
                if not tests:
                    entry.problems.append("assertion claim names no test")
                for t in tests:
                    check_test(entry, t)
            elif mech == "boundary-declaration":
                proof = str(claim.get("boundary_proof", ""))
                if not proof:
                    entry.problems.append("boundary claim names no proof")
                elif proof.startswith("attack:"):
                    aid = proof.split(":", 1)[1]
                    atk = attacks.get(aid)
                    if atk is None:
                        entry.problems.append("unknown attack: " + aid)
                    elif atk.get("expect") != "leak":
                        entry.problems.append(
                            "attack " + aid + " is expected to be blocked, so it "
                            "cannot prove a boundary")
                elif proof.startswith("test:"):
                    check_test(entry, proof.split(":", 1)[1])
                else:
                    entry.problems.append(
                        "boundary_proof must be attack:<id> or test:<id>")
            elif mech == "unverified":
                if not str(claim.get("note", "")).strip():
                    entry.problems.append(
                        "unverified claim needs a note saying why it is not checked")
            else:
                entry.problems.append("unknown mechanism: " + str(mech))

            entry.ok = not entry.problems
            results.append(entry)
            problems.extend(cid + ": " + p for p in entry.problems)

        # -- coverage ratchet --------------------------------------------
        mandatory: set[tuple[str, str]] = set()
        words = tuple(str(w) for w in mandatory_words)
        for fname in scan_files:
            text = doc_text(str(fname))
            if text is None:
                continue
            for s in sentences(text):
                if len(s) <= 8:
                    continue
                if s.startswith("|") or s.startswith("#") or s.startswith("```"):
                    continue
                if any(w in s for w in words):
                    mandatory.add((str(fname), s))
        covered: set[tuple[str, str]] = set()
        for fname, s in mandatory:
            for rf, rs in registered_anchors:
                if rf == fname and normalize(rs) == normalize(s):
                    covered.add((fname, s))
                    break
        ratio = (len(covered) / float(len(mandatory))) if mandatory else 1.0
        coverage = Coverage(len(mandatory), len(covered), ratio, float(coverage_floor))
        if ratio < coverage.floor:
            problems.append(
                "claim coverage %.3f is below the declared floor %.3f"
                % (ratio, coverage.floor))

        by_mech: dict[str, int] = {}
        for r in results:
            by_mech[r.mechanism] = by_mech.get(r.mechanism, 0) + 1
        return Report(len(results), sum(1 for r in results if r.ok),
                      by_mech, coverage, problems, results)
