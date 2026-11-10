# 错误码登记

本文件是**唯一登记处**。`scripts/` 下任何脚本里出现的 `E-*` 字面量都必须在此表内，反之本表也不得留有已不存在的码——这条约束由 selftest.py 的 A28 断言强制（它同时比对「代码里有但表里没有」与「表里有但代码里没有」两个方向）。

为什么要这张表：错误码散落在 12 个脚本里就地构造时，没人能回答「这个码还有没有人发」「两个码是不是同一个意思」。同一个失败用两个码表示，等于把 grep 变成猜谜。

| 错误码 | 含义 | 发出方 |
|---|---|---|
| `E-BATCH-INCOMPLETE` | 独立 run 数量不足，无法汇总 F | batch_check |
| `E-BITMAP` | SVG 含位图或内联 base64，不是纯矢量 | sanitize_svg |
| `E-BUDGET-EXCEEDED` | generation_count 超过运行时派生的预算上界 | aggregate_log, batch_check |
| `E-C-PALETTE-OVERFLOW` | 颜色数超过 C 维阈值 | score |
| `E-CALIB-LOOSENING` | 阈值放宽缺少裁决引用 | aggregate_log |
| `E-CALIB-MISMATCH` | calibration_log 的 after 与当前配置不一致 | aggregate_log |
| `E-CALIB-NO-LOG` | 校准记录缺少真实样本日志引用 | aggregate_log |
| `E-CONFIG-KEYS` | 配置键缺失或存在多余键 | lib_common |
| `E-CONFIG-MISSING` | 配置文件不存在 | lib_common |
| `E-CONFIG-PARSE` | 配置文件无法解析 | lib_common |
| `E-DEGENERATE` | 存在退化路径（零长度/零面积） | sanitize_svg |
| `E-EMPTY-SPEC` | spec 为空或缺少必需项 | freeze_spec |
| `E-EXEMPTION-ABUSE` | mode 与 grading_entered 组合非法（跨轨滥用豁免） | aggregate_log |
| `E-F-DATA-ISOLATION` | F 统计与 C2 收敛产物跨 run 混用 | aggregate_log, batch_check |
| `E-FABRICATED-SCORE` | L2 下出现数字分（伪造读图判读） | aggregate_log |
| `E-FIXTURE-MISMATCH` | fixture 的期望值与实际结果不符 | run_fixtures |
| `E-FORBIDDEN` | SVG 含禁用构造（filter / clipPath / use 等） | sanitize_svg |
| `E-H1-STRUCTURE` | 结构回读失败（raster / placed / paths / 画板与 viewBox）；也用于三条「没有本次产物」的硬失败：自动路径暂存区无 `.ai`，半自动 `OK` 行路径不是本次 run 的 `output/<run-id>/final.ai`，半自动 `output/<run-id>/manual-result.txt` 首行不是 `OK|`（含 0 字节） | land_ai, smoke_ai |
| `E-H2-INCOMPLETE` | 四件产物不齐 | aggregate_log |
| `E-INTERNAL` | 未预期异常（保留原始异常文本） | lib_common |
| `E-JUDGE-SCHEMA` | 判读文件不符合 schema，且重试一次后仍失败 | llm_judge |
| `E-L2-FIELDS` | L2 必须同时置 degraded 与 needs_human_review 为真 | aggregate_log |
| `E-L2-GRADE` | L2 的 grade 必须为不达标（无第三档） | aggregate_log |
| `E-L2-HEADLINE` | L2 首行必须是需求原文，不得改写 | aggregate_log |
| `E-L2-WORDING` | L2 输出出现达标 / 优秀 / 完美字样 | aggregate_log |
| `E-NO-SCORE` | 受评日志缺少必需维 `{A,B,C,D,F}`；达标日志还须带 `E` | aggregate_log |
| `E-QUICK-UNVERIFIED` | quick 模式（F 未验证）宣称达标 | aggregate_log |
| `E-RENDERER-MISSING` | 渲染器全部缺失且内置兜底也产不出真实 PNG | render_png |
| `E-SCHEMA` | 输入结构不符合 schema | aggregate_log, lib_common, run_fixtures, score |
| `E-SELFTEST` | 自检断言失败（附失败断言 id 列表） | selftest |
| `E-SEMI-MANUAL-NOT-ALLOWED` | 未确认 COM 不可用却要求半自动路径 | land_ai |
| `E-SMOKE-NO-SAMPLE` | 冒烟缺少内置样例 SVG | smoke_ai |
| `E-XML` | SVG XML 解析失败 | sanitize_svg |
| `E-CAPABILITY-MISSING` | 所需引擎不可用（VectorCraft CLI 缺失），显式降级而非静默成功 | export_print, trace_ref |
| `E-DELIGHT-EMPTY` | 去光照找不到主体像素（掩膜为空） | delight |
| `E-RENDER` | 矢量转位图失败（引擎返回非零或未产出文件） | export_print |
| `E-TRACE` | 参考图描摹失败（引擎报错或未产出文件） | trace_ref |
| `E-USAGE` | 参数非法（取值越界 / 缺少必填 / 扩展名不支持） | delight, export_print, trace_ref |

## 约定

1. **一个失败只有一个码**。同一条件在不同脚本里必须复用同一个码，不得新增近义码。
2. **码名描述条件，不描述后果**。`E-NO-SCORE` 说明「没有分数」，至于因此不达标，是分档逻辑的事。
3. **新增码必须同时**：加进本表、被某条断言覆盖、并配一条会真失败的反例。只加码不配反例，等于给自己开了个不会被触发的分支。
4. **不删除、只废弃**。已发布的码若不再使用，保留在表内并标注 `（已废弃）`，避免下游按码分流时静默落空。
