#!/usr/bin/env python3
"""pytest-free smoke test for the verdict-kit skeleton.

Run from any directory:

    python <skill>/verdict-kit/smoke.py

Exercises the seams that matter before a full extraction:

  * envelope determinism, exit-code policy, and the CLI error wrapper;
  * claim hashing, mechanism checks, capability skips, and the coverage
    ratchet -- against a tiny fixture, not against vector-ai-studio;
  * contract rendering, byte drift and atomic writes;
  * attack mutations and declared expectations;
  * fixture expansion, materialisation, exact code matching and the
    declared-count guard.

Exit 0 = every check passed. Exit 1 = at least one check failed.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verdict_kit as vk  # noqa: E402
import verdict_kit.assertions as A  # noqa: E402
import verdict_kit.attacks as T  # noqa: E402
import verdict_kit.contract as K  # noqa: E402
import verdict_kit.envelope as E  # noqa: E402
import verdict_kit.fixtures as F  # noqa: E402
import verdict_kit.registry as R  # noqa: E402

FIX = HERE / "fixtures"
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def expect(name: str, fn) -> None:
    try:
        fn()
        check(name, True, "")
    except AssertionError as e:
        check(name, False, str(e)[:200])
    except Exception as e:  # noqa: BLE001 - a smoke failure must be reported, not raised
        check(name, False, type(e).__name__ + ": " + str(e)[:180])


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------

def test_envelope_shape() -> None:
    env = E.envelope("demo.py", status="ok", code=0, data={"n": 1},
                     run_id="r1", schema="demo/v1")
    assert list(env) == ["schema", "script", "run_id", "status", "code",
                         "data", "errors", "warnings", "artifacts"], list(env)
    assert env["schema"] == "demo/v1"
    assert env["code"] == 0 and env["status"] == "ok"
    assert env["errors"] == [] and env["warnings"] == []
    assert "degradation" not in env


def test_envelope_deterministic() -> None:
    a = E.envelope("demo.py", status="degraded", code=3,
                   data={"renderer": "builtin"}, run_id="r2",
                   degradation={"level": "L2", "reason": "no engine"})
    b = E.envelope("demo.py", status="degraded", code=3,
                   data={"renderer": "builtin"}, run_id="r2",
                   degradation={"level": "L2", "reason": "no engine"})
    assert E.dumps(a) == E.dumps(b)
    assert E.dumps(a).endswith("}\n")
    assert list(a)[-1] == "degradation"


def test_emit_stream() -> None:
    buf = io.StringIO()
    rc = E.emit("demo.py", code=0, data={"x": 1}, stream=buf)
    text = buf.getvalue()
    assert rc == 0
    assert text.endswith("\n")
    obj = json.loads(text)
    assert obj["code"] == 0 and obj["data"] == {"x": 1}


def test_policy_validation_is_separate() -> None:
    # The skeleton must be able to describe a legacy envelope without
    # rejecting it; new code calls validate() and refuses to ship it.
    env = E.envelope("legacy.py", status="ok", code=1,
                     errors=[E.err("E-LEGACY", "business failure")])
    problems = E.validate(env)
    assert any("inconsistent" in p for p in problems), problems
    assert E.validate(env, policy=False) == []


def test_toolerror_mapping() -> None:
    def main() -> int:
        raise E.ToolError("E-DEMO-USAGE", "bad flag", E.EXIT_USAGE)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = E.run_cli("demo.py", main, schema="demo/v1")
    obj = json.loads(buf.getvalue())
    assert rc == 2 and obj["code"] == 2 and obj["status"] == "error"
    assert obj["errors"][0]["code"] == "E-DEMO-USAGE"
    assert obj["schema"] == "demo/v1"


def test_internal_error_mapping() -> None:
    def main() -> int:
        raise ValueError("boom")

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = E.run_cli("demo.py", main)
    obj = json.loads(buf.getvalue())
    assert rc == 4 and obj["code"] == 4
    assert obj["errors"][0]["code"] == "E-INTERNAL"
    assert "ValueError" in obj["errors"][0]["msg"]


def test_bad_error_code_rejected() -> None:
    try:
        E.err("NOT-A-CODE", "x")
    except E.EnvelopeError:
        return
    raise AssertionError("err() accepted a code without the E- prefix")


def test_unknown_status_rejected() -> None:
    try:
        E.envelope("demo.py", status="maybe")
    except E.EnvelopeError:
        return
    raise AssertionError("envelope() accepted an unknown status")


def test_missing_status_accepted() -> None:
    # The source skill emits status="missing" + exit 3 when every renderer is
    # absent. The kit must accept that shape without a behavior change.
    env = E.envelope("render.py", status="missing", code=3,
                     errors=[E.err("E-RENDERER-MISSING", "all renderers missing")])
    assert E.validate(env) == [], E.validate(env)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

def test_context_paths() -> None:
    ctx = vk.Context(root=HERE, schema="demo/v1")
    assert ctx.resolve("fixtures/doc.md") == (HERE / "fixtures" / "doc.md").resolve()
    assert ctx.claims_path.name == "claims.json"


def test_context_refuses_escape() -> None:
    ctx = vk.Context(root=HERE, schema="demo/v1")
    for bad in ("../outside.md", str(HERE / "fixtures" / "doc.md")):
        try:
            ctx.resolve(bad)
        except vk.ContextError:
            continue
        raise AssertionError("ctx.resolve accepted an escaping path: " + bad)


# ---------------------------------------------------------------------------
# claim registry
# ---------------------------------------------------------------------------

def _validate(registry_name: str, **overrides):
    reg = R.ClaimRegistry.load(FIX / registry_name)
    kwargs = dict(
        root=HERE,
        assertion_ids={"A-DEMO-1"},
        attacks={"demo-leak": {"id": "demo-leak", "expect": "leak"}},
        capability_skips=(),
        selftest_results={"A-DEMO-1": True},
        scan_files=("fixtures/doc.md",),
        mandatory_words=("必须", "只", "不许"),
        coverage_floor=1.0,
    )
    kwargs.update(overrides)
    return reg.validate(**kwargs)


def test_registry_full_coverage() -> None:
    rep = _validate("claims.json")
    assert rep.healthy, rep.problems
    assert rep.claims == 3 and rep.ok == 3
    # The fenced block contains 必须; if fences were not removed this would be 4.
    assert rep.coverage.mandatory_sentences == 3, rep.coverage
    assert rep.coverage.registered == 3
    assert rep.coverage.ratio == 1.0
    assert rep.by_mechanism == {"assertion": 1, "boundary-declaration": 1,
                                "unverified": 1}


def test_registry_partial_coverage_fails() -> None:
    rep = _validate("claims-partial.json")
    assert not rep.healthy
    assert rep.coverage.mandatory_sentences == 3
    assert rep.coverage.registered == 2
    assert abs(rep.coverage.ratio - 2 / 3) < 1e-9
    assert any("coverage" in p for p in rep.problems), rep.problems


def test_registry_text_drift() -> None:
    rep = _validate("claims-tampered.json")
    assert not rep.healthy
    assert any("text changed" in p for p in rep.problems), rep.problems


def test_registry_skip_needs_declaration() -> None:
    rep = _validate("claims.json", selftest_results={"A-DEMO-1": None})
    assert not rep.healthy
    assert any("skipped" in p for p in rep.problems), rep.problems

    rep_ok = _validate("claims.json", selftest_results={"A-DEMO-1": None},
                       capability_skips={"A-DEMO-1"})
    assert rep_ok.healthy, rep_ok.problems
    c1 = next(r for r in rep_ok.results if r.id == "C1")
    assert c1.skipped_optional == ["A-DEMO-1"]


def test_registry_blocked_attack_cannot_prove_boundary() -> None:
    rep = _validate("claims.json",
                    attacks={"demo-leak": {"id": "demo-leak", "expect": "block"}})
    assert not rep.healthy
    assert any("cannot prove a boundary" in p for p in rep.problems), rep.problems


def test_registry_rejects_path_escape() -> None:
    doc = {
        "schema": R.DEFAULT_SCHEMA,
        "claims": [{"id": "X", "file": "../outside.md", "anchor": "x",
                    "text_hash": "sha256:" + "0" * 64, "mechanism": "unverified",
                    "note": "x"}],
    }
    reg = R.ClaimRegistry(doc)
    try:
        reg.validate(HERE, assertion_ids=set())
    except R.ClaimRegistryError:
        return
    raise AssertionError("registry read a file outside the root")


def test_assertion_id_scan() -> None:
    ids = R.scan_assertion_ids('add("A-ONE", True)\nadd(\'A-TWO\', False)')
    assert ids == {"A-ONE", "A-TWO"}, ids


def test_report_is_deterministic() -> None:
    a = R.ClaimRegistry.load(FIX / "claims.json")
    b = R.ClaimRegistry.load(FIX / "claims.json")
    ka = dict(root=HERE, assertion_ids={"A-DEMO-1"},
              attacks={"demo-leak": {"expect": "leak"}},
              selftest_results={"A-DEMO-1": True},
              scan_files=("fixtures/doc.md",),
              mandatory_words=("必须", "只", "不许"))
    assert json.dumps(a.validate(**ka).as_dict(), ensure_ascii=False) == \
        json.dumps(b.validate(**ka).as_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# assertion harness
# ---------------------------------------------------------------------------

def test_harness_counts_itself() -> None:
    h = A.Harness(expected=3)
    h.add("A-ONE", True)
    rep = h.finalize()
    assert rep.status == "ok", rep.as_dict()
    assert rep.passed == 3 and not rep.failed
    assert [a.id for a in rep.assertions][-1] == "A60-assertion-count-declared"


def test_harness_stale_count_fails() -> None:
    h = A.Harness(expected=4)
    h.add("A-ONE", True)
    rep = h.finalize()
    assert rep.status == "fail"
    assert "A60-assertion-count-declared" in rep.failed


def test_harness_duplicate_id_fails() -> None:
    h = A.Harness(expected=3)
    h.add("A-ONE", True)
    h.add("A-ONE", True)
    rep = h.finalize()
    assert rep.status == "fail"
    assert any("duplicated" in a.detail for a in rep.assertions)


def test_harness_skip_is_partial_and_declared() -> None:
    h = A.Harness(expected=3)
    h.add("A-OPT", None, skipped=True)
    rep = h.finalize()
    assert rep.status == "fail" and rep.exit_code == 1
    assert any("undeclared" in p for p in rep.problems)

    h2 = A.Harness(expected=3, capability_skips={"A-OPT"})
    h2.add("A-OPT", None, skipped=True)
    rep2 = h2.finalize()
    assert rep2.status == "partial" and rep2.exit_code == 3
    assert rep2.ok is False and rep2.acceptable is True
    assert rep2.skipped == ["A-OPT"]


# ---------------------------------------------------------------------------
# contract artifacts
# ---------------------------------------------------------------------------

def test_contract_generation_and_drift() -> None:
    with tempfile.TemporaryDirectory(prefix="verdict-kit-smoke-") as td:
        root = Path(td)
        spec = {"n": 7}
        arts = [K.Artifact("gen/a.txt", lambda s: "A=" + str(s["n"]) + "\n")]

        first = K.generate(spec, arts, root, check=False)
        assert first.ok and first.mode == "write"
        assert (root / "gen" / "a.txt").read_text(encoding="utf-8") == "A=7\n"

        clean = K.generate(spec, arts, root, check=True)
        assert clean.ok and not clean.drift

        (root / "gen" / "a.txt").write_text("A=8\n", encoding="utf-8")
        drifted = K.generate(spec, arts, root, check=True)
        assert not drifted.ok and drifted.drift == ["gen/a.txt"]

        K.generate(spec, arts, root, check=False)
        assert K.generate(spec, arts, root, check=True).ok

        missing = K.generate(spec, [K.Artifact("gen/b.txt", lambda s: "B\n")],
                             root, check=True)
        assert not missing.ok and missing.drift == ["gen/b.txt"]


def test_contract_rejects_escape() -> None:
    try:
        K.Artifact("../outside.txt", lambda s: "x\n")
    except ValueError:
        return
    raise AssertionError("Artifact accepted a path outside the root")


# ---------------------------------------------------------------------------
# attack mutations / expectations
# ---------------------------------------------------------------------------

def test_attack_mutation_ops() -> None:
    with tempfile.TemporaryDirectory(prefix="verdict-kit-smoke-") as td:
        run = Path(td)
        (run / "keep.txt").write_text("keep\n", encoding="utf-8")
        (run / "gone.txt").write_text("gone\n", encoding="utf-8")
        (run / "deep" / "nested").mkdir(parents=True)
        (run / "deep" / "nested" / "doc.json").write_text(
            '{"a": {"b": 1}, "top": 0}\n', encoding="utf-8")

        T.apply_mutations(run, [
            {"op": "delete", "path": "gone.txt"},
            {"op": "write_text", "path": "sub/new.txt", "text": "hi\n"},
            {"op": "write_bytes_b64", "path": "bin/raw.bin", "b64": "AAEC"},
            {"op": "write_json", "path": "fresh/obj.json", "doc": {"k": "值"}},
            {"op": "patch_json", "path": "deep/nested/doc.json",
             "json_path": "a.b", "value": 7},
            {"op": "patch_json", "path": "deep/nested/doc.json",
             "key": "top", "value": 9},
            {"op": "copy", "from": "keep.txt", "to": "copied.txt"},
        ])

        assert not (run / "gone.txt").exists()
        assert (run / "keep.txt").read_text(encoding="utf-8") == "keep\n"
        assert (run / "sub" / "new.txt").read_text(encoding="utf-8") == "hi\n"
        assert (run / "bin" / "raw.bin").read_bytes() == b"\x00\x01\x02"
        # canonical JSON: indent 2, non-ASCII kept, trailing newline
        assert (run / "fresh" / "obj.json").read_text(encoding="utf-8") == \
            '{\n  "k": "值"\n}\n'
        patched = json.loads((run / "deep" / "nested" / "doc.json")
                            .read_text(encoding="utf-8"))
        # json_path is a path INSIDE the file; a bare key is top-level.
        assert patched == {"a": {"b": 7}, "top": 9}, patched
        assert (run / "copied.txt").read_text(encoding="utf-8") == "keep\n"

        # An unknown op is a usage error, never a silent no-op.
        try:
            T.apply_mutations(run, [{"op": "chmod", "path": "keep.txt"}])
        except T.ToolError as e:
            assert e.code == T.UNKNOWN_OP_CODE and e.exit_code == E.EXIT_USAGE, e
        else:
            raise AssertionError("unknown op was silently ignored")


def test_attack_expectations() -> None:
    acceptance = {"grade": "未达标", "a": {"b": 2}}
    result = {"exit": 0, "codes": ["E-Y"], "acceptance": acceptance}

    assert T.evaluate({"id": "clean"}, {"exit": 0, "codes": [],
                                        "acceptance": {}}) == []

    # expect_exit / expect_codes / reject_codes, in the shipped order.
    assert T.evaluate({"id": "x", "expect_exit": [1, 2]}, result) == [
        "exit code is 0, expected one of 1,2"]
    assert T.evaluate({"id": "x", "expect_codes": ["E-X"]}, result) == [
        "expected code E-X not emitted (got E-Y)"]
    assert T.evaluate({"id": "x", "reject_codes": ["E-Y"]}, result) == [
        "unexpected code E-Y"]

    # expect_no_pass: there is NO default predicate. Omitting it is a
    # programming error, and the message names the parameter that is missing.
    passing = {"exit": 0, "codes": [], "acceptance": {"grade": "达标"}}
    for spec in ({"id": "x", "expect_no_pass": True},
                 {"id": "x", "expect_no_pass": True, "expect_codes": []}):
        try:
            T.evaluate(spec, passing)
        except ValueError as e:
            assert "too_good" in str(e), e
        else:
            raise AssertionError(
                "expect_no_pass without too_good did not raise")
    # ... and a spec that does not ask for it never demands the predicate.
    assert T.evaluate({"id": "x"}, passing) == []
    # Injecting one makes the check real, in both directions.
    assert T.evaluate({"id": "x", "expect_no_pass": True}, result,
                      too_good=lambda a: True) == [
        "the attack still claims a pass (grade=未达标)"]
    assert T.evaluate({"id": "x", "expect_no_pass": True}, passing,
                      too_good=lambda a: False) == []
    assert T.evaluate({"id": "x", "expect_no_pass": True}, passing,
                      too_good=lambda a: a.get("grade") == "达标") == [
        "the attack still claims a pass (grade=达标)"]

    # expect_grade / expect_field.
    assert T.evaluate({"id": "x", "expect_grade": "达标"}, passing) == []
    assert T.evaluate({"id": "x", "expect_grade": "达标"}, result) == [
        "grade is '未达标', expected '达标'"]
    assert T.evaluate({"id": "x", "expect_field": {"a.b": 3}}, result) == [
        "a.b is 2, expected 3"]


def test_attack_dotted_and_write_json_seam() -> None:
    assert T.dotted_get({"a": {"b": {"c": 1}}}, "a.b.c") == 1
    assert T.dotted_get({"a": [1, 2]}, "a.0") is None      # list is not a dict
    assert T.dotted_get({"a": {"b": 1}}, "a.x") is None
    assert T.dotted_get({"a": 1}, "a.b") is None

    # write_json is injectable: a skill keeps its own byte format.
    seen = []
    with tempfile.TemporaryDirectory(prefix="verdict-kit-smoke-") as td:
        run = Path(td)
        T.apply_mutations(run, [{"op": "write_json", "path": "x.json",
                                 "doc": {"n": 1}}],
                          write_json=lambda p, o: seen.append((Path(p).name, o)))
        assert seen == [("x.json", {"n": 1})], seen
        assert not (run / "x.json").exists()


# ---------------------------------------------------------------------------
# fixture regression
# ---------------------------------------------------------------------------

_PROBE = '''import json, sys
codes = [c for c in (sys.argv[1].split(",") if len(sys.argv) > 1 else []) if c]
print(json.dumps({"errors": [{"code": c} for c in codes],
                  "warnings": ["E-WARN: a warning is not an error"]}))
sys.exit(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
'''


def _probe_tree(root: Path) -> Path:
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "probe.py").write_text(_PROBE, encoding="utf-8")
    return scripts


def test_fixture_expand_args() -> None:
    fd, od = Path("/fx"), Path("/out")
    rd = od / "case-1"
    got = F.expand_args(["@F@/a.json", "--out=@OUT@", "@RUN@/acceptance.json",
                         "plain", 7],
                        fixtures_dir=fd, out_dir=od, run_dir=rd)
    # Expansion is textual: the literal "/" after a placeholder survives, so
    # the expected value is built the same way rather than via Path joining.
    assert got == [str(fd) + "/a.json", "--out=" + str(od),
                   str(rd) + "/acceptance.json", "plain", "7"], got
    assert F.expand_args([], fixtures_dir=fd, out_dir=od, run_dir=rd) == []


def test_fixture_materialize() -> None:
    with tempfile.TemporaryDirectory(prefix="verdict-kit-smoke-") as td:
        root = Path(td)
        fx = root / "fx"
        (fx / "setup" / "deep").mkdir(parents=True)
        (fx / "setup" / "acceptance.json").write_text(
            '{"grade": "达标"}\n', encoding="utf-8")
        (fx / "setup" / "deep" / "log.json").write_text("{}\n", encoding="utf-8")

        run = root / "out" / "case-1"
        # No setup_dir -> no directory invented.
        assert F.materialize({"id": "case-1"}, fixtures_dir=fx, run_dir=run) is None
        assert not run.exists()

        F.materialize({"id": "case-1", "setup_dir": "setup"},
                      fixtures_dir=fx, run_dir=run)
        assert json.loads((run / "acceptance.json").read_text(encoding="utf-8")) == \
            {"grade": "达标"}
        assert (run / "deep" / "log.json").is_file()

        # A declared-but-absent source is an error, not an empty directory.
        try:
            F.materialize({"id": "case-2", "setup_dir": "absent"},
                          fixtures_dir=fx, run_dir=root / "out" / "case-2")
        except F.FixtureError as e:
            assert "case-2" in str(e), e
        else:
            raise AssertionError("materialize accepted a missing setup_dir")


def test_fixture_exact_code_match() -> None:
    with tempfile.TemporaryDirectory(prefix="verdict-kit-smoke-") as td:
        root = Path(td)
        scripts = _probe_tree(root)
        out = root / "out"
        out.mkdir()

        def run(cid, args, **declared):
            case = {"id": cid, "script": "probe.py", "args": args}
            case.update(declared)
            return F.run_case(case, scripts_dir=scripts, fixtures_dir=root,
                              out_dir=out)

        # Deduplicated and sorted on both sides: declaration order is free.
        exact = run("exact", ["E-B,E-A,E-A", "1"], want_exit=[1],
                    want_codes=["E-A", "E-B"])
        assert exact["ok"] is True, exact
        assert exact["got_codes"] == ["E-A", "E-B"] and exact["want_codes"] == \
            ["E-A", "E-B"]
        assert exact["want_exit"] == [1] and exact["got_exit"] == 1

        # One extra emitted code fails...
        assert run("extra", ["E-A,E-B,E-C", "1"], want_exit=[1],
                   want_codes=["E-A", "E-B"])["ok"] is False
        # ... one missing code fails ...
        assert run("missing", ["E-A", "1"], want_exit=[1],
                   want_codes=["E-A", "E-B"])["ok"] is False
        # ... and a warning whose text starts with E- can never satisfy one.
        warn = run("warning", ["E-A", "1"], want_exit=[1],
                   want_codes=["E-A", "E-WARN"])
        assert warn["ok"] is False and warn["got_codes"] == ["E-A"], warn
        # The exit code is part of the match too.
        assert run("exit", ["E-A", "0"], want_exit=[1],
                   want_codes=["E-A"])["ok"] is False


def test_fixture_acceptance_mismatch() -> None:
    with tempfile.TemporaryDirectory(prefix="verdict-kit-smoke-") as td:
        root = Path(td)
        scripts = _probe_tree(root)
        setup = root / "setup"
        setup.mkdir()
        (setup / "acceptance.json").write_text(
            '{"grade": "未达标", "gates": {"H1": {"pass": false}}}\n',
            encoding="utf-8")
        case = {"id": "acc", "script": "probe.py", "args": [],
                "setup_dir": "setup", "want_exit": [0], "want_codes": [],
                "want_acceptance": {"grade": "达标", "gates.H1.pass": False}}

        # Exit code and codes agree; only the acceptance field does not.
        bad = F.run_case(case, scripts_dir=scripts, fixtures_dir=root,
                         out_dir=root / "out-1")
        assert bad["ok"] is False, bad
        assert bad["got_acceptance"] == {"grade": "未达标",
                                         "gates.H1.pass": False}, bad

        case["want_acceptance"] = {"grade": "未达标", "gates.H1.pass": False}
        good = F.run_case(case, scripts_dir=scripts, fixtures_dir=root,
                          out_dir=root / "out-2")
        assert good["ok"] is True, good


def test_fixture_summary_and_count_guard() -> None:
    cases = [{"id": "a", "ok": True}, {"id": "b", "ok": True},
             {"id": "c", "ok": False}]
    s = F.summarize(cases)
    assert list(s) == ["total", "passed", "failed", "all_ok", "cases"], list(s)
    assert s["total"] == 3 and s["passed"] == 2 and s["failed"] == 1
    assert s["all_ok"] is False and s["cases"] == cases
    assert F.summarize([])["all_ok"] is True

    assert F.check_declared_count(cases, 3) == 3
    # A shrink, a bool (an int in Python) and a non-int are all refusals.
    for bad in (2, 4, True, None, "3", 3.0):
        try:
            F.check_declared_count(cases, bad)
        except F.FixtureError:
            continue
        raise AssertionError("check_declared_count accepted %r" % (bad,))


def test_fixture_missing_script() -> None:
    """A case naming a script that does not exist is a broken definition.

    Left to the interpreter it would exit 2 with empty stdout -- the exact
    reading of a case that declares ``want_exit: [2], want_codes: []``, which
    would then pass without its script ever running.
    """
    with tempfile.TemporaryDirectory(prefix="verdict-kit-smoke-") as td:
        root = Path(td)
        scripts = _probe_tree(root)
        out = root / "out"
        out.mkdir()
        case = {"id": "gone", "script": "absent.py", "args": [],
                "want_exit": [2], "want_codes": []}
        try:
            F.run_case(case, scripts_dir=scripts, fixtures_dir=root, out_dir=out)
        except F.FixtureError as e:
            assert "gone" in str(e) and "absent.py" in str(e), e
        else:
            raise AssertionError("run_case accepted a missing script")


def test_fixture_duplicate_id() -> None:
    """Two cases sharing an id + setup_dir is a definition error, not a crash.

    The second ``copytree`` lands on the first case's run directory; uncaught
    ``FileExistsError`` would surface as an internal error upstream.
    """
    with tempfile.TemporaryDirectory(prefix="verdict-kit-smoke-") as td:
        root = Path(td)
        scripts = _probe_tree(root)
        fx = root / "fx"
        (fx / "setup").mkdir(parents=True)
        (fx / "setup" / "acceptance.json").write_text("{}\n", encoding="utf-8")
        out = root / "out"
        out.mkdir()
        case = {"id": "same", "script": "probe.py", "args": [],
                "setup_dir": "setup", "want_exit": [0], "want_codes": []}
        first = F.run_case(case, scripts_dir=scripts, fixtures_dir=fx, out_dir=out)
        assert first["ok"] is True, first
        try:
            F.run_case(case, scripts_dir=scripts, fixtures_dir=fx, out_dir=out)
        except F.FixtureError as e:
            assert "same" in str(e), e
        else:
            raise AssertionError("run_case accepted a duplicate case id")


TESTS = [
    ("envelope.shape", test_envelope_shape),
    ("envelope.deterministic", test_envelope_deterministic),
    ("envelope.emit-stream", test_emit_stream),
    ("envelope.policy-separate", test_policy_validation_is_separate),
    ("envelope.toolerror", test_toolerror_mapping),
    ("envelope.internal-error", test_internal_error_mapping),
    ("envelope.bad-code", test_bad_error_code_rejected),
    ("envelope.bad-status", test_unknown_status_rejected),
    ("envelope.missing-status", test_missing_status_accepted),
    ("context.paths", test_context_paths),
    ("context.no-escape", test_context_refuses_escape),
    ("registry.full-coverage", test_registry_full_coverage),
    ("registry.partial-coverage", test_registry_partial_coverage_fails),
    ("registry.text-drift", test_registry_text_drift),
    ("registry.capability-skip", test_registry_skip_needs_declaration),
    ("registry.boundary-proof", test_registry_blocked_attack_cannot_prove_boundary),
    ("registry.no-path-escape", test_registry_rejects_path_escape),
    ("registry.assertion-scan", test_assertion_id_scan),
    ("registry.deterministic", test_report_is_deterministic),
    ("assertions.count-guard", test_harness_counts_itself),
    ("assertions.stale-count", test_harness_stale_count_fails),
    ("assertions.duplicate-id", test_harness_duplicate_id_fails),
    ("assertions.skip-policy", test_harness_skip_is_partial_and_declared),
    ("contract.drift", test_contract_generation_and_drift),
    ("contract.no-escape", test_contract_rejects_escape),
    ("attacks.mutation-ops", test_attack_mutation_ops),
    ("attacks.expectations", test_attack_expectations),
    ("attacks.dotted-seam", test_attack_dotted_and_write_json_seam),
    ("fixtures.expand-args", test_fixture_expand_args),
    ("fixtures.materialize", test_fixture_materialize),
    ("fixtures.exact-codes", test_fixture_exact_code_match),
    ("fixtures.acceptance", test_fixture_acceptance_mismatch),
    ("fixtures.summary-guard", test_fixture_summary_and_count_guard),
    ("fixtures.missing-script", test_fixture_missing_script),
    ("fixtures.duplicate-id", test_fixture_duplicate_id),
]


def main() -> int:
    for name, fn in TESTS:
        expect(name, fn)

    failed = [c for c in CHECKS if not c[1]]
    for name, ok, detail in CHECKS:
        print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    summary = {
        "schema": "verdict-kit/smoke-v1",
        "checks": len(CHECKS),
        "passed": len(CHECKS) - len(failed),
        "failed": len(failed),
        "status": "ok" if not failed else "fail",
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
