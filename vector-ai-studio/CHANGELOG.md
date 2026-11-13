# Changelog

Notable changes to this skill, one heading per shipped version. The round
identifier in brackets is the internal release tag used by the project's
test log (round tags `rNN`).

## 1.13.0 — 2026-11-13 [r79]

M5, first slice: the calibration policy becomes executable. `calibration_policy`
had declared a whitelist, a locked list and an approval flow since the
beginning; nothing enforced the whitelist, so a `calibration_log` entry could
name any field -- locked criteria included -- and still pass.

- **New `scripts/thresholds_guard.py`** (stdlib only): a proposal must match a
  `calibratable` glob and no `locked` glob; the loosening direction comes from
  the new `calibration_direction` map (max / min / range), so a mislabelled
  `loosening` is refused; `sample_n` must be a positive integer and
  `real_run_log_ref` a non-empty real file; a loosening also needs a
  `ruling_ref` and external provenance. Two modes: `proposal` (`before` ==
  current) and `entry` (`after` == current).
- **Evidence grading**: external / internal / machine map to A / B / C and are
  REPORTED, never gated -- no calibrated grade-to-truth mapping exists. The
  gate stays structural.
- **`scripts/aggregate_log.py`** now runs every `calibration_log` entry through the
  guard in entry mode; `E-CALIB-MISMATCH`, `E-CALIB-NO-LOG` and
  `E-CALIB-LOOSENING` keep their old names, new refusals use `E-CALIB-LOCKED`
  / `-NOT-WHITELISTED` / `-DIRECTION` / `-STALE` / `-NOOP` / `-SAMPLE` /
  `-PROVENANCE` / `-FIELD`.
- **No threshold value changed**: the additions to `config/thresholds.json`
  are policy metadata (`calibration_direction`, `evidence_provenance`,
  `evidence_grade`, `loosening_requires_external`), not gates.
- **A100** pins 15 cases (locked, not-whitelisted, stale, noop, mislabelled
  direction, mixed interval, tiny sample, missing log, loosening without a
  ruling, loosening without external provenance, both positive paths, the
  machine grade, and the aggregate_log wiring). `EXPECTED_ASSERTIONS`
  99 -> 100; claims 130 -> 133.

## 1.12.0 — 2026-11-13 [r78]

M4 close: reviewer identity. Two submissions are now identified by what they
contain and who claims them, not by the filename someone happened to save.

- **Content-only de-duplication**: the key used to be (absolute path, content
  sha256), so the same ratings copied to a new filename counted as a second
  reviewer. The key is now the content sha256 alone; the new-name copy is
  refused with `E-REVIEW-INVALID` (`16-review-duplicate-new-name` moved from a
  declared boundary to blocked).
- **`reviewer_token`**: each sheet may carry a self-asserted stable pseudonym
  (3-64 chars of A-Z a-z 0-9 . _ -), identical on every row. It is NOT a
  credential -- H4 keeps the pipeline keyless -- but it survives renaming and
  makes one token submitting two different sheets a refusal. `scripts/review.py`
  reports an `identity` block; `evidence.usable_for_claim` now also requires
  every counted sheet to declare a token, all tokens distinct. A legacy sheet
  without a token still aggregates, but is explicitly not claim-grade.
- **A99** pins the lifecycle: distinct tokens -> usable evidence; same token,
  different content -> refused; identical content under a new name -> refused;
  no tokens -> aggregates but `usable_for_claim` false; the pack ships the
  token column.
- **Redteam**: `16` is now blocked; `18-review-two-tokens-one-person` records
  the remaining boundary (one person inventing two tokens is indistinguishable
  from two people). `EXPECTED_ASSERTIONS` 98 -> 99; claims 124 -> 130.

## 1.11.0 — 2026-11-13 [r77]

M4, second slice: pairwise preferences and the Bradley-Terry scale. Absolute
0-2 scores ask a reviewer to judge quality AND calibrate their own scale at
once; asking only "which of these two is better" is the judgement humans are
reliable at.

- **The review pack ships the review-pairs.csv file**: a full round-robin over the
  pack's cases, one row per pair, winner = either case id or `tie`. It is
  produced by `scripts/studio.py --review-pack` next to the absolute sheet.
- **`scripts/review.py` --pairs**: every pair is validated against the pack
  (unknown pair id / swapped pair / invalid winner / unfilled pair are
  `E-REVIEW-INVALID`, the same refusal branch as a bad score sheet), the
  preferences are pooled across reviewers, and `scripts/bradleyterry.py`
  fits the strengths. Report-only, never gated (no calibrated BT threshold).
- **Two boundaries the fit surfaced, documented rather than hidden**: a case
  that never wins has the MLE strength 0 exactly (fixed at 0 and excluded from
  the normalization -- the first real run produced strengths of 17320 and
  climbing before this was handled); with ZERO upsets the likelihood grows as
  the ratios stretch, so the magnitudes are a boundary snapshot and the
  ORDERING is the finding (the result carries `upsets` / `no_upsets`).
- **A97** pins the iteration's properties (strict ordering, tie degeneracy,
  determinism, reversal symmetry, normalization, the zero for never-winners);
  **A98** pins the round trip (pack ships the pairs -> review validates ->
  BT reports; an invalid winner is refused). `EXPECTED_ASSERTIONS` 96 -> 98.
- **Version correction**: 1.10.0 [r76] shipped with the frontmatter still at
  1.9.0 and no CHANGELOG entry -- the r76 package is archived as built and
  NOT rebuilt; the correction is recorded here and in the checksum trail.

## 1.10.0 — 2026-11-13 [r76]

M4, first slice: the review protocol is preregistered and inter-rater
reliability is measured. (Backfilled in r77: this round's package shipped
with the version fields still at 1.9.0; the feature content below is what the
r76 package actually contains.)

- **`scripts/krippendorff.py`**: Krippendorff's alpha (nominal / ordinal /
  interval, any number of reviewers, missing data legal), pinned to the
  published worked examples (0.095 / 0.743 / 0.815 / 0.849, all matched) plus
  unused-level invariance, monotonicity and the unpaired-isolated-value rule.
  REPORTED, NOT GATED -- no calibrated alpha threshold exists.
- **Preregistration**: `scripts/studio.py` --review-pack writes the review protocol
  (dimensions / scale / cases / seed) and its sha256 into the unshipped
  key.json file; `scripts/review.py` verifies it at aggregation, so a protocol edited
  after the reviews came back is `E-REVIEW-INVALID`. Packs without a protocol
  block are warned about, never treated as preregistered.
- **A95 / A96** pin the anchor values and the preregistration lifecycle.
  `EXPECTED_ASSERTIONS` 94 -> 96.

## 1.9.0 — 2026-11-13 [r75]

M3, first slice: the fidelity claims become MEASURED. The builtin fallback's
"faithful" verdict rested on a static sniff -- nobody had ever rendered the
probes twice and compared.

- **New `scripts/fidelity_matrix.py`**: one probe per feature (rect-solid /
  rect-stroked / circle / ellipse / line / path-fill / path-stroke /
  gradient / text / transform / opacity), each rendered by the PRODUCTION
  builtin rasteriser AND by a Pillow reference renderer (a real second
  implementation), compared pixel by pixel with a declared tolerance
  (interior exact for solid shapes; the known 1px bbox overshoot and the
  missing anti-aliasing live in the edge band). The capability matrix is a
  committed measured artifact (`references/fidelity/capability-matrix.json`);
  `--check` re-measures in memory and byte-compares (`E-FIDELITY-DRIFT`).
- **The measurement caught two real classifier blind spots**: a
  `transform` (a rotated rect was drawn unrotated and claimed faithful) and
  `fill-opacity` (a 40% fill was drawn fully opaque). Both now fail closed
  to limited in `render_png._element_fidelity`; `analyze_svg` records
  `transform` and `fill_opacity` per element so the classifier can see them.
- **preview.meta.json gains `fidelity_vector`** (builtin renders only): the
  per-element words with the reason (transform / opacity / fill), additive
  evidence -- the gate still reads the contract word.
- **A94** pins the load-bearing direction: every feature the classifier
  claims FAITHFUL must be measured CONSISTENT; matrix drift is
  `E-FIDELITY-DRIFT`. Pillow is the reference renderer -- without it the
  check is a declared skip (exit 3), never a pass. `EXPECTED_ASSERTIONS`
  93 -> 94.

## 1.8.0 — 2026-11-13 [r74]

M2, second slice: the witness layer -- role separation's missing half. Replay
declared its own hole (the run directory is writable by the producer); the
anchor that closes it lives OUTSIDE the run directory.

- **New `scripts/witness.py`** (keyless by design -- H4's zero-credential
  principle rules out key signing, and the identity this project actually
  has is its repository history): `witness create` appends a record
  (run_id, acceptance/journal sha256, journal tail, criteria_version) to an
  append-only trail that lives outside the run dir and gets COMMITTED and
  PUSHED; `witness verify` re-hashes the run's files and compares against
  the last record for that run_id.
- **replay.py gains check R5** (`--witness <file>`): a certified run whose
  acceptance log changed after certification is red on the spot
  (`E-WITNESS-MISMATCH`); a run_id with no record fails too -- an anchor
  that vouches for everything vouches for nothing.
- **A93** pins the cycle: create -> verify -> tamper (must fail) -> unknown
  run (must fail). `EXPECTED_ASSERTIONS` 92 -> 93.
- The declared boundary, stated in the docs: the anchor is repo history, so
  whoever can force-push that history can forge the anchor; the tag makes
  that visible. Nothing more is claimed.
- Note: the witness trail allows wall-clock (it is an append-only TRAIL like
  sha256.txt -- a record of certifications, not a reproducible artifact).

## 1.7.0 — 2026-11-13 [r73]

M2, first slice: evidence becomes REPLAYABLE. The consumer no longer has to
choose between trusting the shipped log and re-implementing the criteria.

- **Production-time evidence journal (M2)**: `scripts/aggregate_log.py` now
  hashes EVERY file in the run directory into a chain
  (`output/<run-id>/journal.jsonl`) at aggregation time -- including the
  shipped acceptance log itself. No wall-clock in the journal (the same
  input twice yields byte-identical journals; A3's byte-idempotency is
  untouched), the only exclusions are the journal and the run's wall-clock
  log.
- **`scripts/replay.py`** (consumer side): verifies the chain, re-hashes
  every journaled artifact, then re-runs the PUBLIC aggregator
  (`scripts/aggregate_log.py`, one implementation, two roles) on a COPY of
  the run directory and byte-compares both the acceptance log and the
  journal. A forged verdict re-derives to the truth (`grade: 达标 ->
  不达标` in the field diff); an edited score shows up three ways
  (artifact hash, acceptance diff, journal diff). Flags are reconstructed
  from the shipped log (`legacy_grading` / `grading_entered` / `quick` /
  `run_id`), so the replay reproduces the original aggregation exactly.
- **New codes**: `E-JOURNAL-BROKEN` (chain), `E-ARTIFACT-DRIFT` (evidence
  changed after journaling), `E-REPLAY-MISMATCH` (byte-compare), registered
  in `references/error-codes.md`.
- **A91** pins chain + clean replay (the journal must bind the acceptance
  log itself); **A92** pins the forgeries: four mutants (evidence / verdict /
  journal / preview) must each exit 1 with a replay-family code.
  `EXPECTED_ASSERTIONS` 90 -> 92.
- The replay byte-compare immediately caught a real idempotency bug: the
  acceptance log embedded the ABSOLUTE path of the selftest log (the
  hard_gates H3 source field), so the same content at a different location
  produced different bytes. Now recorded relative to the run dir.

## 1.6.1 — 2026-11-13 [r72]

Doc-consistency pass over the 1.6.0 candidate, found by scanning the package
for numbers and wordings that no longer match the code. Still unsigned; the
package was rebuilt rather than released with contradictions inside it.

- **Two contradictions between `references/pipeline-contract.md` and the
  code**, both fixed: the assertion lower bound still said 89 (the code says
  90 since A90 landed), and the L2 wording still said the verdict comes out
  「不达标」 -- which is the pre-2.0.0 two-valued semantics. Since r72, an
  unreadable preview yields 「未判定」, and the contract document said
  otherwise. A contract document that contradicts the code is worse than no
  contract document: consumers will implement the wrong thing.

## 1.6.0 — 2026-11-13 [r72]

The 1.5.0 candidate was never signed off (its engine path could not be
re-tested on the packaging machine), so it is superseded **before** shipping
rather than released and then patched. One addition: the install verifier now
travels with the package, so the check runs where the engine actually is.

- **New `scripts/verify_install.py`** -- the package can verify its own
  install against the engine it will use. It reuses the suite's own
  engine-bound assertions (A30 / A31 / A46 -- no second set of criteria), runs
  `land_vc` and `land_craft` once for artefacts and readable evidence, and
  compares the engine binary's sha256 with the pinned `engine.lock.json`
  (mismatch -> `E-ENGINE-DRIFT`). **It never reports ok for a check it did
  not execute**: an absent engine yields "cannot be verified here" and exit 3,
  not a pass. A90 pins that contract on both kinds of machine; A90 plus the
  new script take `EXPECTED_ASSERTIONS` from 89 to 90.
- **The release process gained a fifth gate** (the release discipline file in the
  repo's docs directory, not shipped in the package): the engine-path
  re-audit must exit 0. Exit 3 means the package is 待 QA 签收 and
  is not sent out. The r72 release is the reason: all four original gates were
  green on a machine with no VectorCraft CLI, so the engine-bound assertions
  were skipped and the pinned engine hash was never re-computed.
- **One implementation, not two**: the release-side re-audit harness now calls
  the package script instead of keeping its own copy of the engine checks, so
  the two cannot drift.
- Docs: `SKILL.md` and `README.md` document the verifier and its exit codes;
  `E-VERIFY-FAILED` is registered in `references/error-codes.md`.

## 1.5.0 — 2026-11-13 [r72]

Criteria 2.0.0: the verdict becomes three-valued and every cross-file enum
turns into a generated artifact. Breaking by design -- see `MIGRATION.md` for
the downstream recipe; do not collapse the third value back into the old
wording, use `--two-valued` instead.

- **M1** Cross-file enums are a GENERATED artifact now: `contract/contract.json`
  is the only place a vocabulary is written, `scripts/gen_contract.py` emits
  `scripts/_contract.py`, `references/schemas/contract-enums.schema.json`,
  `docs/generated/contract-table.md` and `tests/fixtures/contract/contract-matrix.json`
  from it, and `--check` compares bytes in CI (`E-CONTRACT-DRIFT`). This closes
  r71's P0-1 properly: the fidelity vocabulary is ONE list (`faithful` /
  `limited`, plus `unknown` as the fail-closed default value), producers and
  the judge both import it, and the word `full` no longer exists in code.
  A83 pins the chain, including a reverse case (narrowing the accepted set
  must flip a real verdict).
- **4.7** The verdict is THREE-valued: `grade` ∈ 达标 / 不达标 / 未判定
  (`tier`: `pass` / `not-pass` / `indeterminate`). "Could not measure" is no
  longer "measured and failed" -- L2 (capability_missing), unverified F,
  quick mode, incomplete artifacts and missing self-test evidence now land on
  未判定 instead of 不达标 (`E-L2-GRADE`). Two new evidence-derived fields
  join the re-derivation list: `evidence_coverage` (per required dim:
  measured / why / fix, plus the coverage ratio) and `deliverable_status`
  (`deliverable` / `deliverable_with_human_review` / `not_deliverable` /
  `evidence_only`) -- quality and deliverability are separate questions.
  Every full-track log carries `criteria_version` (2.0.0) and
  `thresholds_sha256`; an indeterminate verdict must not claim a clean
  deliverable status (`E-INDETERMINATE-FIELDS`). `--two-valued` maps the
  third value onto the old wording with `legacy_grading: true` and changes
  NOTHING about the pass/fail boundary. A86 pins both modes; MIGRATION.md
  documents the downstream changes.
- **4.1** The outward promises are DATA: `contract/claims.json` registers
  every promise sentence with its text hash, mechanism and test anchor;
  `scripts/validate_claims.py` fails on drift (`E-CLAIMS-DRIFT`) and enforces
  a 1.0 coverage floor over SKILL.md + README.md. 114 claims: 51 pinned by
  assertions, 6 by demonstrated boundaries, 57 registered-unverified (a
  number to push down, not to hide). When a selftest log is supplied, a
  named assertion must have passed in it -- with one exception mirroring the
  H3 policy (A89): a skipped-but-declared-optional capability id
  (`L.CAPABILITY_SKIPS`) is acceptable, a skipped guard or any real failure
  is not.
- **4.2** The attack set ships with the package: `scripts/redteam.py` runs
  the 17 JSON attacks in `tests/attacks/` (fake AI output, truncated PDF,
  tampered verdicts, coverage claims, narrow-track laundering, budget
  backfill, self-attested logs, ...). Current posture: 15 blocked, 0 leaked,
  2 declared boundaries (the two limitations README.md states in prose).
  A82 pins the set; CI runs it.
- **4.6** Degradation notes state their cost: `aggregate_log._loss_list()`
  derives `why` / `lost` / `kept` / `fix_hint` from the trigger itself, so a
  fidelity note can never blame the host LLM and vice versa (A87).
- **4.4** The engine is anchored: `engine.lock.json` pins the CLI binary
  sha256 per platform+version, `trace_ref.find_cli()` walks
  VECTORCRAFT_CLI -> PATH -> known install dirs (no hardcoded dev-machine
  path), and a pinned-but-different build is a hard stop
  (`E-ENGINE-DRIFT`) while an unpinned build is a logged note. A88 pins the
  lock logic; `scripts/detect_deps.py` now reports the pin status and `scripts/land_craft.py`
  runs the same check `scripts/land_vc.py` does.
- **P1** The narrow track cannot launder a graded run: `--landing-only`
  refuses a run directory that already holds any of `output/<run-id>/score.json` /
  `output/<run-id>/judge.json` / `output/<run-id>/selftest.json` /
  `output/<run-id>/batch.json` with `E-EXEMPTION-ABUSE` (A85). The exemption is
  for machines that could not run the full chain, not a way to hide a verdict
  that exists.
- **CI** carries the new gates: `scripts/gen_contract.py --check`,
  `scripts/redteam.py --json`, and `scripts/validate_claims.py --json
  --selftest-json` against the `output/<run-id>/selftest.json` the pure-script
  step produced -- so a hand-edited generated file, a regressed attack or a
  drifted promise is red in CI, not a surprise.
- **Docs**: `MIGRATION.md` (criteria 2.0.0: the three grades, the new fields,
  what happens to old logs, what downstream scripts change);
  `references/pipeline-contract.md` gained the r72 section and lost the stale
  "full is a display enum" wording; the capability-skip list and the
  assertion lower bound are quoted from `lib_common` again.
- Readings (this machine has no VectorCraft CLI, so the three engine
  assertions report as declared capability-skips and selftest is `partial`,
  exit 3, which CI accepts): selftest --fixtures 90 assertions, 87 passed /
  0 failed / 3 skipped; run_fixtures 37/37; redteam 17 attacks / 15 blocked /
  0 leaked / 2 declared boundaries; validate_claims 114/114, coverage 109/109
  = 1.0; gen_contract --check no drift.

## 1.4.1 — 2026-11-12 [r71]

Bug-fix round from the full external audit of r70 (findings F1-F7). No new
features, no threshold changes.

- **F1** `scripts/smoke_ai.py` no longer lies about the install: the self-test inside
  the smoke chain now receives the SAME content-derived `--run-id` as
  `scripts/aggregate_log.py`, so H3's run-id binding is satisfiable; and the smoke
  verdict is derived from the acceptance chain's own exit code and grade
  (`rate_acceptance`) instead of being asserted unconditionally. An internal
  verdict that is not a clean pass now surfaces as `status != ok`.
- **F2** `scripts/studio.py` usage errors (`--review-pack` with fewer than two
  candidates, missing `--spec/--svg/--out`, no args) go through the shared
  `L.run_cli` entry: `E-USAGE` 9-field envelope + exit 2, no traceback.
- **F3** `scripts/schema_check.py` now emits the shared 9-field envelope on every path
  (VALID / INVALID semantics preserved in `data`); an unreadable file or
  malformed JSON is a `code=2` envelope instead of a bare stderr line.
- **F4** `E-REGRESSION` registered in `references/error-codes.md` (warnings-only, exit
  code unchanged); the A28 registry scan now also sees codes carried as
  `"E-X: message"` strings, and a new A80 assertion proves an unregistered
  code is caught while the `E-XXX` placeholder and prose stay out.
- **F5** `scripts/sanitize_svg.py` reports `E-SCHEMA` (not `E-XML`) when the input
  file does not exist, matching `lib_common.read_json` semantics.
- **F7** Documentation drift fixed: the assertion-count lower bound now
  matches `EXPECTED_ASSERTIONS` (81), and the step-10 / command-reference
  `scripts/aggregate_log.py` invocations show the content-derived `--run-id` usage
  that H3 requires.
- Coverage: three new negative fixtures (`bad-sanitize-missing-input`,
  `bad-studio-usage-envelope`, `bad-schema-check-unreadable`) and two new
  selftest assertions (A80, A81); selftest 81/81, `--fixtures` 82/82,
  `run_fixtures` 37/37.

## 1.4.0 — 2026-11-12 [r70]

Judgment-layer hardening round, following the independent deep-research report
(items #1-#8). Field-level fail-closed design with an explicit threat model and
registered boundaries -- not an "unforgeable" claim.

- **#6** All 14 `subprocess.run(..., text=True)` call sites (9 scripts) now pass
  `encoding="utf-8", errors="replace"` explicitly; no silent decode fallback.
- **#1** Fidelity gate decoupled from renderer identity: only an explicit
  `fidelity=="faithful"` plus a readable judge may support A/B scoring. A
  missing `output/<run-id>/preview.meta.json`, `renderer=unknown`, `resvg` with `limited`
  fidelity, or a missing fidelity field all fail closed to L2. `H2.fidelity`
  never records `full`.
- **#2** `lib_pdf.ai_h1_probe()` re-opens `output/<run-id>/final.ai`'s own bytes (`%PDF-`
  header, >=1 `/Type /Page`, image count == structure_readback.raster) and
  AND-combines with the existing H1 gate. Only booleans / small ints are written
  into hard_gates.H1.probe -- no sha256 / dates, preserving byte idempotency.
  Reuses `E-H1-STRUCTURE`; A18 became a paired assertion (a real minimal PDF
  container can reach pass, a 47-byte fake .ai must fail H1).
- **#3** H3 evidence: `L.CAPABILITY_SKIPS` / `L.EXPECTED_ASSERTIONS` are the
  single source in `scripts/lib_common.py`; the whitelist no longer comes from the log
  being validated; `len(assertions) >= L.EXPECTED_ASSERTIONS`; and
  `selftest.json.run_id` binds to the same content-derived `--run-id` the
  orchestration (`scripts/studio.py`, SKILL.md step 11, pipeline-contract.md) passes to
  `scripts/selftest.py` and `scripts/aggregate_log.py`. No nonce / time / signature.
- **#4** `scripts/review.py` de-duplicates `--sheet` by (absolute path, content
  sha256); duplicates raise `E-REVIEW-INVALID` and never count toward the
  reviewer total. Documented boundary: two different files from one person
  cannot be detected as one reviewer.
- **#5** `_derive_grade(evidence, th)` is now the single pure function for all
  verdict fields (grade / tier / headline / status / degraded /
  needs_human_review / reason / internal_excellent), shared by `main()` and the
  `--validate-case` validator. The validator re-derives and compares field by
  field, including `internal_excellent`, rejecting mismatches with the new
  `E-GRADE-MISMATCH`. Six negative validate-case fixtures pin V1/V2/V3/V7/V8/V9.
- **#8** `scripts/run_fixtures.py` switched from a subset check to exact
  `got_codes == want_codes` (warnings can no longer satisfy a wanted code),
  gained `setup_dir` copytree + `@RUN@` to materialize complete run directories,
  and 4 `--run-dir` negative fixtures (fake .ai / deleted meta / 1-assertion
  selftest / raster=0 with an embedded image). Meta-assertions pin >=3 run-dir
  cases and a single grade derivation. `cases_expected`: 24 -> 34.
- **#7** In-package wording: state-A limits on outward pass claims, three
  self-review prompts in the delivery note, C9 re-qualified as engine behavior
  (not a format attribute), C7 PDF/X marked "spec text not verified", WCAG
  2.1 -> "WCAG 2.1 SC 1.4.11 (2.2 keeps the same number and thresholds)" with
  retrieval dates, APCA / `color-contrast()` status corrected. 3:1 / 4.5:1
  thresholds are unchanged. Absolute "unforgeable / anti-self-deception"
  claims are permanently removed.

Readings: selftest 79/79 (with --fixtures 80/80), run_fixtures 34/34,
validate_skill 0 error / 0 warning; all exit 0. Two consecutive plain
selftest runs are identical (79/79/0); same run-dir twice yields a
byte-identical acceptance.json. Meta-assertions A77 (single grade
derivation), A78 (>=3 run-dir cases), A79 (validator recomputes
internal_excellent) guard the paired items.

## 1.3.0 — 2026-11-11 [r69]

- Second audit round, run by independent subagents against the shipped
  package. `scripts/conformance.py` no longer crashes on a `clip-path`
  attribute or on space-separated `points`, and a malformed rules number exits
  2 with `E-SCHEMA` instead of 4 with `E-INTERNAL`.
- Lengths: `%` and `em` are unmeasurable instead of 0, `px`/`pt`/`mm`/`cm`/`in`
  convert to user units, and an unreadable declared `stroke-width` fails
  `STROKE-WIDTHS` instead of being read as the 1.0 default.
- Colours are normalized through `lib_common.normalize_color`, so `#F00` and
  `rgb()` match a hex rule; an undeclared fill counts as `#000000`;
  `fill-opacity="0%"` is zero ink; a `<line>` never contributes a fill colour.
- SVG syntax: comma `viewBox`, `"240px"` root sizes, space-separated `points`
  and a `clip-path` attribute are measured instead of going red or crashing.
- `scripts/selftest.py`: A72-A75 pin the above, A76 allows skips only for
  declared capabilities, and A60 also rejects duplicate assertion ids.
- `scripts/run_fixtures.py`: the declared `cases_expected` makes a truncated
  definition fail with `E-SCHEMA` instead of reporting a clean empty run.
- `scripts/aggregate_log.py`: H3 reads the allowed skip list from the selftest
  log (`capability_skips`) instead of keeping a second, stale copy.
- Examples: the coffee ring is r=77 (inner diameter 144, as the brief states),
  the bookshop spine is 8px (under the 4% cap), and the devtool trend line
  passes through the tops of the four columns and extends past the last one.

## 1.2.0 — 2026-11-11 [r68]

- One inline-style parser: `lib_common.parse_style` is now the only
  implementation of `style=""`; conformance's cascade and the structure reader
  both call it, so `!important` can no longer mean two different things.
- Added this changelog and a CI workflow: on every push and pull request,
  `scripts/selftest.py` runs in both modes plus the fixture regression.
- A71 pins the parser API and its cascade use.

## 1.1.0 — 2026-11-10 [r65–r67]

- [r67] Stroke ink is measured as `perimeter x stroke-width`; a filled and
  stroked shape subtracts the inner half of the band from the fill colour.
  Joins and caps are ignored, ellipses use Ramanujan's approximation, and a
  `path` stays unmeasurable rather than reporting 0.
- [r67] `measured.painted` counts visible shapes; `painted == 0` fails with
  `PAINTED`, so an all-transparent file is no longer all green.
- [r67] The three shipped examples (`examples/01-bookshop`, `02-coffee`,
  `03-devtool`) are checked against their own rules on every selftest run.
- [r66] Only rendered content is measured: `<defs>` and other definitions,
  `display:none` subtrees, `visibility:hidden`, and zero opacity are excluded
  from colours, area and element count. `currentColor` resolves through
  `color`; path area and transformed margins fail as unmeasurable.
- [r65] `<style>` sheets are interpreted over a stated CSS subset (type /
  class / id compounds, descendant and child combinators, comma groups, a
  short declaration list); a sheet outside the subset is refused whole with
  `STYLE-SHEETS` instead of being half applied.
- [r64] Unknown keys in a rules file fail closed with `E-SCHEMA` instead of
  being ignored silently.

## 1.0.0 — 2026-11-07

- First packaged release of the skill: freeze, sanitize, render, judge,
  score, iterate, land and aggregate, with a pure-script selftest.
