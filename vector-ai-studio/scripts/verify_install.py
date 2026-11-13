#!/usr/bin/env python3
"""verify_install.py — 装完之后的自验：引擎路径到底通不通。

Why this exists (r72): the package's own gates say a lot about the CODE, and
almost nothing about the INSTALL. Three assertions (A30 / A31 / A46) need a
real VectorCraft CLI; on a machine without one they are *skipped* -- which is
honest, but it means "the suite is green" says nothing about whether the
engine path works on THIS machine.

So this script asks the question the suite cannot: with the engine actually
present, do the engine-bound paths really run? It reuses the suite's own
assertions (no second set of criteria), then runs the landing paths once so
the user gets artefacts and readable evidence, not just a verdict.

The one rule it must never break: **it never reports "ok" for a check it did
not execute.** An absent engine is reported as "cannot be verified here"
(exit 3), not as a pass.

    python scripts/verify_install.py            # human table
    python scripts/verify_install.py --json     # 9-field envelope

Exit codes: 0 = all verified · 1 = a check failed · 3 = engine absent
(cannot be verified here -- NOT a pass) · 2 = usage error.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import trace_ref

SCRIPT = "verify_install.py"
ROOT = L.skill_root()
ENGINE_BOUND = ("A30-vectorcraft-chain-h1-h2", "A31-deliverable-schema-no-drift",
                "A46-craft-only-landing")


def _call(script, args, cwd, timeout=900):
    """Run one of the package's scripts; return (exit_code, envelope)."""
    cmd = [sys.executable, str(ROOT / "scripts" / script)] + [str(a) for a in args]
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                           timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return -1, {"status": "error", "code": L.EXIT_ERROR,
                    "errors": [{"code": "E-INTERNAL", "msg": str(e)}]}
    try:
        env = json.loads(r.stdout or "")
    except json.JSONDecodeError:
        env = {"status": "error", "code": L.EXIT_ERROR,
               "errors": [{"code": "E-INTERNAL",
                           "msg": (r.stdout or r.stderr or "")[:200]}]}
    return r.returncode, env


def main():
    ap = argparse.ArgumentParser(
        description="verify the installed package against the engine it will use")
    ap.add_argument("--engine", default="",
                    help="explicit path to vectorcraft-cli (default: the package's lookup chain)")
    ap.add_argument("--example", default="",
                    help="SVG to land (default: examples/01-bookshop/final.svg)")
    ap.add_argument("--out", default="",
                    help="keep the artefacts here (default: a temp dir, removed afterwards)")
    ap.add_argument("--skip-selftest", action="store_true",
                    help="only run the engine checks and the landing paths")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    checks = []
    work = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="verify-install-"))
    work.mkdir(parents=True, exist_ok=True)
    keep = bool(args.out)

    def add(cid, name, expect, actual, ok, note="", code=""):
        checks.append({"id": cid, "name": name, "expect": expect, "actual": actual,
                       "ok": bool(ok), "note": note, "code": code})
        return ok

    # ---- E1..E3: the engine, and whether it is the pinned build -------------
    cli = None
    version = ""
    if args.engine:
        p = Path(args.engine)
        if p.is_file():
            cli = p
    if cli is None:
        cli = trace_ref.find_cli()
    if cli is not None:
        version = L.engine_version(cli)
    engine_present = cli is not None
    add("E1", "引擎可定位", "find_cli() 找到 vectorcraft-cli",
        str(cli) if cli else "未找到", engine_present,
        "" if engine_present else
        "查装位置：VECTORCRAFT_CLI 环境变量 → PATH → 平台目录"
        "（脚本 scripts/trace_ref.py 的 find_cli()）。未装时本脚本只能报"
        "「无法复测」，不会替你判达标。")

    lock = L.load_engine_lock()
    lock_entry = None
    for e in (lock.get("entries") or []):
        if (e.get("version") == version and
                e.get("platform") == L.engine_platform()):
            lock_entry = e
            break
    if not lock_entry:
        for e in (lock.get("entries") or []):
            if e.get("version") == version:
                lock_entry = e
                break
    expected = str((lock_entry or {}).get("sha256", ""))
    actual_sha = L.file_sha256(cli) if cli else ""
    drift = bool(expected) and bool(actual_sha) and expected != actual_sha
    if not engine_present:
        add("E2", "引擎版本可读", "非空版本串", "—", False, "引擎缺席")
        add("E3", "引擎二进制哈希", "等于 engine.lock.json 里钉的哈希", "—", False,
            "引擎缺席，无法复算")
    else:
        add("E2", "引擎版本可读", "非空版本串", version or "—", bool(version),
            "" if version else "引擎没有报出版本")
        add("E3", "引擎二进制哈希", expected[:16] + "…" if expected else "锁内无对应条目",
            actual_sha[:16] + "…" if actual_sha else "—",
            (actual_sha == expected) if expected else True,
            "" if (not expected or actual_sha == expected) else
            "与 engine.lock.json 不符：同版本、不同构建，之前的回归结论不再成立",
            "E-ENGINE-DRIFT" if drift else "")

    # ---- V1: the engine-bound assertions, really executed -------------------
    st = None
    if not args.skip_selftest:
        rc, env = _call("selftest.py", ["--json", "--fixtures"], work)
        st = env.get("data") or {}
        asserts = st.get("assertions") or []
        ran = {str(a.get("id")): a.get("ok") for a in asserts}
        for aid in ENGINE_BOUND:
            got = ran.get(aid)
            add("V1-" + aid.split("-")[0], aid + " 真执行", "ok=true（不是 skipped）",
                "ok=%s" % got, got is True,
                "" if got is True else
                "断言未被执行或未通过——引擎路径没有被真正验证")
        expected_n = L.EXPECTED_ASSERTIONS + 1      # A13 runs only with --fixtures
        add("V2", "自检整体", "%d 条断言全过、exit 0" % expected_n,
            "%d 条 / failed=%s / skipped=%s / exit %s"
            % (len(asserts), st.get("failed"),
               len([a for a in asserts if a.get("ok") is None]), rc),
            rc == 0 and not st.get("failed")
            and not [a for a in asserts if a.get("ok") is None])

    # ---- V3/V4: the landing paths, once, with artefacts --------------------
    example = Path(args.example) if args.example else (ROOT / "examples" / "01-bookshop" / "final.svg")
    if not example.is_file():
        add("V3", "真机落地（land_vc）", "示例 SVG 存在", str(example), False,
            "示例缺失")
    else:
        out_dir = work / "land-vc"
        rc, env = _call("land_vc.py",
                        ["--svg", str(example), "--out", str(out_dir),
                         "--profile", "print", "--json"], work)
        data = env.get("data") or {}
        ai = out_dir / "final.ai"
        sr = out_dir / "structure_readback.json"
        sr_ok = False
        if sr.is_file():
            try:
                sr_ok = json.loads(sr.read_text(encoding="utf-8")).get("ok") is True
            except (OSError, json.JSONDecodeError):
                sr_ok = False
        probe_ok = False
        try:
            import lib_pdf
            probe = lib_pdf.ai_h1_probe(ai) if ai.is_file() else {}
            probe_ok = bool(probe.get("pdf_ok"))
        except Exception:                                  # noqa: BLE001
            probe_ok = False
        add("V3", "真机落地（land_vc）",
            "final.ai 非空 + 结构回读 ok=true + %PDF- 字节复算通过",
            "ai=%s B / readback_ok=%s / pdf_probe=%s / exit %s"
            % (ai.stat().st_size if ai.is_file() else 0, sr_ok, probe_ok, rc),
            rc == 0 and ai.is_file() and ai.stat().st_size > 0 and sr_ok and probe_ok)

        craft_dir = work / "land-craft"
        rc2, env2 = _call("land_craft.py",
                          ["--svg", str(example), "--out", str(craft_dir),
                           "--profile", "print", "--bleed-mm", "3",
                           "--pdf-standard", "pdfX1a", "--json"], work)
        d2 = env2.get("data") or {}
        px = d2.get("pdf_x") or {}
        pdf = craft_dir / "print.pdf"
        add("V4", "印刷母版（land_craft · PDF/X-1a）",
            "print.pdf 非空 + h1_pass + GTS_PDFXVersion + output_intents≥1",
            "pdf=%s B / h1=%s / gts_pdfx=%s / intents=%s / exit %s"
            % (pdf.stat().st_size if pdf.is_file() else 0, d2.get("h1_pass"),
               px.get("gts_pdfx"), px.get("output_intents"), rc2),
            rc2 == 0 and pdf.is_file() and pdf.stat().st_size > 0
            and d2.get("h1_pass") is True
            and (px.get("gts_pdfx") or 0) >= 1
            and (px.get("output_intents") or 0) >= 1)

    failed = [c for c in checks if not c["ok"]]
    engine_checks = [c for c in checks if c["id"].startswith("E")]
    engine_ok = engine_present and all(c["ok"] for c in engine_checks)
    if engine_ok and not failed:
        verdict, code, status = "通过：引擎路径已验证", L.EXIT_OK, "ok"
    elif not engine_present:
        verdict, code, status = "无法复测：引擎缺席（不是通过）", L.EXIT_MISSING, "degraded"
    else:
        verdict, code, status = "不通过：%d 项未达预期" % len(failed), L.EXIT_FAIL, "fail"

    doc = {
        "verdict": verdict,
        "exit_code": code,
        "engine": {"path": str(cli) if cli else None, "version": version,
                   "sha256": actual_sha, "lock_expected": expected,
                   "platform": L.engine_platform(), "drift": drift},
        "checks": checks,
        "counts": {"total": len(checks),
                   "ok": len(checks) - len(failed),
                   "failed": len(failed)},
        "note": "本脚本只回答「这台机器上的引擎路径通不通」。它不复算质量判据——"
                "质量结论由 scripts/aggregate_log.py 按 config/thresholds.json 出。",
    }
    if not keep:
        shutil.rmtree(work, ignore_errors=True)

    if not args.json:
        # The human table goes to STDERR: stdout stays exactly one envelope,
        # which is this package's contract ("所有命令脚本 stdout 均为该信封").
        out = sys.stderr
        out.write("vector-ai-studio 安装自验（引擎路径）\n")
        out.write("  引擎：%s\n" % (("%s（版本 %s）" % (cli, version)) if cli else "未找到"))
        out.write("  锁：  %s\n" % ("匹配 engine.lock.json" if expected and actual_sha == expected
                                    else ("未钉住或不一致" if engine_present else "—")))
        out.write("\n")
        for c in checks:
            out.write("  [%s] %-9s %-30s expect=%s | actual=%s%s\n"
                      % ("PASS" if c["ok"] else "FAIL", c["id"], c["name"][:30],
                         c["expect"], c["actual"], (" | " + c["note"]) if c["note"] else ""))
        out.write("\n  结论：%s（exit %d）\n" % (verdict, code))
        out.write("  证据：%s\n" % ("已保留在 " + str(work) if keep else "临时目录已清理"))

    errors = [L.err(c["code"] or "E-VERIFY-FAILED", c["name"] + ": " + str(c["actual"]))
              for c in failed if c["code"] or not engine_present]
    return L.emit(SCRIPT, status=status, code=code, data=doc, errors=errors or None)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))
