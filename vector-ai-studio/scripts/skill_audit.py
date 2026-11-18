#!/usr/bin/env python3
"""Audit an Agent Skills package without changing it.

The default path uses only the standard library. It checks the public
frontmatter contract, progressive-disclosure budget, local resource links,
JSON/Python syntax and trigger-evaluation coverage. ``--official`` additionally
runs the optional agentskills/skills-ref validator; absence is reported as a
capability degradation, never as a pass.

    python scripts/skill_audit.py --require-evals --json
    python scripts/skill_audit.py --official --require-official --json
"""
import argparse
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "skill_audit.py"
ALLOWED_FRONTMATTER = {
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
}
ROOT_PREFIXES = (
    "references/", "scripts/", "config/", "contract/", "templates/",
    "examples/", "tests/", "evals/",
)
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
TOP_FIELD_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$")
META_FIELD_RE = re.compile(r"^\s+([A-Za-z0-9_-]+):(?:\s*(.*))?$")


def _plain(value):
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _frontmatter(text):
    """Read the flat subset used by this package, with explicit errors."""
    problems = []
    if not text.startswith("---\n"):
        return {}, {}, ["frontmatter.missing: opening delimiter"]
    lines = text.splitlines()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, {}, ["frontmatter.missing: closing delimiter"]
    top, metadata = {}, {}
    current = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = TOP_FIELD_RE.match(raw)
        if match:
            key, value = match.group(1), _plain(match.group(2))
            if key in top:
                problems.append("frontmatter.duplicate: " + key)
            top[key] = value
            current = key
            continue
        match = META_FIELD_RE.match(raw)
        if match and current == "metadata":
            key, value = match.group(1), _plain(match.group(2))
            if key in metadata:
                problems.append("frontmatter.duplicate: metadata." + key)
            metadata[key] = value
            continue
        problems.append("frontmatter.unsupported-yaml: " + raw.strip()[:80])
    return top, metadata, problems


def _check_frontmatter(root, problems):
    path = root / "SKILL.md"
    if not path.is_file():
        problems.append("frontmatter.missing: SKILL.md")
        return {}, {}, ""
    text = path.read_text(encoding="utf-8")
    top, metadata, parse_problems = _frontmatter(text)
    problems.extend(parse_problems)
    unexpected = sorted(set(top) - ALLOWED_FRONTMATTER)
    if unexpected:
        problems.append("frontmatter.unexpected: " + ",".join(unexpected))
    for key in ("name", "description"):
        if not top.get(key):
            problems.append("frontmatter.required: " + key)
    name = top.get("name", "")
    if name and (len(name) > 64 or not NAME_RE.fullmatch(name)):
        problems.append("frontmatter.name: invalid format or length")
    if name and name != root.name:
        problems.append("frontmatter.name: directory mismatch " + root.name)
    description = top.get("description", "")
    if not 1 <= len(description) <= 1024:
        problems.append("frontmatter.description: length " + str(len(description)))
    if "<" in description or ">" in description:
        problems.append("frontmatter.description: angle brackets are forbidden")
    compatibility = top.get("compatibility", "")
    if len(compatibility) > 500:
        problems.append("frontmatter.compatibility: length exceeds 500")
    if "version" in metadata and not re.fullmatch(r"\d+\.\d+\.\d+", metadata["version"]):
        problems.append("frontmatter.metadata.version: expected semantic version")
    return top, metadata, text


def _markdown_files(root):
    files = [root / "SKILL.md", root / "README.md"]
    for folder in ("references", "templates", "examples"):
        base = root / folder
        if base.is_dir():
            files.extend(sorted(base.rglob("*.md")))
    return [p for p in files if p.is_file()]


def _clean_target(raw):
    raw = str(raw).strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1]
    # A Markdown title is not part of the path; a command's arguments are not
    # part of a backtick-quoted script path either.
    raw = raw.split()[0] if raw.split() else ""
    return raw.strip("'\"`.,;:!?，。；：！？（）()")


def _resource_exists(root, source, target, backtick=False):
    target = _clean_target(target)
    if not target or target.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", target):
        return None
    if "<" in target or ">" in target or "..." in target:
        return None
    if target.startswith("/"):
        return None
    candidate = None
    if target.startswith("."):
        candidate = (source.parent / target).resolve()
    elif backtick and target.startswith(ROOT_PREFIXES):
        candidate = (root / target).resolve()
    elif not backtick:
        candidate = (source.parent / target).resolve()
    if candidate is None:
        return None
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return "reference.escape: " + target
    if "*" in target or "?" in target:
        return None if any(candidate.parent.glob(candidate.name)) else "reference.missing: " + target
    return None if candidate.exists() else "reference.missing: " + target


def _check_references(root, problems):
    count = 0
    for doc in _markdown_files(root):
        text = doc.read_text(encoding="utf-8")
        rel = doc.relative_to(root).as_posix()
        targets = [(m.group(1), False) for m in LINK_RE.finditer(text)]
        targets.extend((m.group(1), True) for m in BACKTICK_RE.finditer(text))
        for raw, backtick in targets:
            cleaned = _clean_target(raw)
            if backtick and not cleaned.startswith(ROOT_PREFIXES) and not cleaned.startswith("."):
                continue
            issue = _resource_exists(root, doc, raw, backtick=backtick)
            if issue:
                problems.append(issue + " from " + rel)
            elif cleaned and (cleaned.startswith(ROOT_PREFIXES) or cleaned.startswith(".")):
                count += 1
    return count


def _check_syntax(root, problems):
    json_count = 0
    for folder in ("config", "contract", "templates", "evals"):
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.json")):
            try:
                json.loads(path.read_text(encoding="utf-8"))
                json_count += 1
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                problems.append("json.invalid: %s: %s" % (
                    path.relative_to(root).as_posix(), str(exc)[:120]))
    py_count = 0
    scripts = root / "scripts"
    if scripts.is_dir():
        for path in sorted(scripts.rglob("*.py")):
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                py_count += 1
            except (OSError, UnicodeError, SyntaxError) as exc:
                problems.append("python.invalid: %s: %s" % (
                    path.relative_to(root).as_posix(), str(exc)[:120]))
    return json_count, py_count


def _check_evals(root, required, problems):
    path = root / "evals" / "evals.json"
    if not path.is_file():
        if required:
            problems.append("evals.missing: evals/evals.json")
        return {"cases": 0, "positive": 0, "negative": 0}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        problems.append("evals.invalid: " + str(exc)[:120])
        return {"cases": 0, "positive": 0, "negative": 0}
    cases = doc.get("evals")
    if not isinstance(cases, list):
        problems.append("evals.invalid: evals must be a list")
        return {"cases": 0, "positive": 0, "negative": 0}
    if doc.get("skill_name") != root.name:
        problems.append("evals.skill_name: directory mismatch")
    seen = set()
    positive = negative = 0
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            problems.append("evals.case: not an object at " + str(index))
            continue
        cid = case.get("id")
        if not isinstance(cid, int) or isinstance(cid, bool) or cid in seen:
            problems.append("evals.id: missing, duplicate or non-integer at " + str(index))
        seen.add(cid)
        for field in ("prompt", "expected_output"):
            if not str(case.get(field, "")).strip():
                problems.append("evals.%s: empty at id %s" % (field, cid))
        if not isinstance(case.get("files"), list):
            problems.append("evals.files: not a list at id " + str(cid))
        trigger = case.get("expect_trigger")
        if not isinstance(trigger, bool):
            problems.append("evals.expect_trigger: not boolean at id " + str(cid))
        elif trigger:
            positive += 1
        else:
            negative += 1
    if required:
        if len(cases) < 8:
            problems.append("evals.coverage: fewer than 8 cases")
        if positive < 3:
            problems.append("evals.coverage: fewer than 3 positive cases")
        if negative < 2:
            problems.append("evals.coverage: fewer than 2 negative cases")
    return {"cases": len(cases), "positive": positive, "negative": negative}


def audit(root, max_lines=250, require_evals=False, official=False):
    root = root.resolve()
    problems = []
    top, metadata, text = _check_frontmatter(root, problems)
    lines = len(text.splitlines()) if text else 0
    if lines > max_lines:
        problems.append("entrypoint.too-long: %d lines > %d" % (lines, max_lines))
    for doc in _markdown_files(root):
        body = doc.read_text(encoding="utf-8")
        for marker in ("TODO", "TBD", "FIXME"):
            if marker in body:
                problems.append("docs.placeholder: %s in %s" % (
                    marker, doc.relative_to(root).as_posix()))
    ref_count = _check_references(root, problems)
    json_count, py_count = _check_syntax(root, problems)
    eval_metrics = _check_evals(root, require_evals, problems)
    official_result = {"status": "not_requested", "problems": []}
    official_unavailable = False
    if official:
        if importlib.util.find_spec("skills_ref") is None:
            official_result = {"status": "unavailable", "problems": ["skills_ref not installed"]}
            official_unavailable = True
        else:
            from skills_ref import validate
            official_problems = [str(x) for x in validate(root)]
            official_result = {
                "status": "fail" if official_problems else "pass",
                "problems": official_problems,
            }
            problems.extend("official." + x for x in official_problems)
    return {
        "schema": "vector-ai-studio/skill-audit-result-v1",
        "skill": root.name,
        "status": "fail" if problems else ("degraded" if official_unavailable else "ok"),
        "problems": problems,
        "metrics": {
            "entry_lines": lines,
            "entry_chars": len(text),
            "markdown_files": len(_markdown_files(root)),
            "local_references": ref_count,
            "json_files": json_count,
            "python_files": py_count,
            **eval_metrics,
        },
        "frontmatter": {
            "name": top.get("name", ""),
            "license": top.get("license", ""),
            "version": metadata.get("version", ""),
        },
        "official": official_result,
    }, official_unavailable


def main():
    ap = argparse.ArgumentParser(description="audit an Agent Skills package")
    ap.add_argument("--root", default=str(L.skill_root()))
    ap.add_argument("--max-lines", type=int, default=250)
    ap.add_argument("--require-evals", action="store_true")
    ap.add_argument("--official", action="store_true")
    ap.add_argument("--require-official", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir() or not (root / "SKILL.md").is_file():
        raise L.ToolError("E-SCHEMA", "skill root or SKILL.md not found", L.EXIT_USAGE)
    if not 20 <= args.max_lines <= 500:
        raise L.ToolError("E-SCHEMA", "--max-lines must be between 20 and 500", L.EXIT_USAGE)
    doc, unavailable = audit(
        root, max_lines=args.max_lines, require_evals=args.require_evals,
        official=args.official or args.require_official)
    if unavailable and args.require_official:
        doc["problems"].append("official.unavailable: install skills-ref")
        doc["status"] = "fail"
    failed = bool(doc["problems"])
    code = L.EXIT_FAIL if failed else (L.EXIT_MISSING if unavailable else L.EXIT_OK)
    status = "fail" if failed else ("degraded" if unavailable else "ok")
    errors = []
    if failed:
        errors.append(L.err("E-REGRESSION", "; ".join(doc["problems"][:8])))
    elif unavailable:
        errors.append(L.err("E-REGRESSION", "official validator unavailable: install skills-ref"))
    return L.emit(SCRIPT, status=status, code=code, data=doc, errors=errors)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
