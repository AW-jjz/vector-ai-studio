"""Fixture regression: argument expansion, materialisation, one case, summary.

A "fixtures regression" is the cheapest honest gate a CLI skill has: declare a
handful of invocations, run each one as a REAL subprocess, and compare the exit
code and the emitted error codes against what the case says should happen. It
goes wrong in the same four places every time, which is what this module owns:

* **placeholders** -- ``@F@`` / ``@OUT@`` / ``@RUN@`` let one declaration work
  from any temp root. Expansion is pure and ordered; nothing is guessed.
* **materialisation** -- a case may need a complete run directory (files the
  CLI will not create itself). ``setup_dir`` is copied wholesale first, and a
  missing source is an error, never a silently empty directory that makes the
  case "pass" for the wrong reason.
* **matching** -- the wanted codes are compared EXACTLY (deduplicated and
  sorted on both sides). A missing code fails, an extra code fails, and a
  warning can never satisfy a wanted code just because its text starts with
  ``E-``. Only ``errors[].code`` is read.
* **the count** -- a truncated declaration used to report ``total=0,
  all_ok=true`` and exit 0: a green run over no cases. ``check_declared_count``
  makes a shrink visible instead.

Deliberately NOT here: the fixture schema name, the fixture document's keys,
which scripts exist, CLI flags, the process exit codes and the word the failure
is reported as. The kit says "this case did not match"; the skill decides what
that means to its own callers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_TIMEOUT = 90
SUBFAIL_EXIT = -1


class FixtureError(Exception):
    """A fixture declaration or materialisation problem.

    The kit names no exit code and no CLI error code: the caller maps this to
    whatever its own contract requires (the source skill turns it into a
    schema/usage failure).
    """


def expand_args(
    raw_args: Sequence[str],
    *,
    fixtures_dir: str | Path,
    out_dir: str | Path,
    run_dir: str | Path,
) -> list[str]:
    """Replace ``@F@`` / ``@OUT@`` / ``@RUN@`` in every argument.

    Order is fixtures -> out -> run, matching the declaration order documented
    by the source skill. Arguments are stringified first, so a numeric flag in
    a JSON declaration still becomes a proper argv element.
    """
    fdir, odir, rdir = str(fixtures_dir), str(out_dir), str(run_dir)
    expanded = []
    for a in raw_args:
        a = (str(a).replace("@F@", fdir)
             .replace("@OUT@", odir)
             .replace("@RUN@", rdir))
        expanded.append(a)
    return expanded


def materialize(
    case: Mapping,
    *,
    fixtures_dir: str | Path,
    run_dir: str | Path,
) -> str | None:
    """Copy a case's ``setup_dir`` (relative to the fixtures) into ``run_dir``.

    Returns ``None`` when the case declares no ``setup_dir`` -- an absent
    setup is legal, an absent *source* is not: that would hand the CLI an empty
    directory and the case would then fail (or pass) for the wrong reason.

    The destination must not exist yet. Every case owns exactly one run
    directory (``out_dir / id``), so a second case with the same ``id`` that
    also declares a ``setup_dir`` lands on a directory the first case already
    created. ``copytree`` would raise ``FileExistsError`` -- an uncaught
    ``OSError`` that reads as an internal crash rather than as the broken
    declaration it is. Two cases sharing an id is a definition error, so it is
    refused here in the same vocabulary as every other one.
    """
    setup_dir = case.get("setup_dir")
    if not setup_dir:
        return None
    src = Path(fixtures_dir) / str(setup_dir)
    if not src.is_dir():
        raise FixtureError("setup_dir not found for case " + str(case.get("id", "unnamed"))
                           + ": " + str(src))
    if Path(run_dir).exists():
        raise FixtureError("duplicate case id (run dir already exists): "
                           + str(case.get("id", "unnamed")))
    shutil.copytree(src, Path(run_dir))
    return str(run_dir)


def run_case(
    case: Mapping,
    *,
    scripts_dir: str | Path,
    fixtures_dir: str | Path,
    out_dir: str | Path,
    python: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run one case as a subprocess and compare it against its declaration.

    The result dict is the case's public record: ``id / script / ok /
    want_exit / got_exit / want_codes / got_codes / want_acceptance /
    got_acceptance``. Insertion order is part of the shape -- the skill writes
    these straight into its envelope, so a reordering here would look like a
    behaviour change downstream.
    """
    cid = str(case.get("id", "unnamed"))
    script = str(case.get("script", ""))
    # The script must exist before anything is run or materialised. Until r95
    # a missing file was left to the interpreter: CPython starts fine, cannot
    # open the argument, prints to stderr and exits 2 with empty stdout. That
    # produced ``rc=2, got_codes=[]`` -- indistinguishable from a case that
    # declares ``want_exit: [2], want_codes: []``, so such a case would have
    # passed without its script ever running. A missing script is a broken
    # declaration, not a verdict about the CLI.
    script_path = Path(scripts_dir) / script
    if not script_path.is_file():
        raise FixtureError("script not found for case " + cid + ": " + str(script_path))
    run_dir = Path(out_dir) / cid
    materialize(case, fixtures_dir=fixtures_dir, run_dir=run_dir)
    expanded = expand_args([str(a) for a in case.get("args", [])],
                           fixtures_dir=fixtures_dir, out_dir=out_dir,
                           run_dir=run_dir)
    cmd = [python or sys.executable, str(Path(scripts_dir) / script)] + expanded
    try:
        r = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=False)
        rc, out = r.returncode, (r.stdout or "")
    except (OSError, subprocess.SubprocessError):
        # The INTERPRETER itself could not be started (the executable in
        # ``python`` is absent or not runnable). A missing *script* never
        # reaches here -- it is refused above, in the declaration's own
        # vocabulary. rc=-1 can never be in a declared want_exit, so this is a
        # failed case, not a crash of the whole regression run.
        rc, out = SUBFAIL_EXIT, ""

    got_codes: list[str] = []
    try:
        env = json.loads(out)
        # Only real E-* error codes count. A warning (even one whose text
        # starts with "E-") must never be able to satisfy a wanted code --
        # that is the blind spot of a subset check. Duplicates collapse (the
        # "one failure, one code" convention); a distinct unexpected code
        # still fails the match.
        got_codes = sorted({str(e.get("code", ""))
                            for e in env.get("errors", []) if isinstance(e, dict)})
    except json.JSONDecodeError:
        got_codes = []

    want_exit = [int(x) for x in case.get("want_exit", [0])]
    want_codes = sorted({str(x) for x in case.get("want_codes", [])})
    ok = (rc in want_exit and got_codes == want_codes)

    # Optional acceptance.json assertions (dotted path -> expected literal).
    # Some negatives are a degraded verdict rather than an error code (a level
    # downgrade, a failed hard gate); without this they would only prove
    # "exit != 0". Reading the field makes them hit something concrete.
    want_acc = case.get("want_acceptance") or {}
    acc_detail: dict = {}
    if want_acc:
        acc_path = run_dir / "acceptance.json"
        try:
            acc_doc = json.loads(acc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            acc_doc = {}
        for dotted, expected in want_acc.items():
            cur = acc_doc
            for seg in str(dotted).split("."):
                cur = cur.get(seg) if isinstance(cur, dict) else None
            acc_detail[dotted] = cur
            if cur != expected:
                ok = False

    return {"id": cid, "script": script, "ok": ok,
            "want_exit": want_exit, "got_exit": rc,
            "want_codes": want_codes, "got_codes": got_codes,
            "want_acceptance": want_acc, "got_acceptance": acc_detail}


def summarize(results: Sequence[Mapping]) -> dict:
    """Count the results. Key order is the shape the caller publishes."""
    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    return {"total": len(results), "passed": passed, "failed": len(failed),
            "all_ok": not failed, "cases": list(results)}


def check_declared_count(cases: Sequence, cases_expected) -> int:
    """Refuse a truncated declaration: the declared count must be present and equal.

    A bool is an int in Python; ``cases_expected: true`` is a typo, not a
    count of one, so it is rejected explicitly.
    """
    if isinstance(cases_expected, bool) or not isinstance(cases_expected, int):
        raise FixtureError("fixture set must declare an integer case count")
    if len(cases) != cases_expected:
        raise FixtureError("fixture set has %d cases, declared %d"
                           % (len(cases), cases_expected))
    return cases_expected
