---
name: vector-ai-studio
description: 把一句自然语言需求变成按可断言判据达到质量阈值、可继续编辑的原生 .ai 矢量成品；用户说生成矢量图 / logo / 图标 / 插画 / 要可继续编辑 / 要原生 .ai / 装好就能用时触发；只要位图 / 照片级真实感 / 3D、已持有 SVG 仅需转格式、或纯格式考据与可行性调研时不触发。
license: MIT
compatibility: "Requires Python 3.10+; VectorCraft CLI for native .ai; optional PNG renderer and host image reading."
metadata:
  author: skill-lifecycle-studio
  version: 1.27.0
  description_zh: 当用户要求生成可编辑矢量图、原生 .ai 文件或 Illustrator 插画时使用；也覆盖需要批量同风格矢量素材、或用户自带风格参考要求品牌一致性的场景。不适用于只需要位图或照片级真实感、3D、像素级修图、把已有 SVG 仅做格式转换，以及纯格式考据与可行性调研。
  description_en: Use when the user wants an editable vector graphic, a native .ai file, or an Illustrator illustration, including batch production of same-style vector assets or brand consistency against a supplied style reference. Do not use it for bitmap or photorealistic output, 3D, pixel retouching, pure format conversion of an existing SVG, or format research and feasibility write-ups.
---

# vector-ai-studio

把一句需求做成按可断言判据给出**可复核质量结论**、可继续编辑的 **VectorCraft PDF 兼容 `.ai`** 矢量成品。第一优先是出图质量闭环，格式落地是底线约束。

状态 A 限定（r70）：引用 `grade=达标` 前须自行复核三项——① `output/<run-id>/final.ai` 是否为真 `%PDF-` 且能重新打开；② `output/<run-id>/structure_readback.json` 的 `paths` 与产物对不对得上；③ `output/<run-id>/selftest.json` 是否为本次运行产生（`run_id` 绑定）。本结论是脚本机械比较的结果，不替代消费方复算，也不宣称审美已被验证或印刷合规。每一轮的判据变化与版本历史见 `CHANGELOG.md`。

## 触发条件

应当触发：

- 用户要生成矢量图 / logo / 图标 / 插画 / 信息图，并明确要「可继续编辑」「原生 `.ai`」成品。
- 用户给出风格参考，要求批量产出风格一致的一组矢量素材。
- 用户说「装好就能用」，要求验证本机能否真机跑通一次完整出图。

不应触发：

- 只要位图 / 照片级真实感 / 3D / 像素级修图。
- 已持有成品 SVG、仅需转换为 `.ai`（属纯格式转换，不启用生成与质量闭环）。
- 纯格式考据、可行性论证、来源对比或尽调报告。
- 本机没有 VectorCraft CLI 且不接受「格式底线未满足」的降级说明。

触发词（矢量图 / logo / 图标 / 插画）仅为演示，规则本身适用于任何纯矢量图形领域，不把能力窄化为某一类图形。

## 环境与零门槛声明

- 本技能按 `marketplace` 上架口径立标（假设）；上架字段仅为静态元数据，任何脚本都不读取它们。以 `local` 方式安装时零门槛、无需联网 / 凭据 / 额外组件，默认链路完全相同。
- 默认链路零付费凭据、零第三方付费服务；商业生成端只在用户自行提供凭据时启用，不隐式调用。
- 一切外部依赖（VectorCraft CLI、PNG 渲染器、宿主 LLM 读图）一律先由 `scripts/detect_deps.py` 探测，显式列出 FOUND / MISSING / UNVERIFIED 与替代方案，不假设已装。

## 工作流程

### 步 0 依赖探测（C5 / H4）

先探测本机，再决定后续分支。缺失项显式列出并走降级，不静默跳过。

```
python scripts/detect_deps.py --json --out path/to/run/deps.json
```

产出 deps.json。凭据环境变量只判断名称是否存在，从不读取值。

### 步 1 冻结必备视觉元素与风格色板（C1）

由宿主 LLM 从用户当次 prompt 逐条写出必备视觉元素清单（≥1 条）、附加属性与风格色板，写成 `templates/spec.template.json` 形状的 JSON，再校验。

```
python scripts/freeze_spec.py --spec path/to/spec.json --validate --out path/to/run/spec.json
```

空 prompt / 空清单 / 畸形 schema 一律显式拒绝并要求补充，不臆造需求。冻结清单与色板全轮次只读。

### 后续步骤导航

- 步 1.5–4.6（选型、发牌、生成、净化、预览、三检与需求符合度）：见 `references/authoring.md`。
- 步 5–11（判读、打分、收敛、F 汇总、落地、验收、盲评与自检）：见 `references/judging.md`。
- 命令速查、结果契约与照片/印刷路线：见 `references/output-patterns.md`。
- verdict-kit 的工程迁移记录：见 `references/maintenance.md`。
- 结构修改后运行 `python scripts/skill_audit.py --require-evals --json` 与 `python scripts/selftest.py --fixtures`；触发评测定义在 `evals/evals.json`。

## 输出规范

五件产物落在运行期目录，**默认在技能包之外**：`~/.vector-ai-studio/output/<run-id>/`，可用环境变量 `VECTOR_AI_STUDIO_OUT` 覆盖。包内不留任何运行期文件，打 zip 不会带出测试残留。

```
~/.vector-ai-studio/output/<run-id>/final.ai
~/.vector-ai-studio/output/<run-id>/final.svg
~/.vector-ai-studio/output/<run-id>/preview.png
~/.vector-ai-studio/output/<run-id>/acceptance.json
~/.vector-ai-studio/output/<run-id>/deliverable.json
```

交付清单 deliverable.json 是**给客户的**，与验收日志分开：它不评价好坏，只记录物件本身的可核事实（色彩空间、ICC、出血、图层、字体、位图、画板 pt/mm、sha256）与必须说明的风险（第三方重存会剥离 AIPrivateData、CMYK 未做色彩管理等）。`observed` 段每一项都是从产物字节实测（含 print.pdf 的 /OutputConditionIdentifier）；`save_options` 段记录本次**请求**的保存参数，两者不混写——唯一例外是 `save_options.output_intent`：没有 PDF 可读回时它记请求值，读得到时它记读回值（请求值另存 `output_intent_requested`），因为「按请求写的」与「文件里真的这么写」是两件事，混在一起就无法区分。`unverified` 字段专门记录「要求了但没兑现」的条目——例如要求转 CMYK 而 PDF 兼容层仍写 RGB 时会出现在这里，而不是被略过。

- final.ai 由 VectorCraft `run --export` 产出、再由 `info` 重开回读计数；final.svg 与它同源；preview.png 为真实可解析 PNG；acceptance.json 含 A–F 各项分数、轮次记录、H1–H4 与降级标记。
- 阈值、分档、降级文案的唯一事实源是 `config/thresholds.json`；`calibration_policy` 登记可校准白名单与审批流，判据定义类一律锁定，任何放宽必须标 `loosening: true` 且带裁决引用。
- **阈值晋升守卫（r79，M5 第一片）**：`scripts/thresholds_guard.py` 把 `calibration_policy` 从声明变成可执行判据——字段必须命中 `calibratable` 且不得命中 `locked`；方向由 `calibration_direction` 的 max/min/range 推出，mislabelled 的 `loosening` 直接拒；`sample_n` 必须是正整数、`real_run_log_ref` 必须指向非空真机日志；放宽还要 `ruling_ref` 与外部真人证据。证据分级 A/B/C（外部/内部/机器）只报告不闸。`scripts/aggregate_log.py` 对每条 `calibration_log` 跑 entry 模式核验（after == 当前值），旧码 `E-CALIB-MISMATCH` / `E-CALIB-NO-LOG` / `E-CALIB-LOOSENING` 保留。
- **校准台（r80，M5 第二片）**：文书不是证据。`scripts/calibration_bench.py` 读一份带 `fit` / `holdout` 划分的校准集（每样本：测得值、人工 pass/fail 标签），把当前阈值与提案阈值分别套到两个 split 上，算**平衡错误率 BER**（假阳率与假阴率的均值；0 完美，0.5 抛硬币）。晋升要求 **fit BER 不变差且 holdout BER 严格改善**——在自己拟合过的样本上变好不算数。**放宽没有校准集直接拒**（`E-CALIB-NO-BENCH`）；标签与数值无关（噪声）或 fit 变好、holdout 变差（过拟合）都拒（`E-CALIB-NO-GAIN`）；无 holdout / 每类不足 2 个 / 字段或方向不符 / 重复 id / `sample_n` 与集合大小不符，分别走 `E-CALIB-NO-HOLDOUT` / `E-CALIB-HOLDOUT-THIN` / `E-CALIB-DATASET` / `E-CALIB-SAMPLE`。样本量只报告不闸：小留出集上的严格增益仍可能是运气，报告会说 n 是多少。
- **裁决记录（r81，M5 收尾）**：`ruling_ref` 不再是自由字符串，必须是 `ruling:<ID>` 并解析到 `config/rulings.json` 里**恰好一条**记录；记录要有 ISO 日期、`approve` 决定、覆盖该字段的 `fields` scope、非空理由，以及能解析到非空文件的 `evidence_refs`。悬空引用、`reject`、越界 scope、重复 ID、缺理由或缺证据都 `E-CALIB-RULING`。`decider` 是自报角色，不是凭证（H4 零凭据不变），仓库历史才是这条记录的锚。
- 判定类 JSON 不含 wall-clock，同一输入两次执行逐字节一致；时间只进 append-only 运行日志。
- 验收日志另含 generation_count / generation_budget（运行时派生）、f_source_runs / c2_revisions / f_data_isolated、f_verified、needs_human_review、reason、h1 至 h4 等强制字段，供 QA 机械核对；另含 `evidence_coverage`（每维测没测 / 为什么 / 怎么修）、`deliverable_status`（能不能交）、`criteria_version` 与 `thresholds_sha256`（结论的适用版本）。
- 对外说明由 `templates/delivery-note.template.md` 生成，只消费分档结论；未判定或降级时首行显式标注需人工终审并给出证据覆盖，禁止宣称达标 / 完美。
- 轮次与批量样本保留在各轮目录，供 F 项与人工复核追溯。

## 降级与边界

- L0 默认零凭据本地链路；L1 质量未达标进入定向收敛；L2 宿主读图不可用时 A/B 记 N/A (capability_missing)（状态枚举、非分数，禁用几何量折算，违例 `E-FABRICATED-SCORE`），**判据版本 2.0.0 起结论记「未判定」而不是「不达标」**（`tier=indeterminate`）——读不了图是对**测量能力**的陈述，不是对**质量**的判决；强制 degraded / needs_human_review / reason 为 capability_missing、首行「语义 / 构图判读未取证，需人工终审」，降级条目带**损失清单**（丢了什么 / 留了什么 / 怎么修，原因由触发条件派生而不是写死）；五件产物含原生 `.ai` 照常落盘、交付不阻断；L3 商业增强需用户自行提供凭据；L4 依赖缺失；L5 无风格参考时 E 记 N/A。
- VectorCraft CLI 缺失：L4 全自动降级，H1 未取证，结论记「未判定」并把 `deliverable_status` 记为 `not_deliverable`，仅交付净化 SVG 与 PNG 并标注「非原生 `.ai`」，`substitute` 写明安装方式或 `VECTORCRAFT_CLI`。不存在半自动兜底路径（r45 起移除 Adobe/COM），也不允许把 `.ai` 后缀贴到 SVG 上冒充。
- **同一次 run 内的自证拦不住**：`output/<run-id>/` 由生成方可写，脚本无法分辨同一 run 内自己写的 `output/<run-id>/selftest.json` 与真跑出来的。H3 的断言数下界与 `run_id` 绑定只能挡跨 run 复用。`scripts/redteam.py` 的 `17-self-attested-log` 就是这条边界的**反向证明**：它必须真的漏过去，否则这条声明就是低估自己。
- **评审身份的两层边界（r78）**：提交按内容 sha256 去重，同一份表换文件名再交一次会被拒（`16-review-duplicate-new-name` 已从「已声明边界」变为「拦下」）；表内 `reviewer_token` 是评审人自报的稳定假名，一个 token 只能提交一份不同内容的表，对外证据要求所有计入表都带 token。**剩余边界**：同一个人编两个 token、写两份不同内容的表，脚本仍无法分辨——零凭据链路里有「提交」的锚，没有「人」的锚。`scripts/redteam.py` 的 `18-review-two-tokens-one-person` 是这条边界的反向证明。

## 注意事项

- 所有脚本以 `scripts/lib_common.py` 的 `skill_root()` 解析路径，配置 / references / fixtures / 运行期目录都相对技能根，禁止依赖调用时 cwd。
- 不伪造 `.ai`、不承诺一次直出即达到阈值；达标由 `scripts/aggregate_log.py` 按配置机械比较裁定。
- 宿主 LLM 只负责语义生成与二值判读，数值 / 集合 / 结构判定全部由确定性脚本产出。
