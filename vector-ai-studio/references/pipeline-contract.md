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

退出码：`0` 成功 / `1` 判据或校验不通过（业务失败，非崩溃）/ `2` 用法或畸形输入（显式报错，不猜测语义）/ `3` 依赖 MISSING 已降级（不得静默成功）/ `4` 内部异常或真机 COM 失败。`degradation` 仅在降级时出现。

## 数据契约（全部机器可读 JSON）

| 文件 | schema | 生产者 | 关键字段 |
|---|---|---|---|
| spec.json | vector-ai-studio/spec-v1 | `../scripts/freeze_spec.py` | elements / attributes / palette / rounds |
| deps.json | vector-ai-studio/deps-v1 | `../scripts/detect_deps.py` | dependencies[] / missing[] / credential_audit / summary |
| judge.json | vector-ai-studio/judge-v1 | `../scripts/llm_judge.py` | items / stats / degraded / readable |
| score.json | vector-ai-studio/score-v1 | `../scripts/score.py` | scores{A..F} / failed_dims / passed_dims / signals |
| revision.json | vector-ai-studio/revision-v1 | `../scripts/iterate.py` | change_only / must_keep / decision / K_max |
| batch.json | vector-ai-studio/batch-v1 | `../scripts/batch_check.py` | F / f_verified / per_run[] / aggregate |
| preview.meta.json | vector-ai-studio/preview-meta-v1 | `../scripts/render_png.py` | renderer / degraded / note / width / height / fidelity（full 或 faithful 或 limited；缺字段按 limited 处理） |
| structure_readback.json | vector-ai-studio/structure-readback-v1 | `../scripts/land_ai.py` | raster / placed / paths / artboard_matches_viewBox / ok |
| landing.json | vector-ai-studio/landing-v1 | `../scripts/land_ai.py` | automation / status / degradation / ai_produced |
| acceptance.json | vector-ai-studio/acceptance-v1 | `../scripts/aggregate_log.py` | headline(首行) / grade / scores / rounds / hard_gates / degradation |
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

## JSX / COM 落地与结构回读

`../scripts/land_ai.py` 运行时替换 `../scripts/ai_bridge.jsx` 的四个占位符：`__SVG_PATH__` / `__OUT_PATH__` / `__OUTLINE_TEXT__` / `__RESULT_PATH__`（第四个供半自动回读收集）。经 `../scripts/ai_bridge.ps1` 的 COM 通道执行固定序列：打开 → 结构断言 → `saveAs()` 原生 `.ai` → 关闭 → 重开重读计数。返回 `OK|...|raster=0|placed=0|artboard=WxH|viewBox=...` 或显式 `ERROR:`。第四个占位符是为半自动路径兑现 H1 而加的实现扩展，不改判据。保存选项与自动路径同源——两条分支都传 `_resolve_options()` 的结果，因此 `--profile` / `--embed-icc` 等对半自动同样生效（r28 修正：此前半自动分支漏传，固定落回 screen 默认值）。

## L0–L5 → 脚本行为映射

| 级别 | 触发 | 脚本行为 |
|---|---|---|
| L0 | 常态 | 全部脚本按默认链路执行 |
| L1 | 分档未达标 | `../scripts/iterate.py` 出 `stop_exhausted`，acceptance.json 首行标注需人工终审 |
| L2 | 宿主读图不可用 | `../scripts/llm_judge.py` 标 capability_missing，`../scripts/score.py` 令 A/B = N/A (capability_missing)（状态枚举非分数），`../scripts/aggregate_log.py` 机械归「不达标」、强制 degraded / needs_human_review / reason=capability_missing，四件产物照常落盘 |
| L3 | 用户提供凭据 | 默认链路不受影响，不隐式调用 |
| L4 | COM / 渲染器缺失 | `../scripts/detect_deps.py` 显式 `MISSING`；`../scripts/land_ai.py` 交付 SVG + PNG；半自动路径标 `automation: semi-manual` |
| L5 | 无风格参考 | `../scripts/score.py` 令 E = N/A，不计入总分 |

## detect_deps MISSING 表

| 项 | 缺失时的替代 |
|---|---|
| Illustrator COM | L4：H1 不达标，仅 SVG + PNG；或显式半自动 JSX 路径 |
| PNG 渲染器全部 | 内置最小 SVG→PNG 兜底（真实 PNG，`renderer: builtin-fallback`，`degraded: true`） |
| 宿主读图 | L2（detect_deps 列 MISSING + 替代）：A/B = N/A (capability_missing)，C/D/F 与 H1–H4 照常计分，人工终审，不伪造分数 |
| Node / svgo | `../scripts/sanitize_svg.py` 内置最小实现 + warning |
| 风格参考 | E = N/A（受限能力） |
| 付费凭据 | 默认链路零凭据，不启用 L3 |

## 幂等与重入口径

- 判定类 JSON spec.json / score.json / judge.json / revision.json / acceptance.json / deps.json 不含 wall-clock，固定键顺序，同一输入两次逐字节一致。
- 写文件用临时文件加原子覆盖（os.replace），重入结果不变；时间只进 run.log。
- 原生 .ai 二进制字节级一致由 Illustrator 决定，不承诺；幂等断言施加于 JSON 日志与结构计数。
- **舍入统一 6 位小数**（v1.4 新增）。规则是：内部计算全程保留全精度，只在写出 JSON 时 `round(x, 6)`；禁止在中间步骤提前舍入——提前舍入会让误差沿链条累积，且不同调用顺序会得到不同结果。所有分数、比值、色差、对比度均适用此规则。
- **浮点确定性由 A3 守护，不另设重复断言**（v1.4 记）。A3-idempotent-byte-identical 对同一输入跑两遍整条链，逐字节比对 spec.json / final.svg / judge.json / score.json / acceptance.json / preview.png / preview.meta.json 七个产物，比"单独重跑 score.py"更强。该断言的判别力经变异验证：向 score.json 注入一个 set 序字段（字符串哈希随机化会逐进程改变其顺序），A3 立即转红。**曾计划新增 A32 专测确定性，因与 A3 完全重叠而撤除——为已有性质再加一条断言，只会让通过数变大，覆盖面不变。**


## 编排实现登记（按需求 §7.5.1 原编排实现，无编排变更）

- 默认链路 = N 个 run 各自独立生成（严禁共享生成结果）→ 每个 run 内部各自走完至多 K 轮定向收敛（C2 早退，`../scripts/iterate.py`）→ `../scripts/batch_check.py` 从 N 个收敛后的 run 汇总 F → 取 A–E 综合最高且过 H1 的 run 作最终交付文件。K 是每 run 上限，非全局上限。
- **预算上界运行时派生**：generation_budget = F.N × 迭代轮上限（达标档 9、内部更高档 15），由 `../scripts/lib_common.py` 派生，config 与脚本均不硬编码；`../scripts/batch_check.py` 汇总 generation_count，超界报 E-BUDGET-EXCEEDED。
- **O4 数据隔离硬约束**：f_source_runs/run-i/（原始独立生成 + 判定数据）与 c2_revisions/run-i/round- 目录（该 run 自己的收敛轮）物理分目录、按 run 一一对应；C2 收敛产物严禁跨 run 回填 F 统计，禁止同一 SVG 哈希既在 c2_revisions 又被计入 F 统计，违例报 E-F-DATA-ISOLATION；f_data_isolated 必为 true。
- `--quick` 为单 run 显式预览、不参与达标判定：验收日志强制 `f_verified: false`、首行「F 未验证，不宣称达标」、grade 不得为达标，违例报 E-QUICK-UNVERIFIED。
- **阈值一个都不改**；A–F 判据、H1–H4、迭代轮上限全部保持定稿初值。

## 降级与真实性登记（COM / PNG / L2）

- 半自动 COM 兜底：仅在 detect_deps 真机确认 COM 不可驱动且用户显式选择时启用；日志强制 `automation: semi-manual` / degradation.level 为 L4 / `status: degraded`，写明需人工在 Illustrator 内执行一次脚本，禁止宣称全自动。
- 兜底 PNG 计入 H2 的严格条件：真实可解析 PNG（非零字节、魔数 `89 50 4E 47 0D 0A 1A 0A`、可读宽高、IEND），日志标 `renderer: builtin-fallback` / `degraded: true` / 保真度受限；产不出真实 PNG 则 PNG 判缺失、H2 不达标；零字节或占位文件禁止凑数。
- L2 口径：A/B 记 N/A (capability_missing)（禁几何折算，违例 E-FABRICATED-SCORE）；仍按 §7.3 机械比较得出「不达标」、不引入额外档位；强制 `degraded: true` / `needs_human_review: true` / reason=capability_missing、首行「语义 / 构图判读未取证，需人工终审」；四件产物含原生 .ai 照常落盘、交付不阻断；对外说明禁达标 / 优秀 / 完美字样。

## 验收日志强制字段（需求 §7.5 / O4）

`../scripts/aggregate_log.py` 写出的验收日志首行为 headline，并强制含：generation_count / generation_budget（运行时派生）、f_source_runs 列表 / c2_revisions 列表 / f_data_isolated、f_verified、needs_human_review、reason（capability_missing 或 quality_fail 或 none）、六维分数、grade（按 config 机械比较，L2 下必为不达标）、h1 至 h4、rounds 列表 / runs 列表、degraded、capability_missing。L2 下 A、B 必须恰为 N/A (capability_missing) 字符串，不得为数字，否则 E-FABRICATED-SCORE。`--validate-case` 供 fixtures 逐条校验这些字段与四类错误码。

- 第 2 轮扩展字段：mode（full 或 landing-only）、grading_entered（bool）、h2_status（pass 或 fail 或 not_evaluated）、scores_source（score 与 F 的来源、missing 列表）、discarded_scores（L2 覆盖掉的原始 A/B 数字，审计用）。
- 错误码与 fidelity：`E-NO-SCORE`（full 轨缺必需维，REQUIRED_DIMS 为 A、B、C、D、F；E 可选，无参考时合法记 N/A）、`E-EXEMPTION-ABUSE`（跨轨声明滥用）；preview.meta 的 fidelity 三值 full 或 faithful 或 limited，**字段缺失一律按 limited 处理（fail-closed，防止省略字段绕过读图判读闸）**，兜底且非 faithful 时自动转 L2。

## 证据轨道分层（H1 与 H2–H4）

- full 轨（默认）：grading_entered=true，H1–H4 全判，h2 为 bool，grade 为三档之一，scores 五键齐全（L2 下 A/B 为 capability_missing 状态枚举、C/D 为数字、E/F 为 N/A 或数字）。
- landing-only 轨：仅取证 H1；grading_entered=false、h2=null、h2_status=not_evaluated、tier/grade/h3/h4=null，scores 可为空，不进入 §7.3 分档，输出不得出现任何档位词。`../scripts/smoke_ai.py` 加 `--landing-only` 走此轨，默认走 full。双向防滥用：mode=full 却 grading_entered=false，或 mode=landing-only 却 grading_entered=true，均报 E-EXEMPTION-ABUSE。

## D 与 H1 的证据分工

- D 只判**静态 XML**（语义分组数、退化路径、锚点数、禁用元素、位图标记，由 `../scripts/score.py` 从 SVG 文本确定性计算）；真机回读（raster=0 / placed=0 / paths>0 / artboard==viewBox）归 **H1 独占判定**、不重复计入 D，D 的 measured 里 readback 恒为 `delegated_to_H1`（同一证据记两遍会让纯脚本层永远算不出 D）。

## 阈值校准白名单与审批流

- 可校准（仅限几何 / 统计类，需真机样本）：B.whitespace_*、B.centroid_offset_*、C.max_colors_*、C.max_stroke_widths_*、C.group_reuse_excellent、D.anchor_count_excellent、D.min_groups；其余判据定义类（A.*、E.jaccard_*、E.palette_jaccard_excellent、F.N_*、F.h1_pass_rate、F.ad_pass_rate_*、grade.*、iterate.K_max、H1–H4）一律不可校准。
- 审批流：QA 申请（真实样本日志 + 前后值 + 理由 + 样本数）→ 主理人复核批准 → 改 config 并写 `calibration_log`（含裁决引用 `ruling_ref`）→ 重测。收紧可留痕自行；任何放宽必须标 `loosening: true` 且有裁决引用，否则 `../scripts/selftest.py` 判失败。`../scripts/aggregate_log.py` 另做 `after` 与当前值一致性断言（`E-CALIB-MISMATCH`）与真机日志引用断言（`E-CALIB-NO-LOG`）。

## 交付档位与交付清单（v1.1 新增）

land_ai.py 的保存选项全部显式写入 `IllustratorSaveOptions`，不依赖默认值：

| 档位 | pdfCompatible | embedICCProfile | CMYK | 出血 | 按组分层 |
|---|---|---|---|---|---|
| `screen`（默认） | true | false | false | 0 | false |
| `print` | true | true | true（逐对象算术转换） | 3mm + 8 条裁切线 | false |
| `handoff` | true | false | false | 0 | true |

单项覆盖：`--compat` / `--pdf-compatible` / `--embed-icc` / `--cmyk` / `--bleed-mm` / `--layers-from-groups`。

**为什么必须显式**：`pdfCompatible` 官方默认恰好为 `true`、`embedICCProfile` 官方默认恰好为 `false`。第一版传了一个空的 `IllustratorSaveOptions()`，交付看似正常，实际是默认值凑巧对——换一台装了不同版本的机器，行为就会变。

### deliverable.json 的字段契约

与 acceptance.json 分开：验收日志评价"好不好"，交付清单只记录"是什么"。

| 字段 | 含义 |
|---|---|
| `profile` | 本次按哪一档交付 |
| `save_options` | 实际生效的六项保存参数 |
| `observed` | 从文件字节实测：`pdf_compatible`、`has_ai_private`、`device_rgb` / `device_cmyk`、`output_intent` / `iccbased`、`fonts`、`images`、`artboard_pt` / `artboard_mm`、`sha256` |
| `structure` | 结构回读：`open_paths` / `reopen_paths` / `layers` / `reopen_layers` / `artboard` / `viewBox` |
| `jsx_notes` | JSX 每步实际做了什么（如 `cmyk:objects=5:naive_no_icc`、`cropmarks:8`） |
| `warnings` | 必须让客户知道的风险（第三方重存剥离 AIPrivateData、CMYK 未做色彩管理、无出血等） |
| `unverified` | **要求了但没兑现**的项。例如要求转 CMYK 而 PDF 兼容层仍以 `rg` 算子写 RGB，此条会出现在这里，不会被静默略过 |
| `verify_yourself` | 客户可自行执行的校验命令与 sha256 |

### 交付清单的幂等口径

deliverable.json **不保证字节幂等**：它内嵌 `.ai` 的 sha256，而两次落地产出的 `.ai` 字节本就不同（Illustrator 会写入时间戳等元数据）。因此对该文件的正确判据是「测量值正确」，不是「两次运行逐字节相同」。acceptance.json 与 structure_readback.json 仍适用字节幂等。

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

当前可选项：`A30-vectorcraft-chain-h1-h2`、`A31-deliverable-schema-no-drift`——它们依赖 VectorCraft CLI，而 VectorCraft 是本技能的可选引擎。

**跳过任何非可选断言，H3 一律 fail，并在 reason 里指名是哪一条。** 这条比"允许 partial 通过"更重要：可选引擎缺席不该让整台机器判不达标，但任何**守卫**没跑都必须拦下。

## A31 的覆盖范围与真机开关（v1.3 新增）

`A31-deliverable-schema-no-drift` 检查交付清单与它的 schema 是否双向一致（多余键 / 声明缺失 / 类型不符 / 嵌套多余）。它的 **Illustrator 半边默认是合成形状**：直接调 `_deliverable()` 造一份，不需要装 Illustrator。

**这个默认值有代价，必须写明**：合成形状只证明「构造器输出合规」，**不能证明真机链路把输入供全了**。举例：若 JSX 哪天不再回报 `app_version`，真机产物会是 engine_version 为 unknown，而合成形状仍写死 28.3.0——断言照旧绿。

**覆盖开关**：

```
VECTOR_AI_STUDIO_REAL_AI=1   # 该半边改用真机产物
VECTOR_AI_STUDIO_OUT=<dir>   # 去哪里找（默认 ~/.vector-ai-studio/output）
```

行为：设了开关就在输出根下（最多 200 个候选，按 mtime 从新到旧）找真机产物。**挑选依据不是 engine 字段**——那是 A31 要守护的字段之一，用它筛选会让「engine 缺失」这个回归把检查自己关掉：候选被排除 → 退化为合成 → 合成必然合规 → 绿。改用同目录 structure_readback.json 的 source 是否为 illustrator-saveAs-reopen 作为独立信号；选中后再正常比 schema，engine 缺失就会自己变红。

**三种结局都会写进 detail**：

| detail | 含义 | 判据 |
|---|---|---|
| ai(synthetic) | 未要求真机 | 正常比对 |
| ai(real:run-dir) | 用上了真机产物 | 正常比对 |
| ai(real requested, not found under root) | **要求了真机但没找到** | **判红** |

最后一行是关键：**只要不是绿**。一句「not found」的标签挂在一盏绿灯下，比错的结论更难被发现。

**发布前必须至少跑一次 `VECTOR_AI_STUDIO_REAL_AI=1`**，否则这份发布就没有真机覆盖。日常自检不设该变量，保持 30 秒级。

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

WCAG 2.1 SC 1.4.11 的规范原文限定为 "parts of graphics required to understand the content"，并豁免纯装饰图形；Understanding 文档另明确 2.999:1 不达标、不得四舍五入。因此：

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
