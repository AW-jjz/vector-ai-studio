"""Explicit, injectable path context.

The original skill resolves every path from ``Path(__file__).parent.parent``
at import time. That is convenient inside one package and fatal for reuse:
two skills on one PYTHONPATH would disagree about ``ROOT``, and a library
import silently pins the caller's filesystem layout.

Here the root is a value. A consuming skill constructs one Context at its CLI
boundary and passes it down. Nothing in this module reads a global.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class ContextError(ValueError):
    """A configured path is absolute, escapes the root, or is unusable."""


def _clean(rel: str) -> str:
    return str(rel).replace("\\", "/").strip("/")


@dataclass(frozen=True)
class Context:
    """Root-relative layout for one consuming skill.

    Only the envelope schema and the root are required. Every other path has a
    conventional default that a skill may override; a skill that keeps its
    contract somewhere else is not forced to imitate vector-ai-studio.
    """

    root: Path
    schema: str
    config: str = "config/thresholds.json"
    contract: str = "contract/contract.json"
    claims: str = "contract/claims.json"
    attacks: str = "tests/attacks"
    assertion_source: str = "scripts/selftest.py"
    output: str = "output"
    extra: dict = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())
        if not str(self.schema).strip():
            raise ContextError("schema must be a non-empty envelope schema id")

    # -- path resolution -------------------------------------------------
    def resolve(self, rel: str) -> Path:
        """Resolve a root-relative path and refuse to escape the root.

        Registry files are data. A claim entry such as ``../../etc/passwd`` must
        be a hard error, not a read.
        """
        raw = Path(str(rel))
        if raw.is_absolute() or raw.drive:
            raise ContextError("absolute path is not allowed here: " + str(rel))
        candidate = (self.root / raw).resolve()
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise ContextError("path escapes the skill root: " + str(rel))
        return candidate

    def path(self, name: str) -> Path:
        """Return a configured path by attribute name (``context.path("claims")``)."""
        if name not in self.__dataclass_fields__:
            raise ContextError("unknown context path: " + str(name))
        return self.resolve(getattr(self, name))

    # -- convenience -----------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.resolve(self.config)

    @property
    def contract_path(self) -> Path:
        return self.resolve(self.contract)

    @property
    def claims_path(self) -> Path:
        return self.resolve(self.claims)

    @property
    def attacks_path(self) -> Path:
        return self.resolve(self.attacks)

    def with_root(self, root: Path) -> "Context":
        """Same layout, different checkout. Useful for tests and packaging."""
        return Context(
            root=root,
            schema=self.schema,
            config=self.config,
            contract=self.contract,
            claims=self.claims,
            attacks=self.attacks,
            assertion_source=self.assertion_source,
            output=self.output,
            extra=dict(self.extra),
        )

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "schema": self.schema,
            "config": self.config,
            "contract": self.contract,
            "claims": self.claims,
            "attacks": self.attacks,
            "assertion_source": self.assertion_source,
            "output": self.output,
        }
