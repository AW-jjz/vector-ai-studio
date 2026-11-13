"""Deterministic JSON envelope, exit codes, and the one CLI wrapper.

Contract (identical in spirit to vector-ai-studio's ``lib_common``):

* stdout is exactly one JSON object, UTF-8, ``indent=2``, trailing newline;
* key order is fixed, so the same input yields byte-identical output;
* ``code`` always equals the process exit code;
* ``errors[]`` carries ``E-*`` codes; a warning is never an error;
* no wall-clock timestamp ever enters a judgment envelope.

The module owns no root, no config and no skill vocabulary. Pass ``schema``
explicitly, or set it once on :class:`verdict_kit.Context`.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "verdict-kit/v1"

EXIT_OK = 0        # success
EXIT_FAIL = 1      # judgment / validation failed (business failure, not a crash)
EXIT_USAGE = 2     # bad usage or malformed input (explicit, never guessed)
EXIT_MISSING = 3   # dependency MISSING, honestly degraded (never silent success)
EXIT_ERROR = 4     # internal exception or engine failure

EXIT_CODES = frozenset({EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_MISSING, EXIT_ERROR})
# ``missing`` is kept because the source skill really emits it when every
# renderer is absent. Dropping it would force a behavior change during
# migration, which is exactly what a compatibility seam must not do.
STATUSES = ("ok", "degraded", "missing", "partial", "fail", "error")

# ``fail`` may only mean a business failure; usage/internal errors are errors.
_ALLOWED = {
    "ok": frozenset({EXIT_OK}),
    "degraded": frozenset({EXIT_OK, EXIT_MISSING}),
    "missing": frozenset({EXIT_MISSING}),
    # The source skill emits partial + exit 3 when every remaining assertion
    # passed but at least one optional capability was absent. It is not a
    # failure and it is not a clean pass either; the status carries that.
    "partial": frozenset({EXIT_MISSING}),
    "fail": frozenset({EXIT_FAIL}),
    "error": frozenset({EXIT_USAGE, EXIT_ERROR}),
}


class ToolError(Exception):
    """Explicit, non-guessed error carrying an ``E-*`` code and an exit code."""

    def __init__(self, code: str, msg: str, exit_code: int = EXIT_USAGE):
        super().__init__(msg)
        if not str(code).startswith("E-"):
            raise ValueError("error code must start with E-: " + str(code))
        if exit_code not in EXIT_CODES:
            raise ValueError("unknown exit code: " + str(exit_code))
        self.code = str(code)
        self.msg = str(msg)
        self.exit_code = int(exit_code)


class EnvelopeError(ValueError):
    """The envelope violates its own structural contract."""


def err(code: str, msg: str) -> dict:
    """One error entry. Shape is fixed so consumers can count codes."""
    if not str(code).startswith("E-"):
        raise EnvelopeError("error code must start with E-: " + str(code))
    if not str(msg).strip():
        raise EnvelopeError("error message must not be empty")
    return {"code": str(code), "msg": str(msg)}


def envelope(
    script: str,
    status: str = "ok",
    code: int = EXIT_OK,
    data: Mapping | None = None,
    errors: Sequence[Mapping] | None = None,
    warnings: Sequence[Any] | None = None,
    degradation: Mapping | None = None,
    artifacts: Mapping | None = None,
    run_id: str = "",
    schema: str = SCHEMA,
) -> dict:
    """Build an envelope with a fixed key order.

    ``degradation`` is omitted unless supplied; every other key is always
    present (an absent list and an empty list mean different things to a
    consumer, so the kit never omits them).
    """
    if status not in STATUSES:
        raise EnvelopeError("unknown status: " + repr(status))
    if isinstance(code, bool) or not isinstance(code, int) or code not in EXIT_CODES:
        raise EnvelopeError("code must be one of " + repr(sorted(EXIT_CODES)))
    if not str(script).strip():
        raise EnvelopeError("script must not be empty")
    if not str(schema).strip():
        raise EnvelopeError("schema must not be empty")

    env = {
        "schema": str(schema),
        "script": str(script),
        "run_id": str(run_id or ""),
        "status": status,
        "code": code,
        "data": dict(data) if data else {},
        "errors": [dict(e) for e in (errors or [])],
        "warnings": list(warnings or []),
        "artifacts": dict(artifacts) if artifacts else {},
    }
    if degradation is not None:
        env["degradation"] = dict(degradation)

    problems = _structural_problems(env)
    if problems:
        raise EnvelopeError("; ".join(problems))
    return env


# The module is ``verdict_kit.envelope``; the function cannot be exported under
# the same name at package level without shadowing the module. This alias is the
# explicit package-level entry point.
build_envelope = envelope


def _structural_problems(env: Mapping) -> list[str]:
    """Fatal shape errors: missing keys, wrong types, unknown enums."""
    problems: list[str] = []
    required = ("schema", "script", "run_id", "status", "code", "data",
                "errors", "warnings", "artifacts")
    for key in required:
        if key not in env:
            problems.append("missing key: " + key)
    if problems:
        return problems

    if not isinstance(env["schema"], str) or not env["schema"]:
        problems.append("schema must be a non-empty string")
    if not isinstance(env["script"], str) or not env["script"]:
        problems.append("script must be a non-empty string")
    if not isinstance(env["run_id"], str):
        problems.append("run_id must be a string")
    if env["status"] not in STATUSES:
        problems.append("unknown status: " + repr(env["status"]))
    code = env["code"]
    if isinstance(code, bool) or not isinstance(code, int) or code not in EXIT_CODES:
        problems.append("code must be one of " + repr(sorted(EXIT_CODES)))
    if not isinstance(env["data"], dict):
        problems.append("data must be an object")
    if not isinstance(env["artifacts"], dict):
        problems.append("artifacts must be an object")
    if not isinstance(env["warnings"], list):
        problems.append("warnings must be a list")
    if not isinstance(env["errors"], list):
        problems.append("errors must be a list")
        return problems

    for i, entry in enumerate(env["errors"]):
        if not isinstance(entry, dict):
            problems.append("errors[%d] must be an object" % i)
            continue
        c, m = entry.get("code"), entry.get("msg")
        if not isinstance(c, str) or not c.startswith("E-"):
            problems.append("errors[%d].code must be an E-* string" % i)
        if not isinstance(m, str) or not m.strip():
            problems.append("errors[%d].msg must be non-empty" % i)

    if "degradation" in env and not isinstance(env["degradation"], dict):
        problems.append("degradation must be an object")
    return problems


def validate(env: Mapping, policy: bool = True) -> list[str]:
    """Structural + policy checks. Returns problems instead of raising.

    Structural problems (missing keys, wrong types) make ``envelope()`` raise.
    Policy problems (status and code disagree, an error on a success) are
    opt-in here because a legacy skill may need a migration window -- the
    original vector-ai-studio has at least one ``status="ok"`` + ``code=1``
    emit. New code should call ``validate`` and refuse to ship a violation.
    """
    problems = _structural_problems(env)
    if not policy or problems:
        return problems

    status = env["status"]
    code = env["code"]
    if status in _ALLOWED and isinstance(code, int) and code in EXIT_CODES:
        if code not in _ALLOWED[status]:
            problems.append(
                "status %r is inconsistent with code %d (allowed: %s)"
                % (status, code, ",".join(str(x) for x in sorted(_ALLOWED[status])))
            )
    if code == EXIT_OK and env["errors"]:
        problems.append("code 0 must not carry errors")
    if code != EXIT_OK and not env["errors"]:
        problems.append("non-zero code must carry at least one error code")
    return problems


def dumps(env: Mapping) -> str:
    """Canonical serialization: fixed separators, no key sorting, trailing \\n."""
    return json.dumps(dict(env), ensure_ascii=False, indent=2) + "\n"


def _write(stream, text: str) -> None:
    """Write UTF-8 without depending on the console code page.

    On Windows, ``sys.stdout`` defaults to the ANSI code page (GBK on this
    machine). ``ensure_ascii=False`` plus one replacement character then
    raises ``UnicodeEncodeError`` *after* the judgment succeeded -- a crash
    caused by the reporter, not the verdict. A real byte stream gets UTF-8
    bytes; in-memory streams used by tests keep the text path.
    """
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        stream.write(text)


def emit(
    script: str,
    status: str = "ok",
    code: int = EXIT_OK,
    data: Mapping | None = None,
    errors: Sequence[Mapping] | None = None,
    warnings: Sequence[Any] | None = None,
    degradation: Mapping | None = None,
    artifacts: Mapping | None = None,
    run_id: str = "",
    schema: str = SCHEMA,
    stream=None,
) -> int:
    """Write one envelope to ``stream`` (stdout by default) and return ``code``."""
    env = envelope(script, status, code, data, errors, warnings, degradation,
                   artifacts, run_id, schema)
    out = stream if stream is not None else sys.stdout
    _write(out, dumps(env))
    return code


def fail(
    script: str,
    exit_code: int,
    err_code: str,
    msg: str,
    status: str | None = None,
    **kw,
) -> int:
    """Emit a failure envelope. ``status`` defaults from the exit code."""
    if status is None:
        status = "error" if exit_code >= 2 else "fail"
    errors = kw.pop("errors", None) or [err(err_code, msg)]
    return emit(script, status=status, code=exit_code, errors=errors, **kw)


def run_cli(
    script: str,
    main_fn: Callable[[], int],
    schema: str = SCHEMA,
) -> int:
    """Uniform top-level wrapper: ToolError and exceptions become envelopes.

    A traceback never reaches stdout; it never reaches stderr either, because a
    silent stderr is how a crash gets mistaken for a clean run. The exception
    type and message are carried in ``E-INTERNAL``.
    """
    try:
        return main_fn()
    except ToolError as e:
        return fail(script, e.exit_code, e.code, e.msg, schema=schema)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - explicit internal error, never silent
        return fail(script, EXIT_ERROR, "E-INTERNAL",
                    "internal error: " + type(e).__name__ + ": " + str(e),
                    schema=schema)


# ---- deterministic JSON IO ------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: str | Path, obj: Any) -> None:
    """Write JSON with the same canonical formatting as the envelope."""
    _atomic_write(Path(path), json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def write_text_atomic(path: str | Path, text: str) -> None:
    """Write UTF-8 text with LF newlines and an atomic replace."""
    _atomic_write(Path(path), text)


def read_json(path: str | Path) -> dict:
    """Read a JSON object; a missing or malformed file is a ToolError."""
    p = Path(path)
    if not p.is_file():
        raise ToolError("E-SCHEMA", "input file not found: " + p.name, EXIT_USAGE)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ToolError("E-SCHEMA", "malformed JSON: " + str(e), EXIT_USAGE)
    if not isinstance(obj, dict):
        raise ToolError("E-SCHEMA", "top-level JSON must be an object", EXIT_USAGE)
    return obj
