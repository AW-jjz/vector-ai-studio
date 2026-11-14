#!/usr/bin/env python3
"""Generate every cross-file enum from ONE source: contract/contract.json.

Why this exists (r72, plan M1): the `fidelity` vocabulary used to live in six
files with three different spellings (`full` in the producers, `faithful` in
the judge, `limited` forced in the display). Nothing compared them, so the
drift shipped and the whole suite stayed green: a real renderer wrote `full`,
the judge only accepted `faithful`, and every real machine landed in L2
forever. That is not a typo, it is an architecture that allows the bug.

So the vocabulary is now a GENERATED ARTIFACT:

    contract/contract.json          <- the only place an enum is written
        |  scripts/gen_contract.py
        +-> scripts/_contract.py                    (imported by producers AND judges)
        +-> references/schemas/contract-enums.schema.json
        +-> docs/generated/contract-table.md
        +-> tests/fixtures/contract/contract-matrix.json

`--check` regenerates in memory and compares byte-for-byte with what is on
disk; a difference is `E-CONTRACT-DRIFT`. CI runs it, so a hand-edited
generated file (or an un-regenerated contract) is red, not a surprise.

The generator never invents a value: it only moves what the contract says.

Phase 2-1 (r90) splits the mechanics from the vocabulary: loading the
document, comparing bytes, treating a missing target as drift and writing
atomically all live in the vendored `verdict_kit.contract`. What stays here is
the part only this skill knows -- the four renderers, the required keys, and
the cross-check against `config/thresholds.json`. `_contract_path()` and
`ROOT` remain module globals so the cases in
`tests/fixtures/contract-gen/` can point the generator at a throw-away tree.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

# Phase 2-1: the vendored contract generator owns load / byte compare / atomic
# write. Importing the package would create scripts/_vendor/.../__pycache__
# (A22), so bytecode writing is disabled for this CLI process.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor" / "verdict-kit"))
import verdict_kit.contract as VC  # noqa: E402

SCRIPT = "gen_contract.py"
CONTRACT_SCHEMA = "vector-ai-studio/contract-v1"
CONTRACT_KEYS = ("enums", "verdict", "banned_claim_words")
# The codes this CLI may emit while loading the contract. They are named here,
# not only in the kit, because references/error-codes.md registers what THIS
# CLI can emit and A28 compares the two sets in both directions. The tuple is
# also the boundary: a code the kit invents later is not forwarded silently.
CONTRACT_ERROR_CODES = ("E-CONTRACT-MISSING", "E-CONTRACT-PARSE",
                        "E-CONTRACT-SCHEMA")
ROOT = L.skill_root()
CONTRACT_PATH = ROOT / "contract" / "contract.json"


def _contract_path():
    return CONTRACT_PATH


def load_contract():
    """Load and shape-check the contract.

    The kit raises its own ToolError; the skill's run_cli only rescues
    lib_common.ToolError, so the code/exit pair is carried across unchanged.
    A contract that is valid JSON but not an object is refused as
    E-CONTRACT-SCHEMA here instead of dying as E-INTERNAL later.
    """
    try:
        return VC.load_spec(_contract_path(), CONTRACT_SCHEMA, CONTRACT_KEYS)
    except VC.ToolError as e:
        code = e.code if e.code in CONTRACT_ERROR_CODES else "E-CONTRACT-SCHEMA"
        raise L.ToolError(code, e.msg, e.exit_code)


def thresholds_problems(obj):
    """The contract and config/thresholds.json must agree on the grade words.

    The contract owns the MACHINE vocabulary (tiers, dims, banned words);
    thresholds.json owns the LOCALISED words a human reads. The mapping
    between them is declared here, so a renamed message key or a missing
    `grade_indeterminate` is caught instead of surfacing as a KeyError in the
    middle of a run.
    """
    problems = []
    try:
        th = L.load_thresholds()
    except L.ToolError as e:
        return [e.msg]
    msgs = th.get("messages") or {}
    mapping = (obj.get("verdict") or {}).get("tier_to_grade_key") or {}
    for tier, key in sorted(mapping.items()):
        if key not in msgs:
            problems.append("thresholds.json messages lacks " + key +
                            " (tier " + tier + ")")
    legacy = (obj.get("verdict") or {}).get("legacy_grade_key")
    if legacy and legacy not in msgs:
        problems.append("thresholds.json messages lacks " + legacy)
    return problems


# --------------------------------------------------------------------------
# generators (each returns the exact file text; no timestamps, no randomness)
# --------------------------------------------------------------------------

def _py_repr(value):
    return json.dumps(value, ensure_ascii=False)


def gen_contract_py(obj):
    enums = obj["enums"]
    verdict = obj["verdict"]
    lines = [
        '"""GENERATED FILE -- do not edit by hand.',
        "",
        "Source of truth: contract/contract.json",
        "Regenerate:    python scripts/gen_contract.py",
        "Check (CI):    python scripts/gen_contract.py --check",
        "",
        "Producers and judges import THIS, so a second vocabulary cannot exist:",
        "a typo becomes an AttributeError instead of a silent wrong verdict.",
        '"""',
        "",
        "CONTRACT_SCHEMA = " + _py_repr(obj["schema"]),
        "CONTRACT_VERSION = " + _py_repr(obj["version"]),
        "CRITERIA_VERSION = " + _py_repr(obj["criteria_version"]),
        "",
    ]
    for name in sorted(enums):
        spec = enums[name]
        cls = name.upper()
        lines.append("")
        lines.append("class " + cls + ":")
        note = str(spec.get("note", "")).replace('"""', "'''")
        if note:
            lines.append('    """' + note + '"""')
        for v in spec["values"]:
            lines.append("    " + v.upper().replace("-", "_") + " = " + _py_repr(v))
        lines.append("    VALUES = " + _py_repr(tuple(spec["values"])))
        if name == "fidelity":
            lines.append("    FAIL_CLOSED_DEFAULT = " + _py_repr(spec["fail_closed_default"]))
            accepted = {}
            for consumer, dims in sorted(spec["accepted_by"].items()):
                accepted[consumer] = {d: tuple(v) for d, v in sorted(dims.items())}
            lines.append("    ACCEPTED_BY = " + _py_repr(accepted))
            producers = {}
            for producer, cases in sorted(spec["producers"].items()):
                producers[producer] = dict(sorted(cases.items()))
            lines.append("    PRODUCERS = " + _py_repr(producers))
        lines.append("")
    lines += [
        "class VERDICT:",
        '    """Fields the judge re-derives from evidence; a log may not attest them."""',
        "    TIERS = " + _py_repr(tuple(verdict["tiers"])),
        "    TIER_TO_GRADE_KEY = " + _py_repr(dict(sorted(verdict["tier_to_grade_key"].items()))),
        "    LEGACY_GRADE_KEY = " + _py_repr(verdict["legacy_grade_key"]),
        "    LEGACY_TIER = " + _py_repr(verdict["legacy_tier"]),
        "    REQUIRED_DIMS = " + _py_repr(tuple(verdict["required_dims"])),
        "    OPTIONAL_DIMS = " + _py_repr(tuple(verdict["optional_dims"])),
        "    SELF_ATTESTATION_FORBIDDEN = " + _py_repr(tuple(verdict["self_attestation_forbidden"])),
        "",
        "BANNED_CLAIM_WORDS = " + _py_repr(tuple(obj["banned_claim_words"])),
        "COVERAGE_FIX_HINTS = " + _py_repr(dict(sorted((obj.get("coverage_fix_hints") or {}).items()))),
        "",
        "LITERAL_SCAN_FILES = " + _py_repr(tuple((obj.get("literal_scan") or {}).get("files") or [])),
        "LITERAL_SCAN_EXCLUDE = " + _py_repr(tuple((obj.get("literal_scan") or {}).get("exclude") or [])),
        "LITERAL_SCAN_FORBIDDEN = " + _py_repr(tuple((obj.get("literal_scan") or {}).get("forbidden_literals") or [])),
        "",
        "",
        "class CLAIMS:",
        '    """The outward-promise registry: which files are scanned, which words',
        "    make a sentence a promise, and the coverage floor CI enforces.\"\"\"",
        "    REGISTRY = " + _py_repr(str((obj.get("claims") or {}).get("registry", ""))),
        "    SCAN_FILES = " + _py_repr(tuple((obj.get("claims") or {}).get("scan_files") or [])),
        "    MANDATORY_WORDS = " + _py_repr(tuple((obj.get("claims") or {}).get("mandatory_words") or [])),
        "    COVERAGE_FLOOR = " + _py_repr(float((obj.get("claims") or {}).get("coverage_floor", 0.0))),
        "",
        "",
        "def is_fidelity(value):",
        '    """Is this one of the contract\'s fidelity values?"""',
        "    return isinstance(value, str) and value in FIDELITY.VALUES",
        "",
        "",
        "def fidelity_accepted_for(consumer, dim):",
        '    """The fidelity values this consumer accepts for this dimension."""',
        "    return FIDELITY.ACCEPTED_BY.get(consumer, {}).get(dim, ())",
        "",
        "",
        "def fidelity_for(producer, case):",
        '    """The fidelity value this producer must write for this case."""',
        "    return FIDELITY.PRODUCERS.get(producer, {}).get(case)",
        "",
        "",
        "def fidelity_is_judged(consumer, dim, value):",
        '    """May this consumer judge this dimension on a preview of this fidelity?"""',
        "    return value in fidelity_accepted_for(consumer, dim)",
        "",
    ]
    return "\n".join(lines)


def gen_schema(obj):
    enums = obj["enums"]
    verdict = obj["verdict"]
    defs = {}
    props = {}
    for name in sorted(enums):
        spec = enums[name]
        defs[name] = {"type": "string", "enum": list(spec["values"])}
        props[name] = {"$ref": "#/$defs/" + name}
    doc = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://vector-ai-studio.local/schemas/contract-enums.schema.json",
        "title": "vector-ai-studio contract enums",
        "description": ("GENERATED from contract/contract.json by "
                        "scripts/gen_contract.py. Every enum that crosses a file "
                        "boundary in this skill is defined there and only there."),
        "$defs": defs,
        "type": "object",
        "properties": props,
    }
    if "fidelity" in defs:
        doc["$defs"]["fidelity_policy"] = {
            "type": "object",
            "description": "Who may write which fidelity, and who accepts it.",
            "properties": {
                "accepted_by": {"type": "object"},
                "producers": {"type": "object"},
                "fail_closed_default": {"type": "string"},
            },
        }
    doc["$defs"]["verdict"] = {
        "type": "object",
        "description": "The fields a consumer re-derives from evidence.",
        "properties": {
            "tiers": {"type": "array", "items": {"type": "string"}},
            "required_dims": {"type": "array", "items": {"type": "string"}},
            "optional_dims": {"type": "array", "items": {"type": "string"}},
            "self_attestation_forbidden": {"type": "array", "items": {"type": "string"}},
        },
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def gen_doc_table(obj):
    enums = obj["enums"]
    verdict = obj["verdict"]
    out = []
    out.append("# 契约表（自动生成，勿手改）")
    out.append("")
    out.append("> 来源：`contract/contract.json`（契约版本 " + obj["version"] +
               "，判据版本 " + obj["criteria_version"] + "）")
    out.append("> 重新生成：`python scripts/gen_contract.py`；CI 用 `--check` 卡漂移。")
    out.append("")
    out.append("## 枚举")
    out.append("")
    out.append("| 枚举 | 取值 | 说明 |")
    out.append("|---|---|---|")
    for name in sorted(enums):
        spec = enums[name]
        vals = " / ".join("`" + v + "`" for v in spec["values"])
        note = str(spec.get("note", "")).replace("\n", " ")
        out.append("| `" + name + "` | " + vals + " | " + note + " |")
    out.append("")
    fid = enums["fidelity"]
    out.append("## 保真度：谁能写、谁接受")
    out.append("")
    out.append("| 生产者 | 场景 | 写入值 |")
    out.append("|---|---|---|")
    for producer in sorted(fid["producers"]):
        for case in sorted(fid["producers"][producer]):
            out.append("| `" + producer + "` | " + case + " | `" +
                       fid["producers"][producer][case] + "` |")
    out.append("")
    out.append("| 消费者 | 维 | 接受的值 |")
    out.append("|---|---|---|")
    for consumer in sorted(fid["accepted_by"]):
        for dim in sorted(fid["accepted_by"][consumer]):
            vals = " / ".join("`" + v + "`" for v in fid["accepted_by"][consumer][dim])
            out.append("| `" + consumer + "` | " + dim + " | " + vals + " |")
    out.append("")
    out.append("缺字段 / 未知值一律按 fail-closed 默认值 `" +
               fid["fail_closed_default"] + "` 处理。")
    out.append("")
    out.append("## 判决字段")
    out.append("")
    out.append("| 字段 | 说明 |")
    out.append("|---|---|")
    for tier in verdict["tiers"]:
        out.append("| tier `" + tier + "` | grade 取 `" +
                   verdict["tier_to_grade_key"][tier] + "` 对应的文案 |")
    out.append("| 必需维 | " + " / ".join("`" + d + "`" for d in verdict["required_dims"]) + " |")
    out.append("| 可选维 | " + " / ".join("`" + d + "`" for d in verdict["optional_dims"]) + " |")
    out.append("| 禁止自证字段 | " + " / ".join("`" + f + "`" for f in verdict["self_attestation_forbidden"]) + " |")
    out.append("")
    out.append("判据版本 " + obj["criteria_version"] + " 起，判决是三值的："
               "`pass` / `fail` / `indeterminate`。"
               "`indeterminate`（未判定）表示**没能力测**，不等于**不达标**。")
    out.append("")
    return "\n".join(out)


def gen_matrix(obj):
    fid = obj["enums"]["fidelity"]
    consumer = "aggregate_log"
    dims = sorted(fid["accepted_by"].get(consumer, {}))
    rows = []
    for producer in sorted(fid["producers"]):
        for case in sorted(fid["producers"][producer]):
            value = fid["producers"][producer][case]
            judged = all(value in fid["accepted_by"][consumer][d] for d in dims)
            rows.append({
                "id": producer + "/" + case,
                "producer": producer,
                "case": case,
                "fidelity": value,
                "dims": dims,
                "expect_judged": judged,
            })
    produced = {r["fidelity"] for r in rows}
    for value in fid["values"]:
        if value in produced:
            continue
        judged = all(value in fid["accepted_by"][consumer][d] for d in dims)
        rows.append({
            "id": "synthetic/" + value,
            "producer": "-",
            "case": "synthetic",
            "fidelity": value,
            "dims": dims,
            "expect_judged": judged,
        })
    return json.dumps({
        "schema": "vector-ai-studio/contract-matrix-v1",
        "note": ("自动生成：每个生产者场景 × 保真度取值，及其应得的消费者判定。"
                 "selftest 用它检查真实行为与契约一致（正例 + 反例）。"),
        "generated_from": {"contract_version": obj["version"],
                           "criteria_version": obj["criteria_version"]},
        "consumer": consumer,
        "rows": rows,
    }, ensure_ascii=False, indent=2) + "\n"


def _artifacts():
    """One pure renderer per target; the kit only moves the bytes."""
    return [
        VC.Artifact("scripts/_contract.py", gen_contract_py),
        VC.Artifact("references/schemas/contract-enums.schema.json", gen_schema),
        VC.Artifact("docs/generated/contract-table.md", gen_doc_table),
        VC.Artifact("tests/fixtures/contract/contract-matrix.json", gen_matrix),
    ]


def main():
    ap = argparse.ArgumentParser(description="generate contract artifacts")
    ap.add_argument("--check", action="store_true",
                    help="compare generated output with what is on disk; "
                         "exit 1 (E-CONTRACT-DRIFT) on any difference")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    obj = load_contract()
    problems = thresholds_problems(obj)
    if problems:
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL,
                      data={"problems": problems},
                      errors=[L.err("E-CONTRACT-DRIFT", p) for p in problems])

    report = VC.generate(obj, _artifacts(), ROOT, check=args.check)

    if args.check and not report.ok:
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL,
                      data={"files": report.files, "drift": list(report.drift)},
                      errors=[L.err("E-CONTRACT-DRIFT",
                                    "generated files are out of date: " +
                                    ", ".join(report.drift) +
                                    " -- run: python scripts/gen_contract.py")])
    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK,
                  data={"contract_version": obj["version"],
                        "criteria_version": obj["criteria_version"],
                        "files": report.files, "mode": report.mode},
                  artifacts={"contract": "contract/contract.json"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
