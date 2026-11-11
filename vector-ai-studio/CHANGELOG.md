# Changelog

Notable changes to this skill, one heading per shipped version. The round
identifier in brackets is the internal release tag used by the project's
test log (round tags `rNN`).

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
