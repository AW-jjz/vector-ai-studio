"""Attack plumbing: mutation ops and declared-expectation checks.

The point of the extraction is the DATA/RUNNER split: an attack is a JSON
document (what to break, what must then be observed), and the code that
applies and judges it is the same in every skill. Only three things are
genuinely local to a consuming skill and stay there:

* the **base run** -- how to materialise a run the skill accepts as honest;
* the **runner** -- which script to invoke and how;
* the **pass predicate** -- what "a pass" means in that skill's vocabulary.

Everything else moved here:

    apply_mutations(dest, mutations)   the op vocabulary
    dotted_get(doc, dotted)            one dotted lookup, never raises
    evaluate(spec, result)             the declared-expectation checks

Two deliberate behaviours are carried over unchanged from the source skill,
because both are honesty properties, not conveniences:

* an unknown op is a **usage** error (`E-USAGE`, exit 2), never a silent skip.
  A typo that quietly did nothing would turn a guard into decoration.
* `patch_json` keeps `path` (the FILE) and `json_path` (the dotted path
  INSIDE it) apart. A file called ``selftest.json`` would otherwise be read as
  the JSON path ``selftest`` -> ``json``.

`expect_no_pass` is the one check that cannot be written generically: whether
an acceptance document claims a pass is the consuming skill's vocabulary. So
the caller injects ``too_good(acceptance)``. There is NO default: a spec that
sets ``expect_no_pass`` without an injected predicate is a programming error
and raises ``ValueError``. Guessing a pass word here would put one skill's
vocabulary inside the kit, which is exactly the drift this package exists to
prevent.
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from .envelope import EXIT_USAGE, ToolError, write_json_atomic

#: Signals "unknown op" -- registered here so a consuming skill can name the
#: exact code it forwards instead of surface whatever the kit invents.
UNKNOWN_OP_CODE = "E-USAGE"

MUTATION_OPS = ("delete", "write_text", "write_bytes_b64", "write_json",
                "patch_json", "copy")


def dotted_get(doc: object, dotted: str) -> object:
    """Follow ``a.b.c`` through nested dicts; a non-dict or missing key is None.

    Returning None (rather than raising) is what lets `expect_field` report
    "the field is absent" instead of crashing the whole attack run.
    """
    cur = doc
    for seg in str(dotted).split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def apply_mutations(dest: str | Path, mutations: Iterable[Mapping] | None, *,
                    write_json: Callable[[Path, object], None] | None = None
                    ) -> None:
    """Apply an attack's mutations to a run directory, in order.

    ``write_json`` is injectable so a skill that owns a byte-exact atomic JSON
    writer can keep the file format identical; the default is this package's
    own writer, which already uses the same canonical formatting.
    """
    dest = Path(dest)
    wj = write_json or write_json_atomic
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
            wj(target, m["doc"])
        elif op == "patch_json":
            # `path` is the FILE; `json_path` (dotted) is where inside it.
            doc = json.loads(target.read_text(encoding="utf-8"))
            jpath = str(m.get("json_path", "")).split(".") if m.get("json_path") else []
            if not jpath:
                doc[m["key"]] = m["value"]
            else:
                cur = doc
                for seg in jpath[:-1]:
                    cur = cur.setdefault(seg, {})
                cur[jpath[-1]] = m["value"]
            wj(target, doc)
        elif op == "copy":
            shutil.copyfile(dest / m["from"], dest / m["to"])
        else:
            raise ToolError(UNKNOWN_OP_CODE, "unknown attack op: " + str(op),
                            EXIT_USAGE)


def evaluate(spec: Mapping, result: Mapping, *,
             too_good: Callable[[Mapping], bool] | None = None
             ) -> list[str]:
    """Check one observed result against what the attack declared.

    ``spec`` carries the declarations (``expect_exit``, ``expect_codes``,
    ``reject_codes``, ``expect_no_pass``, ``expect_grade``, ``expect_field``);
    ``result`` carries ``exit``, ``codes`` and ``acceptance``. Returns the
    list of problems, in the fixed order the source skill emitted them, so a
    caller can join them verbatim.

    ``too_good`` is required whenever the spec sets ``expect_no_pass``. A
    missing predicate raises ``ValueError`` rather than falling back to any
    built-in pass word: the kit does not know this skill's vocabulary, and a
    silent default would turn "we forgot to inject it" into a green run.
    """
    problems: list[str] = []
    codes: Sequence[str] = result["codes"]
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
        if too_good is None:
            raise ValueError(
                "expect_no_pass requires an injected too_good predicate")
        if too_good(acceptance):
            problems.append("the attack still claims a pass (grade=" +
                            str(acceptance.get("grade")) + ")")
    eg = spec.get("expect_grade")
    if eg is not None and acceptance.get("grade") != eg:
        problems.append("grade is " + repr(acceptance.get("grade")) +
                        ", expected " + repr(eg))
    for dotted, want_value in (spec.get("expect_field") or {}).items():
        got = dotted_get(acceptance, dotted)
        if got != want_value:
            problems.append(dotted + " is " + repr(got) + ", expected " +
                            repr(want_value))
    return problems
