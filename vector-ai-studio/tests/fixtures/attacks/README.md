# 冻结的攻击集输出（r92 / Phase 2-2）

这两个文件是**换实现之前**跑出来的。r92 把 `scripts/redteam.py` 里的
`_apply` / `_dotted` / `_evaluate` 换成 `verdict_kit.attacks`（`apply_mutations`
/ `dotted_get` / `evaluate`），断言 A111 用它们做前后对照：换实现后重跑，
必须逐条一致，否则就是抽取改变了行为。

| 文件 | 内容 | 约束 |
|---|---|---|
| `redteam-frozen.json` | `redteam.py --json` 的**整份信封**（18 条 results + summary） | 语义全等（JSON 对象相等）；实测连字节都相同 |
| `attacks-frozen.json` | 逐条 `id / expect / exit / codes / problems` | 18 条全等，顺序无关 |

## 为什么不比较字节

`redteam.py --json` 的输出在本环境下连字节都可复现（两次运行
byte-identical，6937 B），但 A111 只承诺**语义**相等：key 顺序和缩进不是
契约，一旦有人换了 JSON 序列化实现，不该因此判红。`attacks-frozen.json`
同理，按 `id` 建索引比较。

## 唯一被排除在 parity 之外的字段

无。逐字段核对过：信封里 `run_id` 恒为空串，`results[]` 只含
`id / name / expect / status / why / codes / detail`，没有任何时间戳、绝对
路径、临时目录名或机器相关值。所以 A111 比较的是**整份信封**，没有排除项。

`_run_attack` 返回的 `run_dir` 与 `acceptance` 是内部值，不进信封，也不进
`attacks-frozen.json`。

## 18 条攻击的冻结观测

`problems` 全为空表示这条攻击被按声明拦下（`expect=block`）或按声明穿透
（`expect=leak`）。`exit` 是被调用脚本（`aggregate_log.py`）的退出码，
不是 `redteam.py` 自己的。

| id | expect | exit | codes | problems | status |
|---|---|---|---|---|---|
| 01-fake-ai | block | 1 | E-H1-STRUCTURE | — | blocked |
| 02-truncated-pdf | block | 1 | E-H1-STRUCTURE | — | blocked |
| 03-missing-preview-meta | block | 1 | — | — | blocked |
| 04-foreign-run-id | block | 1 | — | — | blocked |
| 05-mini-selftest | block | 1 | — | — | blocked |
| 06-l2-fabricated-score | block | 1 | E-FABRICATED-SCORE | — | blocked |
| 07-tampered-verdict | block | 1 | E-GRADE-MISMATCH | — | blocked |
| 08-coverage-claim | block | 1 | E-GRADE-MISMATCH | — | blocked |
| 09-indeterminate-pass-wording | block | 1 | E-INDETERMINATE-FIELDS | — | blocked |
| 10-legacy-fidelity-word | block | 1 | — | — | blocked |
| 11-narrow-track-laundering | block | 2 | E-EXEMPTION-ABUSE | — | blocked |
| 12-budget-exceeded | block | 1 | E-BUDGET-EXCEEDED | — | blocked |
| 13-f-backfill | block | 1 | E-F-DATA-ISOLATION | — | blocked |
| 14-exemption-abuse | block | 1 | E-EXEMPTION-ABUSE | — | blocked |
| 15-review-duplicate-same-path | block | 1 | E-REVIEW-INVALID | — | blocked |
| 16-review-duplicate-new-name | block | 1 | E-REVIEW-INVALID | — | blocked |
| 17-self-attested-log | leak | 1 | — | — | known-boundary |
| 18-review-two-tokens-one-person | leak | 0 | — | — | known-boundary |

汇总（信封 `data`）：`attacks=18 / blocked=16 / leaked=0 /
known_boundaries=2 / boundaries_closed=0 / all_blocked=true`。

## 复现

```
PATH="/c/Program Files/Tesseract-OCR:$PATH" \
PYTHONDONTWRITEBYTECODE=1 \
VECTORCRAFT_CLI="<portable>/vectorcraft-cli.exe" \
python scripts/redteam.py --json
```

需要 Python 3.12 + 内置渲染器即可；`01-fake-ai` 到 `18-*` 都不依赖外部
引擎，`base_run` 用 `--force-builtin` 生成 `preview.png`。
