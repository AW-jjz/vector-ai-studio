"""verdict-kit: reusable verdict plumbing for skills that must not lie.

The package is deliberately dependency-free (standard library only) and
contains no skill-specific vocabulary, thresholds, paths or fixtures.

Public surface (see README.md for the consuming-skill contract):

    verdict_kit.Context          explicit root + relative paths, no globals
    verdict_kit.envelope         deterministic JSON envelope + exit codes
    verdict_kit.registry         outward-claim registry (hashes + coverage)
    verdict_kit.assertions       three-valued harness with a self-count guard
    verdict_kit.contract         generated artifacts + byte-exact drift check
    verdict_kit.attacks          mutation ops + declared-expectation checks
    verdict_kit.fixtures         fixture regression: expand, materialise, match

Deliberately NOT here: thresholds, grading, SVG/PDF analysis, engine locks.
Those belong to the skill that owns the judgment.
"""

from .assertions import Assertion, Harness, HarnessReport
from .attacks import (
    MUTATION_OPS,
    UNKNOWN_OP_CODE,
    apply_mutations,
    dotted_get,
    evaluate,
)
from .context import Context, ContextError
from .contract import Artifact, GenerationReport, generate, load_spec
from .envelope import (
    EXIT_CODES,
    EXIT_ERROR,
    EXIT_FAIL,
    EXIT_MISSING,
    EXIT_OK,
    EXIT_USAGE,
    SCHEMA,
    STATUSES,
    EnvelopeError,
    ToolError,
    build_envelope,
    dumps,
    emit,
    err,
    fail,
    read_json,
    run_cli,
    validate,
    write_json_atomic,
    write_text_atomic,
)
from .fixtures import (
    DEFAULT_TIMEOUT,
    FixtureError,
    check_declared_count,
    expand_args,
    materialize,
    run_case,
    summarize,
)
from .registry import (
    ClaimRegistry,
    ClaimRegistryError,
    ClaimResult,
    Coverage,
    Report,
    normalize,
    scan_assertion_ids,
    sentences,
    text_hash,
)

__all__ = [
    "Context",
    "ContextError",
    "SCHEMA",
    "STATUSES",
    "EXIT_OK",
    "EXIT_FAIL",
    "EXIT_USAGE",
    "EXIT_MISSING",
    "EXIT_ERROR",
    "EXIT_CODES",
    "ToolError",
    "EnvelopeError",
    "build_envelope",
    "emit",
    "err",
    "fail",
    "run_cli",
    "dumps",
    "validate",
    "read_json",
    "write_json_atomic",
    "write_text_atomic",
    "ClaimRegistry",
    "ClaimRegistryError",
    "ClaimResult",
    "Coverage",
    "Report",
    "normalize",
    "sentences",
    "text_hash",
    "scan_assertion_ids",
    "Assertion",
    "Harness",
    "HarnessReport",
    "Artifact",
    "GenerationReport",
    "generate",
    "load_spec",
    "apply_mutations",
    "dotted_get",
    "evaluate",
    "MUTATION_OPS",
    "UNKNOWN_OP_CODE",
    "FixtureError",
    "DEFAULT_TIMEOUT",
    "expand_args",
    "materialize",
    "run_case",
    "summarize",
    "check_declared_count",
]
