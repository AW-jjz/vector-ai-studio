# 冻结的 fixtures 回归输出（r93 / Phase 2-3）

这个文件是**换实现之前**跑出来的。r93 把 `scripts/run_fixtures.py` 里的
占位符展开、`setup_dir` 物化、单 case 执行与码匹配、汇总、声明条数守卫
换成 `verdict_kit.fixtures`（`expand_args` / `materialize` / `run_case` /
`summarize` / `check_declared_count`），断言 A112 用它做前后对照：换实现
后重跑，必须与这里一致，否则就是抽取改变了行为。

| 文件 | 内容 | 约束 |
|---|---|---|
| `run-fixtures-frozen.json` | `run_fixtures.py --json` 的**整份信封**（37 条 cases + total/passed/failed/all_ok） | 逐字节相等；实测两次运行 byte-identical |

## 为什么可以逐字节比较

`run_fixtures.py --json` 的输出在本环境下连字节都可复现（两次运行
byte-identical，14514 B），且信封里没有任何易变内容：

* `run_id` 不被 `--json` 传入，恒为空串；
* 没有时间戳字段，也没有耗时统计；
* 每条 case 只有 `id / script / ok / want_exit / got_exit / want_codes /
  got_codes / want_acceptance / got_acceptance`，全部是声明值或脚本自己
  输出的稳定值；
* 临时目录路径**不会**进入信封。`@OUT@` / `@RUN@` 只用于拼子进程命令行，
  写进结果的是 `got_acceptance` 的**字面值**（例如 `grade=不达标`），不是
  `acceptance.json` 的路径。逐字段核对过，整份文件里没有 `C:`、`Temp`、
  `tmp`、`vector-ai-fixtures` 等任何临时路径片段。

所以 A112 比较**整份信封的字节**，没有需要剔除的字段。这条约束也反过来
约束实现：任何在信封里塞时间戳或绝对路径的改动都会立刻判红。

## 37 条 case 的冻结观测

`got_codes` 是去重并排序后的 `errors[].code`；`-` 表示空。`got_acceptance`
只对声明了 `want_acceptance` 的 4 条非空（`run-dir-*`），其余为空对象。

| id | script | want_exit | got_exit | got_codes | got_acceptance | ok |
|---|---|---|---|---|---|---|
| good-freeze-spec | freeze_spec.py | 0 | 0 | - | - | ok |
| good-sanitize | sanitize_svg.py | 0 | 0 | - | - | ok |
| good-judge | llm_judge.py | 0 | 0 | - | - | ok |
| good-score | score.py | 0 | 0 | - | - | ok |
| good-render-png-fallback | render_png.py | 0 | 0 | - | - | ok |
| bad-bitmap | sanitize_svg.py | 1 | 1 | E-BITMAP | - | ok |
| bad-empty-spec | freeze_spec.py | 1 | 1 | E-EMPTY-SPEC | - | ok |
| bad-degenerate-forbidden | sanitize_svg.py | 1 | 1 | E-DEGENERATE,E-FORBIDDEN | - | ok |
| bad-palette-overflow | score.py | 1 | 1 | E-C-PALETTE-OVERFLOW | - | ok |
| good-l2-degraded | aggregate_log.py | 0 | 0 | - | - | ok |
| good-o4-isolated | aggregate_log.py | 0 | 0 | - | - | ok |
| bad-l2-fabricated | aggregate_log.py | 1 | 1 | E-FABRICATED-SCORE | - | ok |
| bad-budget-exceeded | aggregate_log.py | 1 | 1 | E-BUDGET-EXCEEDED | - | ok |
| bad-f-backfill | aggregate_log.py | 1 | 1 | E-F-DATA-ISOLATION | - | ok |
| bad-quick-claimed-pass | aggregate_log.py | 1 | 1 | E-QUICK-UNVERIFIED | - | ok |
| good-acceptance-reachable | aggregate_log.py | 0 | 0 | - | - | ok |
| bad-no-score | aggregate_log.py | 1 | 1 | E-NO-SCORE | - | ok |
| good-landing-only | aggregate_log.py | 0 | 0 | - | - | ok |
| bad-exemption-abuse-full | aggregate_log.py | 1 | 1 | E-EXEMPTION-ABUSE | - | ok |
| bad-exemption-abuse-lo | aggregate_log.py | 1 | 1 | E-EXEMPTION-ABUSE,E-NO-SCORE | - | ok |
| bad-missing-f | aggregate_log.py | 1 | 1 | E-NO-SCORE | - | ok |
| bad-missing-a | aggregate_log.py | 1 | 1 | E-NO-SCORE | - | ok |
| good-render-png-rect-faithful | render_png.py | 0 | 0 | - | - | ok |
| bad-pass-missing-e | aggregate_log.py | 1 | 1 | E-NO-SCORE | - | ok |
| bad-grade-v1-e-zero | aggregate_log.py | 1 | 1 | E-GRADE-MISMATCH | - | ok |
| bad-grade-v2-a-zero | aggregate_log.py | 1 | 1 | E-GRADE-MISMATCH | - | ok |
| bad-grade-v3-h1-false | aggregate_log.py | 1 | 1 | E-GRADE-MISMATCH | - | ok |
| bad-grade-v7-all-zero | aggregate_log.py | 1 | 1 | E-GRADE-MISMATCH | - | ok |
| bad-grade-v8-no-gates | aggregate_log.py | 1 | 1 | E-GRADE-MISMATCH | - | ok |
| bad-grade-v9-excellent | aggregate_log.py | 1 | 1 | E-GRADE-MISMATCH | - | ok |
| run-dir-fake-ai | aggregate_log.py | 1 | 1 | E-H1-STRUCTURE | hard_gates.H1.pass=False,grade=不达标 | ok |
| run-dir-no-meta | aggregate_log.py | 1 | 1 | - | capability_missing=True,degraded=True,grade=未判定,tier=indeterminate,deliverable_status=deliverable_with_human_review | ok |
| run-dir-one-assertion | aggregate_log.py | 1 | 1 | - | hard_gates.H3.pass=False,grade=不达标 | ok |
| run-dir-raster-image-mismatch | aggregate_log.py | 1 | 1 | E-H1-STRUCTURE | hard_gates.H1.pass=False,grade=不达标 | ok |
| bad-sanitize-missing-input | sanitize_svg.py | 2 | 2 | E-SCHEMA | - | ok |
| bad-studio-usage-envelope | studio.py | 2 | 2 | E-USAGE | - | ok |
| bad-schema-check-unreadable | schema_check.py | 2 | 2 | E-SCHEMA | - | ok |

汇总（信封 `data`）：`total=37 / passed=37 / failed=0 / all_ok=true`。

## 复现

```
PATH="/c/Program Files/Tesseract-OCR:$PATH" \
PYTHONDONTWRITEBYTECODE=1 \
VECTORCRAFT_CLI="<portable>/vectorcraft-cli.exe" \
python scripts/run_fixtures.py --json
```

37 条 case 全部走内置渲染器与真实子进程，不依赖外部引擎；单次约 9 秒。
