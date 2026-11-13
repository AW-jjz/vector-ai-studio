# 施工侧契约（pipeline-contract）

照图施工与照图验收的唯一数据契约。路径基准：`../scripts/lib_common.py` 的 `skill_root()`，禁止依赖 cwd。

## 统一 JSON 信封

所有命令脚本 stdout 均为该信封（默认即 JSON，`--json` 可强制）：

```json
{
  "schema": "vector-ai-studio/v1", "script": "<name>", "run_id": "...",
  "status": "ok | fail | degraded | missing | error", "code": 0,
  "data": {}, "errors": [{"code": "E-XXX", "msg": "..."}], "warnings": [],
  "degradation": {"level": "L2 | L3 | L4 | L5", "reason": "...", "substitute": "..."},
  "artifacts": {}
}
```

退出码：`0` 成功 / `1` 判据或校验不通过（业务失败，非崩溃）/ `2` 用法或畸形输入（显式报错，不猜测语义）/ `3` 依赖 MISSING 已降级（不得静默成功）/ `4` 内部异常或落地引擎失败。`degradation` 仅在降级时出现。

## 数据契约（全部机器可读 JSON）

| 文件 | schema | 生产者 | 关键字段 |
|---|---|---|---|
| spec.json | vector-ai-studio/spec-v1 | `../scripts/freeze_spec.py` | elements / attributes / palette / rounds |
| deps.json | vector-ai-studio/deps-v1 | `../scripts/detect_deps.py` | dependencies[] / missing[] / credential_audit / engine_lock / summary |
| judge.json | vector-ai-studio/judge-v1 | `../scripts/llm_judge.py` | items / stats / degraded / readable |
| score.json | vector-ai-studio/score-v1 | `../scripts/score.py` | scores{A..F} / failed_dims / passed_dims / signals / verdicts[]（id / dim / msg / action） |
| revision.json | vector-ai-studio/revision-v1 | `../scripts/iterate.py` | change_only / must_keep / decision / K_max |
| batch.json | vector-ai-studio/batch-v1 | `../scripts/batch_check.py` | F / f_verified / per_run[] / aggregate |
| preview.meta.json | vector-ai-studio/preview-meta-v1 | `../scripts/render_png.py` | renderer / degraded / note / width / height / fidelity（词表唯一来源 `../contract/contract.json` 的 `enums.fidelity`：`faithful` / `limited`，另有 `unknown` 兜底值；生产侧只写 faithful / limited，缺字段按 limited 处理） |
| structure_readback.json | vector-ai-studio/structure-readback-v1 | `../scripts/land_vc.py`（`../scripts/land_craft.py` 同 schema） | raster / placed / paths / artboard_matches_viewBox / ok |
| landing.json | vector-ai-studio/landing-v1 | `../scripts/land_vc.py`（`../scripts/land_craft.py` 同 schema） | automation / status / degradation / ai_produced |
| studio.json | vector-ai-studio/studio-v1 | `../scripts/studio.py` | status / stages[]（stage / status / code / errors / detail）；单稿编排的阶段流水，不参与评级 |
| key.json | vector-ai-studio/review-key-v1 | `../scripts/studio.py --review-pack` | seed / cases[]（case → source）；盲评映射，只给归档、不给评审人 |
| acceptance.json | vector-ai-studio/acceptance-v1 | `../scripts/aggregate_log.py` | headline(首行) / grade / tier / scores / rounds / hard_gates / degradation / evidence_coverage / deliverable_status / criteria_version / thresholds_sha256 / legacy_grading |
| deal.json | vector-ai-studio/deal-v1 | `../scripts/deal.py` | seed / tones / excluded_from_previous_rounds / directions[]（archetype / palette / composition / why / risk） |
| deal-history.json | vector-ai-studio/deal-history-v1 | `../scripts/deal.py` | rounds[]，只保留最近两轮；损坏或 schema 不符即 `E-SCHEMA` 退出 2，不覆盖原文件 |
| legibility.json | vector-ai-studio/legibility-v1 | `../scripts/legibility.py` | measured（contrast / coverage / coverage_small / components / gray_bands）/ verdicts[] / gates |
| review-summary.json | vector-ai-studio/review-summary-v1 | `../scripts/review.py` | reviewer_kind / reviewers[] / cases[]（per_dim / total_mean / verdict）/ agreement（rate / dissent[]）/ evidence（usable_for_claim / why） |
| selftest.json | vector-ai-studio/selftest-v1 | `../scripts/selftest.py` | assertions[] / chain[] / passed / failed |

`scores` 单项为 `{score, measured, threshold, degraded}`、字符串 N/A 或 L2 状态枚举 N/A (capability_missing)（非分数）。分档是 `../scripts/aggregate_log.py` 按 `../config/thresholds.json` 的数值比较，LLM 不得宣布；L2 下 A/B 非 ≥1，机械得出不达标。

## 轮次目录与四件产物

```
output/<run-id>/spec.json
output/<run-id>/round-1/draft.svg sanitized.svg preview.png judge.json score.json revision.json
output/<run-id>/run-1/ ... run-N/        # F 项：N 个独立单轮 run
output/<run-id>/final.ai
output/<run-id>/final.svg
output/<run-id>/preview.png
output/<run-id>/acceptance.json
output/<run-id>/deps.json
output/<run-id>/run.log                 # append-only，唯一允许含 wall-clock 的文件
```

run-id = sha256(prompt + spec + thresholds.version)[:12]。H2 四件齐全断言：final.ai + final.svg + 真实可解析 preview.png + acceptance.json。

## 落地为原生 .ai 与结构回读

r45 起整条链只用 VectorCraft，不依赖 Adobe / COM / PowerShell。`../scripts/land_vc.py` 执行 `vectorcraft-cli run --in <sanitized.svg> [--cmd file.documentColorMode …] --export output/<run-id>/final.ai [--export output/<run-id>/final.pdf]`，再执行 `vectorcraft-cli info` 重新打开产物读回 kinds / artboards；最后由 `../scripts/lib_pdf.py` 直接解析文件字节（对象流、页面内容流、字体、图像、/OCGs）。H1 = `images=0` 且 `paths>0` 且画板与 viewBox 一致（±1pt）且结构回读的 ok 字段为真。`../scripts/land_craft.py` 是印刷母版兄弟：同一引擎，追加 PDF/X、裁切线、出血、TrimBox / BleedBox 实测。

两个脚本都不把「文件存在」当成「本次产出」：**调用引擎之前**先清掉目标产物（`output/<run-id>/final.ai`、`output/<run-id>/print.pdf`、`output/<run-id>/preview.png` 及其 meta），这样「文件在」只代表本轮写过——CLI 退出 0 却没落盘这种静默失败不会被上一轮文件顶替。落地命令必须退出 0，`output/<run-id>/final.ai` 非空，`output/<run-id>/structure_readback.json` 的 `source` 为 `vectorcraft-info`（land_vc）或 `vectorcraft-cli info (reopen)`（land_craft）。任何失败出口（源 SVG 缺失、CLI 缺失、CLI 无法执行、导出非零、未写出 .ai）都先删上一轮的 `output/<run-id>/structure_readback.json` 与 `output/<run-id>/preview.png`（连同它的 meta），再写 `status: fail` 或 `degraded` 的 `output/<run-id>/landing.json`；旧读数删不掉时把它改写为 `ok: false`，宁可让 H1 判失败，也不让上一轮的 pass 留下来。预览同理：H2 只判 PNG 能否解析，留下上一版的图会让「本轮什么都没渲染」看起来四件齐全，所以失败出口必须把图清掉。`output/<run-id>/landing.json` 记 `stale_readback_removed` 与 `stale_preview_removed`（两个布尔）。CLI 缺失是 L4 降级：只交 SVG + PNG，并把安装方式或 `VECTORCRAFT_CLI` 写进 `substitute`，绝不把 SVG 改后缀冒充 `.ai`。

## L0–L5 → 脚本行为映射

| 级别 | 触发 | 脚本行为 |
|---|---|---|
| L0 | 常态 | 全部脚本按默认链路执行 |
| L1 | 分档未达标 | `../scripts/iterate.py` 出 `stop_exhausted`，acceptance.json 首行标注需人工终审 |
| L2 | 宿主读图不可用 | `../scripts/llm_judge.py` 标 capability_missing，`../scripts/score.py` 令 A/B = N/A (capability_missing)（状态枚举非分数），`../scripts/aggregate_log.py` 机械归「不达标」、强制 degraded / needs_human_review / reason=capability_missing，四件产物照常落盘 |
| L3 | 用户提供凭据 | 默认链路不受影响，不隐式调用 |
| L4 | VectorCraft CLI / 渲染器缺失 | `../scripts/detect_deps.py` 显式 `MISSING`；`../scripts/land_vc.py` 交付 SVG + PNG，`automation: none`，H1 不达标 |
| L5 | 无风格参考 | `../scripts/score.py` 令 E = N/A，不计入总分 |

## detect_deps MISSING 表

| 项 | 缺失时的替代 |
|---|---|
| VectorCraft CLI 缺失 | L4：H1 不达标，仅 SVG + PNG；安装引擎或设 `VECTORCRAFT_CLI` |
| PNG 渲染器全部 | 内置最小 SVG→PNG 兜底（真实 PNG，`renderer: builtin-fallback`，`degraded: true`） |
| 宿主读图 | L2（detect_deps 列 MISSING + 替代）：A/B = N/A (capability_missing)，C/D/F 与 H1–H4 照常计分，人工终审，不伪造分数 |
| 风格参考 | E = N/A（受限能力） |
| 付费凭据 | 默认链路零凭据，不启用 L3 |

## 幂等与重入口径

- 判定类 JSON spec.json / score.json / judge.json / revision.json / acceptance.json / deps.json 不含 wall-clock，固定键顺序，同一输入两次逐字节一致。
- 写文件用临时文件加原子覆盖（os.replace），重入结果不变；时间只进 run.log。
- 原生 .ai 二进制字节级一致由 VectorCraft 决定（内含时间戳等元数据），不承诺；幂等断言施加于 JSON 日志与结构计数。
- **舍入统一 6 位小数**（v1.4 新增）。规则是：内部计算全程保留全精度，只在写出 JSON 时 `round(x, 6)`；禁止在中间步骤提前舍入——提前舍入会让误差沿链条累积，且不同调用顺序会得到不同结果。所有分数、比值、色差、对比度均适用此规则。
- **浮点确定性由 A3 守护，不另设重复断言**（v1.4 记）。A3-idempotent-byte-identical 对同一输入跑两遍整条链，逐字节比对 spec.json / final.svg / judge.json / score.json / acceptance.json / preview.png / preview.meta.json 七个产物，比"单独重跑 score.py"更强。该断言的判别力经变异验证：向 score.json 注入一个 set 序字段（字符串哈希随机化会逐进程改变其顺序），A3 立即转红。**曾计划新增 A32 专测确定性，因与 A3 完全重叠而撤除——为已有性质再加一条断言，只会让通过数变大，覆盖面不变。**


## 编排实现登记（按需求 §7.5.1 原编排实现，无编排变更）

- 默认链路 = N 个 run 各自独立生成（严禁共享生成结果）→ 每个 run 内部各自走完至多 K 轮定向收敛（C2 早退，`../scripts/iterate.py`）→ `../scripts/batch_check.py` 从 N 个收敛后的 run 汇总 F → 取 A–E 综合最高且过 H1 的 run 作最终交付文件。K 是每 run 上限，非全局上限。
- **预算上界运行时派生**：generation_budget = F.N × 迭代轮上限（达标档 9、内部更高档 15），由 `../scripts/lib_common.py` 派生，config 与脚本均不硬编码；`../scripts/batch_check.py` 汇总 generation_count，超界报 E-BUDGET-EXCEEDED。
- **O4 数据隔离硬约束**：f_source_runs/run-i/（原始独立生成 + 判定数据）与 c2_revisions/run-i/round- 目录（该 run 自己的收敛轮）物理分目录、按 run 一一对应；C2 收敛产物严禁跨 run 回填 F 统计，禁止同一 SVG 哈希既在 c2_revisions 又被计入 F 统计，违例报 E-F-DATA-ISOLATION；f_data_isolated 必为 true。
- `--quick` 为单 run 显式预览、不参与达标判定：验收日志强制 `f_verified: false`、首行「F 未验证，不宣称达标」、grade 不得为达标，违例报 E-QUICK-UNVERIFIED。
- **阈值一个都不改**；A–F 判据、H1–H4、迭代轮上限全部保持定稿初值。

## 降级与真实性登记（引擎 / PNG / L2）

- 只在 `../scripts/detect_deps.py` 真机确认 `vectorcraft_cli` 为 `FOUND` 时才进入落地；`MISSING` 时 `../scripts/land_vc.py` 把降级级别写为 L4、`status: degraded`、`automation: none`，只交净化 SVG 与 PNG。不存在人工半自动兜底路径（r45 移除 Adobe/COM），也不允许把 `.ai` 后缀贴到 SVG 上冒充。
- 兜底 PNG 计入 H2 的严格条件：真实可解析 PNG（非零字节、魔数 `89 50 4E 47 0D 0A 1A 0A`、可读宽高、IEND），日志标 `renderer: builtin-fallback` / `degraded: true` / 保真度受限；产不出真实 PNG 则 PNG 判缺失、H2 不达标；零字节或占位文件禁止凑数。
- L2 口径：A/B 记 N/A (capability_missing)（禁几何折算，违例 E-FABRICATED-SCORE）；仍按 §7.3 机械比较得出「不达标」、不引入额外档位；强制 `degraded: true` / `needs_human_review: true` / reason=capability_missing、首行「语义 / 构图判读未取证，需人工终审」；四件产物含原生 .ai 照常落盘、交付不阻断；对外说明禁达标 / 优秀 / 完美字样。

## 验收日志强制字段（需求 §7.5 / O4）

`../scripts/aggregate_log.py` 写出的验收日志首行为 headline，并强制含：generation_count / generation_budget（运行时派生）、f_source_runs 列表 / c2_revisions 列表 / f_data_isolated、f_verified、needs_human_review、reason（capability_missing 或 quality_fail 或 none）、六维分数、grade（按 config 机械比较，L2 下必为不达标）、h1 至 h4、rounds 列表 / runs 列表、degraded、capability_missing。L2 下 A、B 必须恰为 N/A (capability_missing) 字符串，不得为数字，否则 E-FABRICATED-SCORE。`--validate-case` 供 fixtures 逐条校验这些字段与四类错误码。

- 第 2 轮扩展字段：mode（full 或 landing-only）、grading_entered（bool）、h2_status（pass 或 fail 或 not_evaluated）、scores_source（score 与 F 的来源、missing 列表）、discarded_scores（L2 覆盖掉的原始 A/B 数字，审计用）。
- 错误码与 fidelity：`E-NO-SCORE`（full 轨缺必需维，REQUIRED_DIMS 为 A、B、C、D、F；E 可选，无参考时合法记 N/A）、`E-EXEMPTION-ABUSE`（跨轨声明滥用）；preview.meta 的 fidelity 三值 full 或 faithful 或 limited，**字段缺失一律按 limited 处理（fail-closed，防止省略字段绕过读图判读闸）**，兜底且非 faithful 时自动转 L2。
- **r70 #1 fidelity fail-closed（判据与渲染器身份解耦）**：A/B 语义 / 构图判读只在 `fidelity=="faithful"` 且 `judge.readable is True` 时放行。`output/<run-id>/preview.meta.json` 缺失 / `renderer=unknown` / `renderer=resvg` 但 `fidelity=limited` / 无 `fidelity` 字段，一律 L2 降级（`capability_missing=true` / `degraded=true` / A、B 记 N/A）；`H2.fidelity` 不记 `full`。**r72 起保真度词表只有一份**：`../contract/contract.json` 的 `enums.fidelity` 是唯一书写处，`../scripts/gen_contract.py` 生成给生产侧与判据侧共用的 `../scripts/_contract.py`，`full` 这个词已从代码里消失；改词表必须改契约文件并重新生成（`python ../scripts/gen_contract.py`，CI 用 `--check` 卡漂移）。删 meta 不再能翻绿。
- **r70 #5 grade 单一函数复算**：`_derive_grade(evidence, th)` 是 grade / tier / headline / status / degraded / needs_human_review / reason / internal_excellent 八个判定字段的**唯一实现**，`main()` 与 `--validate-case` 校验器都调用它。校验器从证据逐字段重推并比对（含 `internal_excellent`），不一致报 `E-GRADE-MISMATCH`；landing-only 轨（grade/tier=null）豁免。档位赋值在 `../scripts/aggregate_log.py` 中只出现一次（A77 元断言护栏）。

## 三值判决与证据覆盖（r72 / 判据 2.0.0）

`../scripts/aggregate_log.py` 的判决是三值，不是两值。`grade` 的词取自 `../config/thresholds.json` 的 `messages`（达标 / 不达标 / 未判定）；机器档位 `tier` 是契约枚举 `pass` / `not-pass` / `indeterminate`（`../contract/contract.json` 的 `enums.tier`）：

| `grade` | `tier` | 判定 |
|---|---|---|
| 达标 | `pass` | 必需维（A/B/C/D/F）全部**测过**且无一失败，H1–H4 全过，F 已验证 |
| 不达标 | `not-pass` | 某维**测过**且失败，或硬门明确为假（`.ai` 字节与回读对不上、自检日志损坏、凭据链路异常） |
| 未判定 | `indeterminate` | 链路完好但必需维**没测到**：L2、F 未验证、quick、四件产物不齐、自检证据缺失 |

- **没测 ≠ 质量差**：L2（`capability_missing`）记「未判定」，不再记「不达标」（校验器 `E-L2-GRADE` 强制，`--two-valued` 例外）；同样地，未被评估的硬门不是失败的门——「没测」与「测了没过」从这一版起是两个不同的结论。
- **两个新字段由证据重推**（已进 `_VERDICT_FIELDS`，声明值与重推不一致报 `E-GRADE-MISMATCH`）：`evidence_coverage`（每个必需维 `{measured, why, fix}` + `coverage` 比值 + `required_dims`——哪几维测过、哪几维没测、为什么、怎么修）与 `deliverable_status`（`deliverable` / `deliverable_with_human_review` / `not_deliverable` / `evidence_only`——文件能不能交，与质量结论分开回答）。
- **未判定的护栏**：`grade=未判定` 必须带 `tier=indeterminate`，不得自称 `deliverable_status=deliverable`，headline 不得含达标词（`E-INDETERMINATE-FIELDS`）；未知的 `deliverable_status` 取值同样拒绝。
- **判据版本随日志走**：`acceptance.json` 记 `criteria_version`（当前 2.0.0）与 `thresholds_sha256`——结论只在该判据版本与该阈值文件下有效。旧日志的处理与下游脚本改法见 `MIGRATION.md`。
- **兼容开关**：`--two-valued` 把「未判定」折回旧措辞（日志标 `legacy_grading: true`），达标 / 不达标的判定边界一个都不变。这是给暂时读不懂三值的下游留的措辞开关，不是免重算的通行证——新字段照常写入。
- **窄轨防洗白**：目录里已有 `score.json` / `judge.json` / `selftest.json` / `batch.json` 任何一件时，`--landing-only` 直接 `E-EXEMPTION-ABUSE`——窄轨是给跑不动完整链的机器留的取证通道，不是把完整 run 的判决藏起来的后门。

## 证据轨道分层（H1 与 H2–H4）

- full 轨（默认）：grading_entered=true，H1–H4 全判，h2 为 bool，grade 为三值之一（达标 / 不达标 / 未判定，见下节），scores 五键齐全（L2 下 A/B 为 capability_missing 状态枚举、C/D 为数字、E/F 为 N/A 或数字）。
- landing-only 轨：仅取证 H1；grading_entered=false、h2=null、h2_status=not_evaluated、tier/grade/h3/h4=null，scores 可为空，不进入 §7.3 分档，输出不得出现任何档位词。`../scripts/smoke_ai.py` 加 `--landing-only` 走此轨，默认走 full。`--print` 额外落一份真实 PDF/X-1a 主文件并核对 print 段（PDF/X 版本串、output_intent≥1、output_intent_objects=1、无 DeviceRGB 算子、CMYK 算子>0、unverified 为空），任一不符报 E-PDFX-PROOF。双向防滥用：mode=full 却 grading_entered=false，或 mode=landing-only 却 grading_entered=true，均报 E-EXEMPTION-ABUSE。

## D 与 H1 的证据分工

- D 只判**静态 XML**（语义分组数、退化路径、锚点数、禁用元素、位图标记，由 `../scripts/score.py` 从 SVG 文本确定性计算）；真机回读（raster=0 / placed=0 / paths>0 / artboard==viewBox）归 **H1 独占判定**、不重复计入 D，D 的 measured 里 readback 恒为 `delegated_to_H1`（同一证据记两遍会让纯脚本层永远算不出 D）。
- **r70 #2 H1 独占判定新增产物字节复算**：`../scripts/lib_pdf.py` 的 `ai_h1_probe()` 纯字节读取器（`%PDF-` 前缀、≥1 `/Type /Page`、`/Subtype /Image` 计数与 structure_readback.raster 比对）以 AND 叠加于既有 H1 判据，两轨都生效；任一不符 `h1=false` / `h1_status=fail` / 报 `E-H1-STRUCTURE`。**幂等边界**：写入 `output/<run-id>/acceptance.json` 的仅 hard_gates.H1.probe 的布尔 / 小整数，绝不写 `output/<run-id>/final.ai` 的 sha256 / `/CreationDate` / `/ModDate` 等随落地变化的量——`output/<run-id>/acceptance.json` 仍保证同 run-dir 连跑两次逐字节一致（A18/自检固化报告 §3 第 10 条）。`output/<run-id>/deliverable.json` 内嵌 sha256，本就不保证字节幂等。

## 阈值校准白名单与审批流

- 可校准（仅限几何 / 统计类，需真机样本）：B.whitespace_*、B.centroid_offset_*、C.max_colors_*、C.max_stroke_widths_*、C.group_reuse_excellent、D.short_segment_ratio_max、D.tangent_reversal_max、D.min_groups；其余判据定义类（A.*、E.jaccard_*、E.palette_jaccard_excellent、F.N_*、F.h1_pass_rate、F.ad_pass_rate_*、grade.*、iterate.K_max、H1–H4）一律不可校准。
- 审批流：QA 申请（真实样本日志 + 前后值 + 理由 + 样本数）→ 主理人复核批准 → 改 config 并写 `calibration_log`（含裁决引用 `ruling_ref`）→ 重测。收紧可留痕自行；任何放宽必须标 `loosening: true` 且有裁决引用，否则 `../scripts/selftest.py` 判失败。`../scripts/aggregate_log.py` 另做 `after` 与当前值一致性断言（`E-CALIB-MISMATCH`）与真机日志引用断言（`E-CALIB-NO-LOG`）。

## 交付档位与交付清单（v1.1 新增）

`../scripts/land_vc.py` 的档位是**请求**，兑现与否由 `../scripts/lib_pdf.py` 实测后写进交付清单：

| 档位 | 转 CMYK | 另出 `output/<run-id>/final.pdf` | 请求 ICC / 输出意图 | 图层 |
|---|---|---|---|---|
| `screen`（默认） | 否 | 否 | 否 | 按源 group |
| `print` | 是（`file.documentColorMode`） | 是 | 是 | 按源 group |
| `handoff` | 否 | 否 | 否 | 按源 group |

印刷母版另由 `../scripts/land_craft.py` 提供：`--pdf-standard pdfX1a|pdfX3|pdfX4`、`--bleed-mm`、裁切 / 套准标记。

**为什么仍要逐项记录请求**：`save_options` 记请求、`observed` 记实测，两者分开才能发现「要求了但没兑现」（进 `unverified`）。r45 起不依赖任何桌面程序的默认保存选项，因此不存在「默认值凑巧对」这类随安装环境漂移的问题；实际风险变成引擎版本差异，靠清单里的 `engine_version` 与实测字段暴露。

### deliverable.json 的字段契约

与 acceptance.json 分开：验收日志评价"好不好"，交付清单只记录"是什么"。

| 字段 | 含义 |
|---|---|
| `profile` | 本次按哪一档交付 |
| `save_options` | 本次**请求**的保存参数（不是实测值；实测值在 observed）。唯一例外 `output_intent`：读得到 print.pdf 的 `/OutputConditionIdentifier` 时它是读回值，请求值另存 `output_intent_requested` |
| `observed` | 从文件字节实测：`pdf_compatible`、`has_ai_private`（= `has_aiprivatedata1_marker`）、`device_rgb` / `device_cmyk`、`output_intent` / `iccbased` / `output_condition`（`output_intent` 只数意图对象，`/OutputIntents` 目录数组不计；两条落地脚本共用同一个计数函数）、`fonts` / `fonts_embedded`（嵌入字体程序数，一个 Type0 字体计 1 次，不按 `/BaseFont` 条目重复计）、`images`、`content_ops`（只数页面 `/Contents` 引用与 `/Subtype /Form` 流；ICC / 字体 / CMap 二进制不计入，内联图像 `BI…ID…EI` 之间的像素字节也不计入；两条落地脚本同一口径）、`content_streams`（`objects` 文件里**实际找到**的页面流数 / `refs` 页面 `/Contents` **引用**数 / `decoded` 实际解码并计过数的流数，未压缩内容流算解码成功；`refs > objects` 说明有引用指向读取器没解开的对象；`decoded < objects` 说明有流未测；**`decoded` 为 0 时 `content_ops` 写 null，表示未测，不写 0**——0 会被读成「测过，没有颜色算子」）、`artboard_pt` / `artboard_mm`、`sha256`；vectorcraft 另写 `print_pdf`（GTS_PDFXVersion / output_intent / output_intent_objects / 各 Box 计数与矩形 / 同一份 `content_ops`）、`paths`、`artboard_matches_viewBox` |
| `structure` | 结构回读：`open_paths` / `reopen_paths` / `layers` / `reopen_layers` / `artboard` / `viewBox` |
| `engine_notes` | 引擎每步实际做了什么；VectorCraft 路径留空数组（无此回报） |
| `warnings` | 必须让客户知道的风险（第三方重存剥离 AIPrivateData、CMYK 未做色彩管理、无出血等） |
| `tool_warnings` | 工具链自己看到的提示，保留写入方的写法：`land_vc` 收导出引擎各步的告警（同一条只留一次，按首次出现顺序），`lib_pdf` 收 PDF 读取器的条目（每条带 `pdf reader: ` 前缀，如对象流 `/N` 不符）。与 `warnings` 分工不同——这栏是"工具看到了什么"，`warnings` 是"客户需要知道什么"；`land_vc` 目前不转发读取器那一条（见下） |
| `unverified` | **要求了但没兑现**的项。例如要求转 CMYK 而 PDF 兼容层仍以 `rg` 算子写 RGB，此条会出现在这里，不会被静默略过 |
| `verify_yourself` | 客户可自行执行的校验命令与 sha256 |

流记「未测」的已知情形——**只有一条会留下提示**：对象流 `/N` 与实际配对数不符时写一条 `pdf reader: …` 到 `deliverable.tool_warnings`（不是顶层 `warnings`）；`land_vc` 调 `_observe_ai` 时不传收集列表，该提示在 vectorcraft 引擎下落不下来。其余情形只能从 `decoded < objects`（有的流没测）或 `content_ops: null`（整页都没测）看出来：部分未测只看前者，整页未测只看后者；`decoded` 为 0 且 `objects ≥ 1` 时两个条件同时成立。

| 情形 | 表现 |
|---|---|
| 内联图像字典算不出样本长度（带 `/F`、`/DP`，或索引色空间） | 该流不计入 `decoded`，无提示 |
| 1/2/4 bpc 的两种字节数（整体位流 / 行对齐）**都**落在合法 `EI` 上，无法判别 | 同上 |
| 内联图像声明的样本长度越过流尾，找不到端点 | 同上 |
| 页面内容流声明的滤镜**全部**未实现（LZW / ASCII85 / DCT 等） | 同上（`_decode_stream` 返回 None，不猜解压） |
| 声明了 Flate 但解压失败，或流边界缺失（无 `endstream`、仅 CR 换行） | 同上 |
| 对象流 `/N` 与实际配对数不符 | 按实际对数读取，并写一条 `tool_warnings` |

这张表不是穷举：凡是「本读取器无法确定边界或解码」的输入，一律走同一条兜底——整条流记未测，宁可交白卷，不给一个猜出来的数。

**一个已知的口径例外**：滤镜链里同时含 Flate 和未实现的滤镜（如 `[/FlateDecode /DCTDecode]`）时，`_decode_stream` 只认 Flate、忽略其余名字并照常计数——这类输入会给出数值而不是未测。属实现取舍，不改判据，列在这里以免被当成"已覆盖所有滤镜"。

### 交付清单的幂等口径

deliverable.json **不保证字节幂等**：它内嵌 `.ai` 的 sha256，而两次落地产出的 `.ai` 字节本就不同（VectorCraft 会写入时间戳等元数据）。因此对该文件的正确判据是「测量值正确」，不是「两次运行逐字节相同」。acceptance.json 与 structure_readback.json 仍适用字节幂等。

### 结果契约的机器可读定义

schemas/acceptance.schema.json 与 deliverable.schema.json（draft 2020-12）。
用 `../scripts/schema_check.py` 校验，零依赖子集实现；遇到不认识的 schema 关键字会报错而非静默忽略。

### 错误码

全部错误码登记在 `error-codes.md`，由 `../scripts/selftest.py` 的 A28 断言双向强制：代码里有的必须在表里，表里的必须还在代码里。

## 自检三态与 H3 的口径（v1.2 新增）

`selftest` 的每条断言有三个取值，不是两个：

| `ok` | 含义 | 计入 |
|---|---|---|
| `true` | 已评估且通过 | `passed` |
| `false` | 已评估且失败 | `failed` |
| `null` | **未评估**——依赖的能力不在本机 | `skipped`，并让运行标 `partial`、退出码 `3` |

对齐既有的 `h2=null` + `h2_status=not_evaluated`：同一个项目对「未评估」只能有一种表达。

### H3 判定（显式策略，不藏在计数公式里）

`aggregate_log` 的 H3 通过条件：

1. `passed + failed + skipped == assertions 总数`（**计入 skipped**——旧公式 `passed + failed == all` 写于没有 skip 的年代，一旦有跳过就恒不成立，与状态无关地关死闸）
2. `failed == 0` 且 `passed >= 1`
3. 状态为 `ok`（无跳过）**或** `partial` 且**每一个跳过的断言都在 `OPTIONAL_ASSERTIONS` 里**

当前可选项以 `../scripts/lib_common.py` 的 `L.CAPABILITY_SKIPS` 为唯一词表（当前：`A30-vectorcraft-chain-h1-h2`、`A31-deliverable-schema-no-drift`、`A42-delight-flattens-lighting`、`A43-export-print-dimensions`、`A44-trace-ref-contract`、`A45-uniform-error-envelope`、`A46-craft-only-landing`、`A53-legibility-fails-what-the-eye-misses`）——全部依赖可选引擎或可选外部库，VectorCraft 是其中之一。登记条件见该常量的注释：把守卫加进白名单让它变绿，守卫就成了摆设。

**跳过任何非可选断言，H3 一律 fail，并在 reason 里指名是哪一条。** 这条比"允许 partial 通过"更重要：可选引擎缺席不该让整台机器判不达标，但任何**守卫**没跑都必须拦下。

### H3 证据强度与 run-id 绑定（r70 / #3 新增）

- **白名单单一来源**：可跳过断言集合取自 `../scripts/lib_common.py` 的 `L.CAPABILITY_SKIPS`，`../scripts/selftest.py` 与 `../scripts/aggregate_log.py` 都从 L 引用。被校验日志自带的 `capability_skips` 字段降级为展示 / 审计，不再作为判据输入（否则日志可自证哪些守卫「可选」）；与 L 不一致时只输出一条 warning 候选信号，最终裁决归人。
- **断言总数下界**：`len(assertions) >= L.EXPECTED_ASSERTIONS`（当前 89，随断言增减同步）。手写「1/1 ok」的伪日志直接 H3 fail。判定用 `>=` 而非 `==`，兼容 `--fixtures` 时多出 A13 的正常形态。
- **绑定**：`selftest.json.run_id == aggregate_log 的 --run-id`，且 `--run-id` 非空（空则 fail-closed）。两者均为内容派生（`L.sha_run_id`，输入 = spec / run 内容 + `thresholds.version`），**不是** `datetime.now()` / 随机 nonce / 签名——时间与随机值会破坏判定 JSON 逐字节幂等，签名密钥会引入凭据。编排三处（`../scripts/studio.py` 的 `content_run_id()`、`SKILL.md` 步 11、`本节`）显式把**同一个**值同时传给 `../scripts/selftest.py` 与 `../scripts/aggregate_log.py`；只改校验不改编排会让所有正常日志 H3 全红。
- **边界（必须写明，不得夸大）**：`output/<run-id>/` 是生成方可写的、`run_id` 公开可推导，因此**拦不住「同一次 run 内生成方自写一份 run_id 匹配的假 selftest.json」**。那属威胁模型外边界。同 run 内自证靠 #2（产物字节复算）+ #5（grade 单一函数复算）+ 断言总数下界共同收紧，而不是靠绑定谎称「不可伪造」。
- H3 fail 通过 `hard_gates.H3.pass=false` + `h3_status=fail` + exit 1 表达，不新增错误码。

## A31 的覆盖范围与真机开关（r45 修订）

`A31-deliverable-schema-no-drift` 检查交付清单与 schema 是否双向一致（多余键 / 声明缺失 / 类型不符 / 嵌套多余）。

r45 删掉了拟合成的 Illustrator 半边：它只证明「构造器输出合规」，**不能证明真机链路把输入供全了**——若模板哪天不再回报 `app_version`，合成形状仍写死 28.3.0，断言照旧绿。现在唯一会出货的清单由 `../scripts/land_vc.py` 真跑产生，直接对它比对。

`VECTOR_AI_STUDIO_REAL_AI=1` 的含义随之变成「要求这台机器上必须有 VectorCraft CLI 并跑真的落地」：

| 结局 | 判据 |
|---|---|
| CLI 在，真机清单双向一致 | 绿 |
| CLI 在，比对不一致 | 红 |
| CLI 不在，未设开关 | skip（可选引擎缺席，H3 允许） |
| CLI 不在，设了开关 | 红（要求了真机却没跑成） |

最后一行是关键：一句「skipped」挂在一盏绿灯下，比错的结论更难被发现。**发布前必须至少跑一次 `VECTOR_AI_STUDIO_REAL_AI=1`**；日常自检不设该变量，保持快跑。

## 感知色彩层与标签传播（v1.4 新增）

`../scripts/perceptual.py` 提供四个算子，全部为公开公式或公开查表，无第三方依赖：WCAG 2.x 对比度、Machado 等 (2009) 色觉缺陷模拟、CIEDE2000、HyAB。

### 三个测量互不替代

| 问题 | 算子 | 理由 |
|---|---|---|
| 两个色是不是同一个 | CIEDE2000 < 2.3 | 小色差场景，ΔE00 的拟合区间就在这里 |
| 声明要区分的色是否拉开 | HyAB < 10 | 大色差场景，此时 ΔE00 会失真（Abasi/Amani Tehran/Fairchild 2020） |
| 前景背景能否读 | WCAG 对比度，四种观察者取最小 | 见下 |

用同一个算子兼判"是否重复"和"是否明显不同"是方法论错误：CIEDE2000 是为小色差拟合的，在大色差区间会压缩差异。

### 对比度的作用域刻意收窄

WCAG 2.1 SC 1.4.11（2.2 沿用同编号同阈值，检索日 2026-11-11）的规范原文限定为 "parts of graphics required to understand the content"，并豁免纯装饰图形；Understanding 文档另明确 2.999:1 不达标、不得四舍五入。因此：

- 只有 spec 显式声明 `contrast_pairs` 时才检查；未声明一律记 `not_applicable`，**绝不显示为通过**。
- `role` 三态严格校验：`text`（走 SC 1.4.3，正文 4.5 / 大字 3.0）、`meaning`（走 SC 1.4.11，3:1）、`decorative`（豁免，但仍记录实测比值，便于复核豁免是否正当）。未知 role 直接报 E-EMPTY-SPEC，**不得降级成 meaning**——降级会让标准明文豁免的图形开始被判失败。
- 对整份调色板套 4.5 是误用标准：插画里低对比配色是正当表达，全量套用会把正常作品判死，然后生成端会学会"永远灰底黑字"。

### 色觉缺陷层是本项目扩展，非标准

WCAG 不要求模拟色觉缺陷。保留这一层是因为"正常视觉达标、色盲几乎看不见"的配色大量存在：纯红 `#FF0000` 在黑底上 WCAG 判 5.25 通过 AA，红色盲视角只有 1.26。`cvd_contrast_floor` 因此在配置里标注"无标准依据、未标定"。

### 本轮只测量，不判失败

`duplicate_delta_e_2000_max`、`distinct_hyab_min`、`cvd_contrast_floor` 三个阈值均无权威出处（配置 `_src_*` 已逐条写明）。未标定的数字当闸门，等于把噪声点成红灯。三个测量的结果进 `scores.C.measured.perceptual`，配套文案进顶层 `perceptual_warnings`，**均不参与 `c_pass`**。等 24 个 fixture 的真实分布出来再定切点。

### 参考向量自检

`../scripts/perceptual.py` 内置：Sharma 等 (2005) 的 34 组 CIEDE2000（最大误差须 < 1e-4）、4 个 WCAG 对比度锚点、2 个色觉缺陷**未舍入**锚点、9 个矩阵行和（每行必须为 1，白色映射到白色）。由 A33 复跑。

**锚点不能舍入到 0-255 再比。** 第一版就是这么写的，容差 ±1/通道；变异测试证明它形同虚设：把矩阵系数改动 0.0001，输出只变 0.0255 个单位，四舍五入后仍是同一个整数，断言照绿。改为比较未舍入浮点（容差 1e-4）并加行和约束后，同一变异立刻被两条独立信号抓住（锚点偏 0.0255、行和 1.00010100）。**改矩阵必须同时改锚点，这是设计意图，不是不便。**

A34 则把整条链路钉住：以 `#FF0000` 作前景、`#000000` 作背景、role=meaning 真跑一次 `../scripts/score.py`，要求 `normal > 4.5` 且 `min < 3.0` 且 status=warn。这两个条件同时成立，只可能是模拟层真的在工作。

## 判决路径的负向夹具与评审边界（r70 / #8 / #4）

- **r70 #8 判定精确化**：`../scripts/run_fixtures.py` 从「子集检查」改为 `got_codes == want_codes` 精确匹配（去重后比较；期望外的 `E-*` 错误码、warnings 前缀都不得算通过）。`fixtures-v1` case 增加两个可选能力：`setup_dir`（相对 `@F@` 的目录，用 `shutil.copytree` 物化完整 run 目录到 `@OUT@/<case-id>/`）与占位符 `@RUN@`（args 里替换为该路径）；以及可选 `want_acceptance`（dotted path → 期望字面值），用于把「L2 降级 / H3 fail」这类不以 `E-*` 错误码表达的判决精确锚到 `output/<run-id>/acceptance.json` 字段，而非仅凭 exit≠0。
- **4 条 `--run-dir` 负向夹具**（`tests/fixtures/runs/`）：① 22 字节假 `output/<run-id>/final.ai` → `E-H1-STRUCTURE` / `H1.pass=false`；② 删 `output/<run-id>/preview.meta.json` → `capability_missing=true` / `degraded=true` / 不达标；③ 1 条断言且 `run_id` 跨 run 不匹配的 `output/<run-id>/selftest.json` → `H3.pass=false`；④ `structure_readback.raster=0` 但 `output/<run-id>/final.ai` 内嵌 `/Subtype /Image` → `E-H1-STRUCTURE` / `H1.pass=false`。第 4 条**不是**「grade 与 scores 矛盾」——`--run-dir` 路径 grade 当场算，无此输入；矛盾一族（V1/V2/V3/V7/V8/V9）归 #5 的 `--validate-case`。`cases_expected` 24 → 34（#5 的 6 条 + #8 的 4 条），由 A13 / A78 元断言守护 ≥3 条 run-dir case。
- **r70 #4 评审去重的诚实边界**：`../scripts/review.py` 按（绝对路径 + 内容 sha256）去重，重复表报 `E-REVIEW-INVALID` 且不计入评审人数，`usable_for_claim` 按去重后人数 ≥2 判定。该机制**拦得住**「同一份表重复提交」「内容相同、文件名不同」，但**拦不住**「同一个人把同一份表另存为两个不同文件并改动一个字节」——两个人还是一个人，脚本无从判定。这是字段级 fail-closed 设计 + 已声明威胁模型 + 已登记边界，**不构成「两名不同真人已看过」的对外证据，也不宣称不可伪造**。
