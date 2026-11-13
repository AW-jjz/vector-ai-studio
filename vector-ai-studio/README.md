# vector-ai-studio

一句话把需求做成按可断言判据给出**可复核质量结论**、可继续编辑的 **VectorCraft PDF 兼容 `.ai`** 矢量成品（状态 A：引用 `grade=达标` 前须复核真 `%PDF-` 可重开、`output/<run-id>/structure_readback.json` 的 `paths` 与产物对得上、`output/<run-id>/selftest.json` 为本次运行产生；不替代消费方复算，不宣称审美已被验证或印刷合规）。

## 装好就能用

默认链路只依赖 Python 3.10+ 标准库（CI 在 3.10 与 3.12 上跑通；本机实测 3.12.10），零付费凭据、零联网、零额外组件。安装后先跑：

```
python scripts/detect_deps.py --json
python scripts/selftest.py
python scripts/run_fixtures.py
python scripts/redteam.py --json      # 随包攻击集：每个攻击都必须被拦下
```

真机冒烟是独立命令（需要本机 VectorCraft CLI）：

```
python scripts/smoke_ai.py
```

引擎不可用时该命令显式降级并留下真实日志，不会假装成功、不会编造 `.ai`。

还有一条专门验**引擎路径**的命令：

```
python scripts/verify_install.py --json
```

自检里依赖引擎的三条断言在没有引擎时是 *skipped*（没测，不是通过），所以「自检全绿」不代表引擎能用。这条命令复用那三条断言、再真跑一次落地，并把引擎二进制哈希与 `engine.lock.json` 里钉的那一个比对：退出码 0 = 引擎路径已验证，**3 = 引擎缺席（无法复测，不是通过）**，1 = 有项目失败，哈希对不上时报 `E-ENGINE-DRIFT`（同版本、不同构建）。

## 完整出图

主流程见 `SKILL.md`：冻结必备元素清单与风格色板，按 `references/design-language.md` 选型 → 生成结构化纯矢量 SVG → 净化与六维打分 → N 个独立 run 各自内部完成至多 K 轮定向收敛 → 汇总 F → VectorCraft 落地为原生 `.ai` → 聚合验收日志。按需求 §7.5.1 原编排实现，无编排变更。

## 五件产物

- 原生可编辑 `.ai`（由 VectorCraft 产出，附 `info` 重开回读计数）
- 净化后的 `.svg`
- PNG 预览
- 机器可读验收日志（A–F 分数、轮次记录、H1–H4、降级标记）
- 交付清单 deliverable.json（只记录从产物字节实测的事实与未兑现项）

产物落在运行期目录，打包时排除，不随包提交。

## 三例示例的证据状态

随包三例（书本店、咖啡、开发者工具）各有一份 review-status.md（位于 examples 各目录），写清它现在是**什么等级的证据**：

- 当前等级一律是 `machine_only`：三个独立模型实例按同一张盲评表打分，表不空、可复核，但**不是外部评审**，不可对外声称「质量已验证」或「审美已被验证」。
- 需求符合度里机械可判的部分（尺寸、色数、色面积上限、线宽档数、16px 与单色可辨、禁用构造）由 `scripts/conformance.py` 按各例的 conformance.json 判定，逐条写进状态文件；规则文件里没写的项进 `unchecked`，不显示成通过。剩下的审美判断（主张是否成立、主次、气质）只能人判。
- 外部评审表回收后用 `scripts/review.py` 汇总，只有 ≥2 名**去重后**（绝对路径 + 内容 sha256）的不同 external 评审人且零空格时 `evidence.usable_for_claim` 才为真；在此之前恒为假。这是字段级 fail-closed 设计 + 已声明威胁模型 + 已登记边界，不构成「两名不同真人已看过」的对外证据。请求件在 `templates/review-request.md`。

## 素材政策：两条通道，不建库

- **参考通道**：风格、构图、质感参照可以来自任何素材站，但只进 `source-ledger`（URL、许可、获取日期、用途），**不进入** final.svg / .ai。
- **产出通道**：纹理、笔刷、边框等非标志性构件若要用，必须许可明确允许商用、账本登记、SVG 元素带 `data-source`，并由 `scripts/conformance.py` 校验六件事：「条目字段齐全」「有标记必有登记」「有登记必有元素」「许可在白名单」「非 `own` 条目必须有许可声明页 `license_source`」「`license_verified_at` 必须是合法 `YYYY-MM-DD`」。
- **许可写显式名，不写 `commercial`**：用 cc0、mit、cc-by-4.0、ofl-1.1。`commercial` 描述的是用途不是许可，会把需署名与免署名混成一类，审计无法判定。站点清单、核实等级（E1/E2/E3）与失效模式见 `references/sources-whitelist.md`。
- **标识主体不用现成素材**：撞标与「非原创」风险高，且与「可复制、可断言」的证据链冲突。
- **不下载、不建库**：链接与许可会变，库存的是记录而不是资产；账本模板在 `templates/source-ledger.json`。

## 需要什么

- 必需：Python 3.12+ 标准库；VectorCraft CLI（`vectorcraft-cli`，格式底线；可用 `VECTORCRAFT_CLI` 指定路径）。
- 可选增强：Pillow（读图三检与导出打印）、PNG 渲染器（resvg / cairosvg）、宿主读图、风格参考、商业生成端凭据。
- 引擎、渲染器、宿主读图三项一律先探测，缺失显式列出 `MISSING` 与替代方案并降级，不假设已装。不依赖 Adobe / COM。

依赖组合与可达结论（状态 A）：

| 本机依赖 | 预览路径 | 可达结论 |
|---|---|---|
| 引擎 + PNG 渲染器齐全 | 真渲染器预览 | 按 A–F 机械比较，达标可达 |
| 缺 VectorCraft CLI 或缺 Pillow | 无原生 .ai / 无读图 | L4 / L2 降级，恒未判定 |
| **装了引擎但无 PNG 渲染器** | **预览恒内置兜底** | **非全 rect 稿恒 L2 → 本机永远拿不到达标** |

### 依赖组合与验收结论（本机可复核）

| VectorCraft CLI | PNG 渲染器 | 预览与降级 | 能否拿到 `达标` |
|---|---|---|---|
| FOUND | 有真渲染器（resvg / cairosvg） | 真预览，fidelity 由渲染器判定 | 过全部硬门后可达标 |
| 缺 CLI（MISSING）或 Pillow 缺 | — | L4 降级：只交 SVG + PNG，H1 未取证 | 恒未判定 |
| **装了引擎但无 PNG 渲染器** | 全部 MISSING | **预览恒内置兜底**（`renderer: builtin-fallback`）；只有纯 rect 稿被判 `faithful` 能进评级，其余稿恒 L2（A/B = capability_missing） | **非全 rect 稿恒 L2、恒未判定**；全 rect 稿亦需过其余硬门 |

> 第三行是本机常见形态：引擎在、渲染器不在时，内置兜底只会画轴对齐纯色矩形，带圆 / 路径 / 描边 / 文字的稿子一律 fail-closed 到 L2，不会靠「文件存在」凑成达标。

## 结论是三值的：`达标` / `不达标` / `未判定`

判据版本 2.0.0 起，验收日志的 `grade` 有三个值：

- **达标**（`tier=pass`）：必需维全部**测过**且没有一维失败，硬门全过，F 已验证。
- **不达标**（`tier=not-pass`）：某维**测过**且失败，或硬门明确为假（产物字节与回读对不上、自检日志坏了）。
- **未判定**（`tier=indeterminate`）：**没测**。宿主读不了图、F 未验证、quick 模式、四件产物不齐、自检证据缺失——这些是**测量能力**的问题，不是**质量**的问题。

验收日志另给两个字段把话说全：`evidence_coverage` 说清**哪几维测过、哪几维没测、为什么、怎么修**；`deliverable_status` 说清**文件能不能交**（「能不能交」与「质量达没达标」是两个问题）。日志还记 `criteria_version` 与 `thresholds_sha256`：结论只在该判据版本下有效。

下游暂时只能处理两个值时，聚合器加 `--two-valued`：结论折回旧措辞、日志标 `legacy_grading: true`，**达标 / 不达标的判定边界一个都没变**。迁移说明见 `MIGRATION.md`。

## 证据可重放（r73）

聚合时 run 目录里每个文件都被哈希进 `output/<run-id>/journal.jsonl`（验收日志自身也在链里）；拿包的人用 `python scripts/replay.py --run-dir output/<run-id>` 在副本上重跑同一个聚合器、逐字节比对——篡改证据、直改判决、偷换预览都会被重放打回真实结论。不要信任随包日志，重算它。

## 边界

- 不承诺一次直出即达到阈值；达标由验收日志按 `config/thresholds.json` 机械比较裁定。
- 默认零凭据链路下若宿主读不到渲染图，按 L2 交付：A/B 记 capability_missing 状态枚举（非分数）、**grade 记「未判定」**（读不了图不是质量结论）、五件产物照常落盘、不阻断交付。
- 取证分工：H1 由 landing-only 轨取证（smoke_ai.py 加 --landing-only），H2/H3/H4 由完整链（默认）取证；**已经跑过完整链的目录不许再走窄轨**（否则窄轨就成了洗白失败 run 的后门）。
- 不伪造 `.ai`；引擎缺失时明确标注「非原生 `.ai`，格式底线未满足」。
- 路径含中文或空格时，需确认 VectorCraft CLI 能读取该路径；运行期请勿移动源文件。
- 对外说明只承诺分档结论；未判定或降级时首行标注需人工终审。状态 A 限定（r70）：引用 `grade=达标` 前须自行复核三项（真 `%PDF-` 可重开、`output/<run-id>/structure_readback.json` 的 `paths` 与产物对得上、`output/<run-id>/selftest.json` 为本次运行产生）；本结论不替代消费方复算，不宣称印刷合规。
- 用户可见说明仅此一页；`references/design-language.md` 与 `references/archetypes/` 是设计与生成约束，其余 references 是施工契约，都不是使用手册。
- 单稿链路可压成一条命令：`python scripts/studio.py --spec ... --svg ... --out output/<run-id>`；阶段状态见 `output/<run-id>/studio.json`。
- 盲评回收用 `python scripts/review.py --sheet ... --key ... --reviewer-kind external`：空表判 `E-REVIEW-EMPTY`、缺格判 `E-REVIEW-INVALID`；`--sheet` 按（绝对路径 + 内容 sha256）去重，重复提交同一份表也判 `E-REVIEW-INVALID` 且不计入评审人数；只有 ≥2 名**去重后**的外部评审且零空格时 `evidence.usable_for_claim` 才为真。评审请求件在 `templates/review-request.md`。
- 评审去重的**已声明边界**：`(绝对路径, 内容 sha256)` 拦得住「同一份表重复提交」，但**拦不住「同一份表换个文件名再交一次」与「同一个人把同一份表另存为两个不同文件并改动一个字节」**——那是两个人还是一个人，脚本无从判定。这是字段级 fail-closed 设计 + 已声明威胁模型 + 已登记边界，不构成「两名不同真人已看过」的对外证据。`python scripts/redteam.py --json` 里的 `16-review-duplicate-new-name` 就是这条边界的反向证明。
- **同一次 run 内的自证拦不住**：`output/<run-id>/` 由生成方可写，脚本分不清同一 run 内自写的 `output/<run-id>/selftest.json` 与真跑出来的；H3 的断言数下界与 `run_id` 绑定只挡跨 run 复用。反向证明见 red-team 的 `17-self-attested-log`。
- 跨文件的枚举（保真度、档位、判读字段）只在 `contract/contract.json` 定义一次，由 `scripts/gen_contract.py` 生成给生产侧与判据侧共同 import；`python scripts/gen_contract.py --check` 在 CI 里卡漂移。


## 自检说明

`scripts/selftest.py` 是纯脚本层，切换 cwd 后仍可用；`scripts/run_fixtures.py` 跑随包回归样例。两者都不需要 VectorCraft CLI 或渲染器，零凭据即可执行。
