# verdict-kit (skeleton)

Reusable verdict plumbing for skills that must produce machine-checkable
honesty artifacts. Standard library only; no thresholds, no grading, no SVG,
no engine paths.

This directory started as a **probe** (phase 0) and is now the real dependency:
`verdict_kit.envelope`, `.registry`, `.contract`, `.attacks` and `.fixtures`
are each imported by one CLI of the consuming skill. It is still a vendored
copy, not a released package on an index.

## Run

```bash
python verdict-kit/smoke.py
```

Exit 0 = all checks pass. Exit 1 = at least one check failed. The smoke test
uses a 3-sentence fixture and a 3-claim registry; it does not read the skill's
own contract or selftest. The five fixture checks build their own probe script
and throw-away run directories, so they exercise the real subprocess path.

## Public API

| Module | Public API | Owns |
|---|---|---|
| `verdict_kit.context` | `Context(root, schema, ...)`, `.resolve(rel)` | explicit root, escape-safe relative paths |
| `verdict_kit.envelope` | `envelope()/build_envelope`, `emit`, `err`, `fail`, `run_cli`, `dumps`, `validate`, `EXIT_*`, `ToolError` | deterministic JSON, exit-code policy, CLI error wrapper |
| `verdict_kit.registry` | `ClaimRegistry`, `.validate(...)`, `sentences`, `text_hash`, `scan_assertion_ids` | claim hashes, mechanisms, coverage ratchet |
| `verdict_kit.assertions` | `Harness(expected, capability_skips)`, `.add`, `.finalize` | three-valued assertions, A60-style self-count, unique ids |
| `verdict_kit.contract` | `Artifact`, `load_spec`, `generate(..., check=)` | generated artifacts, byte-exact drift |
| `verdict_kit.attacks` | `apply_mutations`, `dotted_get`, `evaluate(spec, result, too_good=)`, `MUTATION_OPS` | attack op vocabulary + declared-expectation checks |
| `verdict_kit.fixtures` | `expand_args`, `materialize`, `run_case`, `summarize`, `check_declared_count`, `FixtureError` | fixture regression: placeholder expansion, run-dir materialisation, subprocess execution, exact code match, declared-count guard |

`fixtures.FixtureError` carries **no** exit code and **no** `E-*` code: the
caller decides how a broken declaration is reported in its own contract.
A missing script and a duplicate case id are definition errors too -- they
are refused as `FixtureError`, never folded into a case result.

`attacks.evaluate` has **no default pass predicate**. `expect_no_pass` asks
whether an acceptance document claims a pass, which is the consuming skill's
vocabulary, so the caller must inject `too_good(acceptance)`. A spec that sets
`expect_no_pass` while no predicate was injected raises `ValueError` -- the
kit fails loudly instead of guessing a word.

Planned CLI (not in this skeleton):

```
python -m verdict_kit claims   --root . --selftest-json selftest.json
python -m verdict_kit contract --check
python -m verdict_kit attacks  --attack-dir tests/attacks
python -m verdict_kit gate     --config gate.json
```

## Consuming-skill contract (minimal)

A skill must provide:

1. **Envelope schema id** — e.g. `Context(root=..., schema="my-skill/v1")`.
2. **Root-relative layout** — defaults match `contract/`, `tests/attacks/`,
   `scripts/selftest.py`; override on `Context`.
3. **Assertion policy** — `expected` count and `capability_skips`; these stay
   in the skill because only the skill knows which capabilities are optional.
4. **Claim policy** — `scan_files`, `mandatory_words`, `coverage_floor`,
   assertion ids, attack map, and (optionally) a selftest result map.
5. **Contract renderers** — one pure `Artifact(relpath, render)` per generated
   file. The kit never invents enum values.
6. **Attack base run + pass predicate** — how to materialize an honest run, and
   the `too_good(acceptance)` callable that says what counts as a pass in that
   skill's vocabulary (`达标` / `pass` / ...). Both are injected into
   `verdict_kit.attacks`; the kit applies the mutations and checks the declared
   expectations, it never decides what a pass is on its own.
7. **Gate step list** — commands, expectations, and which steps are
   engine-bound.

## What is deliberately absent

- `load_thresholds`, grading, L0–L5, SVG/PDF parsing, engine locks.
- Any import-time global `ROOT`; a missing `Context` is a programming error.
- Wall-clock timestamps in judgment envelopes.
