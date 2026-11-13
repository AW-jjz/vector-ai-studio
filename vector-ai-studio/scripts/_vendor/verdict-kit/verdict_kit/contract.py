"""Contract artifacts: one source, generated files, byte-exact drift check.

The generic part is small and worth extracting because every skill that
generates code from a contract gets it subtly wrong at least once:

* the contract is the only source; the generator never invents a value;
* every target is rendered from the same in-memory document;
* ``--check`` compares bytes, not semantics, so a hand edit is red;
* a missing target is drift, not "nothing to compare";
* writes are atomic, so a failed generation cannot leave half a file.

What stays skill-specific: the renderer functions. A skill registers one
``Artifact`` per target (Python module, JSON Schema, Markdown table, fixture
matrix, ...). The kit only moves bytes and reports drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .envelope import EXIT_USAGE, ToolError, write_text_atomic


@dataclass(frozen=True)
class Artifact:
    """One generated file: a root-relative path and a pure renderer."""

    relpath: str
    render: Callable[[Mapping], str]

    def __post_init__(self) -> None:
        if Path(self.relpath).is_absolute() or Path(self.relpath).drive:
            raise ValueError("artifact path must be relative: " + self.relpath)
        if ".." in Path(self.relpath).parts:
            raise ValueError("artifact path must not escape the root: " + self.relpath)


@dataclass
class GenerationReport:
    mode: str
    files: list[dict]
    drift: list[str]

    @property
    def ok(self) -> bool:
        return not self.drift

    def as_dict(self) -> dict:
        return {"mode": self.mode, "ok": self.ok,
                "files": self.files, "drift": list(self.drift)}


def load_spec(path: str | Path, schema: str,
              required: Sequence[str] = ()) -> dict:
    """Load and shape-check a contract document."""
    p = Path(path)
    if not p.is_file():
        raise ToolError("E-CONTRACT-MISSING", "contract not found: " + p.name,
                        EXIT_USAGE)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ToolError("E-CONTRACT-PARSE", "cannot parse contract: " + str(e),
                        EXIT_USAGE)
    if not isinstance(obj, dict):
        raise ToolError("E-CONTRACT-SCHEMA", "contract must be a JSON object",
                        EXIT_USAGE)
    if obj.get("schema") != schema:
        raise ToolError("E-CONTRACT-SCHEMA",
                        "contract schema mismatch: expected " + schema +
                        ", got " + str(obj.get("schema")), EXIT_USAGE)
    missing = [k for k in required if k not in obj]
    if missing:
        raise ToolError("E-CONTRACT-SCHEMA",
                        "contract lacks " + ",".join(missing), EXIT_USAGE)
    return obj


def generate(
    spec: Mapping,
    artifacts: Sequence[Artifact],
    root: str | Path,
    *,
    check: bool = False,
) -> GenerationReport:
    """Render every artifact; write it, or compare it byte-for-byte.

    A renderer must return the exact file text, including its trailing
    newline. No timestamps and no random ordering are allowed: identical
    specs must produce identical bytes.
    """
    base = Path(root).resolve()
    files: list[dict] = []
    drift: list[str] = []
    for art in artifacts:
        if not callable(art.render):
            raise TypeError("artifact renderer is not callable: " + art.relpath)
        target = (base / art.relpath).resolve()
        if target != base and not target.is_relative_to(base):
            raise ValueError("artifact escapes the root: " + art.relpath)
        text = art.render(spec)
        if not isinstance(text, str):
            raise TypeError("artifact renderer must return str: " + art.relpath)
        size = len(text.encode("utf-8"))
        if check:
            current = target.read_text(encoding="utf-8") if target.is_file() else None
            same = current == text
            if not same:
                drift.append(art.relpath)
            files.append({"file": art.relpath, "same": same, "bytes": size})
        else:
            write_text_atomic(target, text)
            files.append({"file": art.relpath, "same": True, "bytes": size})
    return GenerationReport("check" if check else "write", files, drift)
