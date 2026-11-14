# 判据 2.0.0 迁移说明（MIGRATION）

r72 把验收判决从两值改成三值，`criteria_version` 随之升到 **2.0.0**。这是**有意的破坏性变更**，不是措辞调整。写给两类人：解析 `output/<run-id>/acceptance.json` 的下游脚本，和在自己管线里调用 `scripts/aggregate_log.py` 的人。

一句话立场：**别为了兼容把三值判决降回二值；兼容走 `--two-valued`。**

## 1. `grade` 三个取值，不是两个

| `grade` | `tier` | 语义 | 什么时候给出 |
|---|---|---|---|
| 达标 | `pass` | 必需维（A/B/C/D/F）全部**测过**且无一失败，H1–H4 全过，F 已验证 | 证据齐全且测量结果支持 |
| 不达标 | `not-pass` | 某维**测过**且失败，或硬门明确为假（`.ai` 字节与回读对不上、自检日志损坏、凭据链路异常） | 有确定的失败事实 |
| 未判定 | `indeterminate` | 链路完好但必需维**没测到**：A/B 读不了图（L2）、F 未验证、quick 模式、四件产物不齐、自检证据缺失 | 测量能力缺失 |

「没测出来」不等于「质量差」——这是本次变更最重要的一条。原实现把宿主读不了图（`capability_missing`）一律记「不达标」，用户会去改一幅根本没被看过的稿子；现在这类结论记「未判定」，并把原因与修法写进 `evidence_coverage`。`grade` 的三个词取自 `config/thresholds.json` 的 `messages`；`tier` 是契约枚举（`contract/contract.json` 里 `enums` 的 `tier` 子键）。

## 2. 新增字段

| 字段 | 含义 |
|---|---|
| `evidence_coverage` | 每个必需维 `{measured, why, fix}`，另加 `coverage` 比值与 `required_dims`。由证据重推，日志不得自称 |
| `deliverable_status` | 文件能不能交：`deliverable`（可交）/ `deliverable_with_human_review`（交前需人工过目）/ `not_deliverable`（缺原生 `.ai`，不能当矢量成品交）/ `evidence_only`（只作 H1 取证，未进评级）。与 `grade` 分开回答「质量」与「可交性」两个问题 |
| `criteria_version` | 产出该结论的判据版本（当前 2.0.0）。消费方先读它再解读日志 |
| `thresholds_sha256` | 本次聚合所用的 `config/thresholds.json` 哈希。需要钉死判据时与 `criteria_version` 一起核对 |
| `legacy_grading` | 仅当聚合时加了 `--two-valued` 才为 `true`；否则字段为 `false` |

## 3. 校验器跟着收紧

`scripts/aggregate_log.py` 的 `_validate_acceptance` 对 full 轨日志（`mode=full` + `grading_entered=true`）从证据**重推**全部判决字段——`evidence_coverage` 与 `deliverable_status` 也进了这份清单。因此：

- **1.2+ 的旧日志（带 `mode=full`）在 2.0.0 校验下会报 `E-GRADE-MISMATCH`**：旧日志没有 `evidence_coverage` / `deliverable_status`，与重推结果必然不一致。
- 更早（无 `mode` 字段）的样本维持原有单一拒绝码，不叠加新报错。
- 「未判定」有护栏：必须带 `tier=indeterminate`、不得自称 `deliverable_status=deliverable`、headline 不得含达标词（`E-INDETERMINATE-FIELDS`）。
- L2 日志的 `grade` 现在必须是「未判定」（`E-L2-GRADE`），除非日志标了 `legacy_grading: true`。

## 4. 旧日志怎么处理

推荐路径是**重算，不改旧文件**：证据本来就在盘上，r72 只改了判决层。

```
python scripts/aggregate_log.py --run-dir output/<旧run-id> --out output/<旧run-id>/acceptance-2.0.0.json --run-id <与原日志相同的内容派生 id>
```

- run-id 用与原日志相同的内容派生值（sha256(spec + svg + thresholds.version) 前 12 位；`scripts/studio.py` 的 `content_run_id()` 是唯一实现）。
- 输出写新文件名，原始 `output/<run-id>/acceptance.json` 保留作审计痕迹。
- 1.2+ 的 run 目录可直接重算；更早年代的目录若缺 `mode` / `grading_entered`，先按当时的口径补齐证据文件，或按现状重新出图。

`--two-valued` 是**措辞开关**，不是免重算的通行证：它把「未判定」折回旧措辞、日志标 `legacy_grading: true`，**达标 / 不达标的判定边界一个都没变**；但新字段（`evidence_coverage` / `deliverable_status` / `criteria_version` / `thresholds_sha256`）照常写入——所以旧日志本身仍然要重算，开关只解决「下游暂时读不懂第三个值」的问题。

## 5. 下游脚本要怎么改

1. **先读 `criteria_version` 再解读日志。** 判据 2.0.0 下 `grade` 才有三个值；见到 2.0.0 就按本说明处理。
2. **把「未判定」当「需要补证据 / 人工终审」，绝不当失败处理。** 具体缺什么、怎么补，看 `evidence_coverage` 里没测维的 `why` / `fix`；别对它做任何基于「不达标」的自动化（重迭代、告警、打回）。
3. **交付判断用 `deliverable_status`，不要用 `grade` 推断。** 「能不能交」与「达没达标」是两个字段回答的两个问题；`not_deliverable` 时缺的是原生 `.ai`，任何分数都替代不了格式底线。
4. **要措辞稳定的下游匹配 `tier`，不要匹配 `grade` 的中文词。** `tier`（`pass` / `not-pass` / `indeterminate`）是契约枚举，`grade` 的词来自 `config/thresholds.json` 的 `messages`，属于可校准的展示层。
5. **需要钉死判据时同时核对 `criteria_version` 与 `thresholds_sha256`。**
6. 兼容期实在读不懂三值的脚本：调 `scripts/aggregate_log.py` 时加 `--two-valued`，并核对日志里 `legacy_grading: true`；这是过渡措施，尽快改成原生三值处理。

## 6. 自查改对了没有

```
python scripts/selftest.py --json --fixtures        # A86 钉三值判决与兼容旗，A89 钉 claims 的跳过政策
python scripts/aggregate_log.py --run-dir <run> --out <run>/acceptance.json --run-id <id>
python -c "import json; d=json.load(open('acceptance.json',encoding='utf-8')); print(d['criteria_version'], d['grade'], d['deliverable_status'])"
```

重算出的日志再交给原消费方跑一遍；判「未判定」的地方逐条确认是按「缺证据」而不是按「失败」处理的。

## 7. 附：内部迁移进度（verdict-kit，非对外破坏性变更）

这张表是给维护者的：判定工具按分片迁到 vendored `scripts/_vendor/verdict-kit/`，每片都在换实现**之前**冻结 golden。对外契约（CLI flags、信封 schema、退出码、错误码）逐片保持；表中任何一片都不要求下游改代码。

| 分片 | 轮次 | 交给 kit 的东西 | 留在技能里的东西 | 钉住的断言 |
|---|---|---|---|---|
| Phase 0 | r86 | 骨架落盘（envelope / registry / contract / assertions / context） | 全部现有 import 未改 | A106 |
| Phase 1a | r87 / r88 | `lib_common.envelope()` / `emit()` 的信封形状与键序 | schema id、`ToolError`、`run_cli` | A107（13 组字节 parity） |
| Phase 1b | r89 | `scripts/validate_claims.py` 的句子切分、锚点解析、哈希比对、机制校验、覆盖率 | 扫描文件、强制词、断言 id、攻击表、`CAPABILITY_SKIPS` | A108（4 组语义 parity） |
| Phase 2-1 | r90 | `scripts/gen_contract.py` 的契约装载与形状校验、逐字节 `--check`、缺目标即漂移、原子写 | 四个渲染器、必需键清单、`config/thresholds.json` 文案键交叉检查 | A109（7 组 parity：解析后 JSON + status/code） |

迁移期间三条约束照旧：kit 不发明任何取值（词表只有一个来源，即 `contract/contract.json`）；kit 缺能力时补 kit，不让技能侧改值迁就；两侧语义不该合并时停下上报，不硬接。r90 的判定是「kit 能力足够」，因此 `scripts/_vendor/verdict-kit/VENDORED.sha256` 本轮未变。

