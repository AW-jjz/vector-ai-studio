# 契约表（自动生成，勿手改）

> 来源：`contract/contract.json`（契约版本 1.0.0，判据版本 2.0.0）
> 重新生成：`python scripts/gen_contract.py`；CI 用 `--check` 卡漂移。

## 枚举

| 枚举 | 取值 | 说明 |
|---|---|---|
| `degradation_level` | `L1` / `L2` / `L3` / `L4` / `L5` |  |
| `fidelity` | `faithful` / `limited` / `unknown` | 预览保真度。r71 的 P0-1 就在这里：生产侧写 full、判据侧只认 faithful、六处三种说法、零处校验。现在只有一个词表、一个接受集合，full 这个词不再存在于代码里。 |
| `h2_status` | `pass` / `fail` / `not_evaluated` |  |
| `mode` | `full` / `landing-only` |  |
| `reason` | `none` / `quality_fail` / `capability_missing` / `insufficient_evidence` |  |
| `status` | `ok` / `degraded` / `fail` / `error` |  |
| `tier` | `pass` / `not-pass` / `indeterminate` | 机器可读档位。indeterminate = 未判定：我们没能力测，不等于不达标。 |

## 保真度：谁能写、谁接受

| 生产者 | 场景 | 写入值 |
|---|---|---|
| `land_vc` | preview_failed | `limited` |
| `land_vc` | vectorcraft_preview_ok | `faithful` |
| `render_png` | builtin_other | `limited` |
| `render_png` | builtin_rect_only | `faithful` |
| `render_png` | real_renderer | `faithful` |

| 消费者 | 维 | 接受的值 |
|---|---|---|
| `aggregate_log` | A | `faithful` |
| `aggregate_log` | B | `faithful` |

缺字段 / 未知值一律按 fail-closed 默认值 `limited` 处理。

## 判决字段

| 字段 | 说明 |
|---|---|
| tier `pass` | grade 取 `grade_pass` 对应的文案 |
| tier `fail` | grade 取 `grade_fail` 对应的文案 |
| tier `indeterminate` | grade 取 `grade_indeterminate` 对应的文案 |
| 必需维 | `A` / `B` / `C` / `D` / `F` |
| 可选维 | `E` |
| 禁止自证字段 | `grade` / `tier` / `headline` / `status` / `degraded` / `needs_human_review` / `reason` / `internal_excellent` / `evidence_coverage` / `deliverable_status` |

判据版本 2.0.0 起，判决是三值的：`pass` / `fail` / `indeterminate`。`indeterminate`（未判定）表示**没能力测**，不等于**不达标**。
