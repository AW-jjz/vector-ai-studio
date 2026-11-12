# Changelog

Notable changes to this skill, one heading per shipped version. The round
identifier in brackets is the internal release tag used by the project's
test log (round tags `rNN`).

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
