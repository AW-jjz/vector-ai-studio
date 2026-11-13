# 错误码登记

本文件是**唯一登记处**。`scripts/` 下任何脚本里出现的 `E-*` 字面量都必须在此表内，反之本表也不得留有已不存在的码——这条约束由 selftest.py 的 A28 断言强制（它同时比对「代码里有但表里没有」与「表里有但代码里没有」两个方向）。

为什么要这张表：错误码散落在 12 个脚本里就地构造时，没人能回答「这个码还有没有人发」「两个码是不是同一个意思」。同一个失败用两个码表示，等于把 grep 变成猜谜。

| 错误码 | 含义 | 发出方 |
|---|---|---|
| `E-ATTACK-PASSED` | 随包攻击集里有 `expect=block` 的攻击没有被拦下（漏拦） | redteam |
| `E-BATCH-INCOMPLETE` | 独立 run 数量不足，无法汇总 F | batch_check |
| `E-BITMAP` | SVG 含位图或内联 base64，不是纯矢量 | sanitize_svg |
| `E-BUDGET-EXCEEDED` | generation_count 超过运行时派生的预算上界 | aggregate_log, batch_check |
| `E-C-PALETTE-OVERFLOW` | 颜色数超过 C 维阈值 | score |
| `E-CALIB-DATASET` | 校准集 schema/字段/方向/样本非法，或文件不可读 | calibration_bench, thresholds_guard |
| `E-CALIB-DIRECTION` | 阈值变化方向与 `calibration_direction` 不符，或区间变化同时放宽一端、收紧另一端 | thresholds_guard |
| `E-CALIB-FIELD` | 文档没有 field，或 field 在配置里不存在 | thresholds_guard |
| `E-CALIB-HOLDOUT-THIN` | 留出集不足（每类至少 2 个样本） | calibration_bench |
| `E-CALIB-LOCKED` | 试图校准锁定判据（`A.*` / `grade.*` / H1–H4 等），收紧也不允许 | thresholds_guard |
| `E-CALIB-LOOSENING` | 阈值放宽缺少裁决引用 | aggregate_log, thresholds_guard |
| `E-CALIB-MISMATCH` | calibration_log 的 after 与当前配置不一致 | aggregate_log, thresholds_guard |
| `E-CALIB-NO-BENCH` | 放宽没有附校准集；只有文书、没有留出集证据 | thresholds_guard |
| `E-CALIB-NO-GAIN` | 校准集上 fit BER 变差，或 holdout BER 没有严格改善 | calibration_bench, thresholds_guard |
| `E-CALIB-NO-HOLDOUT` | 校准集没有 holdout 样本，无法检验泛化 | calibration_bench |
| `E-CALIB-NO-LOG` | 校准记录缺少真实样本日志引用，或引用文件不存在/为空 | aggregate_log, thresholds_guard |
| `E-CALIB-NOOP` | before 与 after 相同，没有可晋升的变化 | thresholds_guard |
| `E-CALIB-NOT-WHITELISTED` | 字段不匹配任何 `calibratable` 白名单模式 | thresholds_guard |
| `E-CALIB-PROVENANCE` | 证据来源缺失/非法，或放宽时不是外部真人证据 | thresholds_guard |
| `E-CALIB-RULING` | 裁决引用不是 `ruling:<ID>`、注册表里找不到/重复、决定不是 approve、字段不在范围内、缺理由或证据文件不存在 | thresholds_guard |
| `E-CALIB-SAMPLE` | `sample_n` 不是正整数 | thresholds_guard |
| `E-CALIB-STALE` | 提案的 before 不等于当前值；entry 的 after 不等于当前值 | thresholds_guard |
| `E-CONFIG-KEYS` | 配置键缺失或存在多余键 | lib_common |
| `E-CONFIG-MISSING` | 配置文件不存在 | lib_common |
| `E-CONFIG-PARSE` | 配置文件无法解析 | lib_common |
| `E-CONTRACT-MISSING` | 契约文件 `../contract/contract.json` 不存在 | gen_contract |
| `E-CONTRACT-PARSE` | 契约文件无法解析为 JSON | gen_contract |
| `E-CONTRACT-SCHEMA` | 契约文件缺少必需键或 schema 不符 | gen_contract |
| `E-CLAIMS-DRIFT` | 对外声明登记册与文档对不上：原文哈希变了（改了文档没改契约）、anchor 找不到、assertion 类没挂测试、boundary 类没挂反向证明、或承诺句登记覆盖率低于契约里声明的下限 | validate_claims |
| `E-CONTRACT-DRIFT` | 契约生成物（`../scripts/_contract.py` / schema / 文档 / 夹具）与 `../contract/contract.json` 不一致，或 thresholds.json 缺少契约声明的文案键；必须重跑 `python scripts/gen_contract.py` | gen_contract |
| `E-CONFORMANCE` | 需求符合度的机械检查未通过（颜色、色面积、描边宽度、图元种类/数量、留白、禁用构造）；逐条列出失败 id 与实测值，退出 1 | conformance |
| `E-DEGENERATE` | 存在退化路径（零长度/零面积） | sanitize_svg |
| `E-EMPTY-SPEC` | spec 为空或缺少必需项 | freeze_spec |
| `E-EXEMPTION-ABUSE` | mode 与 grading_entered 组合非法（跨轨滥用豁免） | aggregate_log |
| `E-ENGINE-DRIFT` | 引擎二进制与 `../engine.lock.json` 里钉的哈希不符（同版本、不同构建）：引擎是产物的事实来源，构建换了就等于事实变了，必须重压一遍引擎回归并更新锁文件后才允许继续 | trace_ref, land_vc |
| `E-F-DATA-ISOLATION` | F 统计与 C2 收敛产物跨 run 混用 | aggregate_log, batch_check |
| `E-FABRICATED-SCORE` | L2 下出现数字分（伪造读图判读） | aggregate_log |
| `E-FIXTURE-MISMATCH` | fixture 的期望值与实际结果不符 | run_fixtures |
| `E-FORBIDDEN` | SVG 含禁用构造（filter / clipPath / use 等） | sanitize_svg |
| `E-GRADE-MISMATCH` | `--validate-case` 时，日志声明的判定字段（grade / tier / headline / status / degraded / needs_human_review / reason / internal_excellent）与 `_derive_grade` 从 evidence 重推的 verdict 逐字段不一致（含 `internal_excellent` 自证）；仅在 full 轨、且已通过所有其它契约检查时作为唯一裁决码，不叠加到更具体的错误码上 | aggregate_log |
| `E-H1-STRUCTURE` | 结构回读失败（raster / placed / paths / 画板与 viewBox）；也用于「没有本次产物」的硬失败：落地命令非零退出或未写出本次 `output/<run-id>/final.ai`；r70 起含 `aggregate_log` 对 `output/<run-id>/final.ai` 的字节级独立复算不满足（`%PDF-` 前缀 / ≥1 `/Type /Page` / 内嵌 `/Subtype /Image` 数与 structure_readback.raster 一致），即「产物字节复算不满足」 | land_vc, land_craft, lib_pdf, smoke_ai, aggregate_log |
| `E-H2-INCOMPLETE` | 四件产物不齐 | aggregate_log |
| `E-INDETERMINATE-FIELDS` | 「未判定」结论的字段不自洽：tier 不是 indeterminate、deliverable_status 非法、或首行出现达标字样；也用于 deliverable_status 取值不在枚举内 | aggregate_log |
| `E-INTERNAL` | 未预期异常（保留原始异常文本） | lib_common |
| `E-JUDGE-SCHEMA` | 判读文件不符合 schema，且重试一次后仍失败 | llm_judge |
| `E-L2-FIELDS` | L2 必须同时置 degraded 与 needs_human_review 为真 | aggregate_log |
| `E-L2-GRADE` | L2 的 grade 必须是「未判定」（capability_missing 是测量缺口，不是质量结论）；只有显式声明 `legacy_grading=true` 的日志才接受旧的两值写法「不达标」 | aggregate_log |
| `E-L2-HEADLINE` | L2 首行必须是需求原文，不得改写 | aggregate_log |
| `E-L2-WORDING` | L2 输出出现达标 / 优秀 / 完美字样 | aggregate_log |
| `E-NO-SCORE` | 受评日志缺少必需维 `{A,B,C,D,F}`；达标日志还须带 `E` | aggregate_log |
| `E-OUTPUT-STALE` | 调用引擎前清不掉上一轮的产物（`output/<run-id>/final.ai`、`output/<run-id>/print.pdf`、`output/<run-id>/preview.png`），拒绝继续：旧文件会让「文件存在」被误当成本轮产出 | land_vc, land_craft |
| `E-PDFX-PROOF` | `../scripts/smoke_ai.py --print` 落到真机的 PDF/X 主文件没有兑现声明：无 PDF/X 版本串、无输出意图、意图对象数不等于 1、仍含 DeviceRGB 算子、CMYK 算子为 0，或 deliverable 的 unverified 非空 | smoke_ai |
| `E-QUICK-UNVERIFIED` | quick 模式（F 未验证）宣称达标 | aggregate_log |
| `E-REGRESSION` | 与 `--prev` 的 passed-item 结构比对发现变化（g ids / 元素数 / 颜色），或 `--prev` 文件缺失跳过比对；仅作为 `warnings` 条目发出（`"E-REGRESSION: 说明"` 形式），不改变退出码，也不单独终止流程 | sanitize_svg |
| `E-RENDERER-MISSING` | 渲染器全部缺失且内置兜底也产不出真实 PNG；或 Pillow 缺失导致 PNG 无法读取，小尺寸三检保持未测量 | render_png, legibility |
| `E-REVIEW-EMPTY` | 盲评表每一个分数格都是空的：空表不是证据，退出 1，不出汇总 | review |
| `E-REVIEW-INVALID` | 盲评表不可用：缺格、非 0–2 整数、case 缺失或重复、表头缺维；r70 起含「同一份表重复提交（`--sheet` 按绝对路径 + 内容 sha256 去重命中）」——重复表不计入评审人数；逐条列出后退出 1，`usable_for_claim=false` | review |
| `E-SCHEMA` | 输入结构不符合 schema；含 deal 历史文件损坏或 schema 不符（此时代码拒绝继续且不覆盖原文件，不做静默重置）、输入文件不存在或 JSON 损坏（lib_common.read_json、schema_check、r71 起 sanitize_svg 的 ENOENT） | aggregate_log, deal, lib_common, run_fixtures, score, sanitize_svg, schema_check |
| `E-STUDIO-STAGE` | studio.py 的某阶段以非零码结束但未带更具体的码（如 score.py 因零分维返回 1）；消息里列出停链的阶段 | studio.py |
| `E-SELFTEST` | 自检断言失败（附失败断言 id 列表） | selftest |
| `E-SEMI-MANUAL-NOT-ALLOWED` | 未确认 COM 不可用却要求半自动路径（已废弃：r45 移除 Adobe/COM 链路，半自动路径不复存在；保留供下游按码分流） | （已废弃） |
| `E-SMOKE-NO-SAMPLE` | 冒烟缺少内置样例 SVG | smoke_ai |
| `E-XML` | SVG XML 解析失败 | sanitize_svg |
| `E-CAPABILITY-MISSING` | 所需引擎不可用（VectorCraft CLI 缺失），显式降级而非静默成功 | export_print, trace_ref, land_craft |
| `E-DELIGHT-EMPTY` | 去光照找不到主体像素（掩膜为空） | delight |
| `E-RENDER` | 矢量转位图失败（引擎返回非零或未产出文件） | export_print |
| `E-TRACE` | 参考图描摹失败（引擎报错或未产出文件） | trace_ref |
| `E-USAGE` | 参数非法（取值越界 / 缺少必填 / 扩展名不支持） | delight, export_print, trace_ref, studio |
| `E-VERIFY-FAILED` | 安装自验（`../scripts/verify_install.py`）有一项未达预期：引擎不可定位 / 版本读不出 / 二进制哈希与 `../engine.lock.json` 不符 / 引擎相关断言未真执行 / 真机落地未产出合格产物；逐项列出。引擎缺席时退 3（无法复测，不是失败） | verify_install |
| `E-JOURNAL-BROKEN` | 证据哈希链不成立：journal 缺失 / 某行不是 JSON / seq 断号 / prev 链断裂 / 条目自哈希不符 | journal, replay |
| `E-ARTIFACT-DRIFT` | 证据工件在入链之后字节变了：当前 sha256 与 journal 记录不符，或重算出的 journal 与随包 journal 不一致 | journal, replay |
| `E-REPLAY-MISMATCH` | 证据重放不一致：对 run 目录副本重跑聚合器，产物与随包验收日志逐字节比对不符（附 JSON 字段级差异清单） | replay |
| `E-WITNESS-MISMATCH` | 见证锚不符：run 的验收日志 / journal 当前哈希与见证记录（签收时写入、提交进仓库的锚）不一致，或该 run_id 无见证记录 | witness, replay |
| `E-FIDELITY-DRIFT` | 保真度能力矩阵与重测结果不一致（改了矩阵没重测），或分类器宣称 faithful 的特征实测 divergent | fidelity_matrix |

## 约定

1. **一个失败只有一个码**。同一条件在不同脚本里必须复用同一个码，不得新增近义码。
2. **码名描述条件，不描述后果**。`E-NO-SCORE` 说明「没有分数」，至于因此不达标，是分档逻辑的事。
3. **新增码必须同时**：加进本表、被某条断言覆盖、并配一条会真失败的反例。只加码不配反例，等于给自己开了个不会被触发的分支。
4. **不删除、只废弃**。已发布的码若不再使用，保留在表内并标注 `（已废弃）`，避免下游按码分流时静默落空。
