# 交付说明

本技能按可断言判据完成出图，脚本机械比较得出的结论：@@GRADE@@。

> **引用前请自行复核三项（状态 A）**：① `output/<run-id>/final.ai` 是否为真 `%PDF-` 且能重新打开；② `output/<run-id>/structure_readback.json` 的 `paths` 与产物对不对得上；③ `output/<run-id>/selftest.json` 是否为本次运行产生（`run_id` 绑定）。本结论是脚本机械比较的结果，不替代消费方复算；不宣称审美已被验证、印刷合规或「两名不同真人已看过」。

## 四件产物

- VectorCraft PDF 兼容可编辑文件：`@@AI_FILE@@`
- 净化矢量：`@@SVG_FILE@@`
- PNG 预览：`@@PNG_FILE@@`
- 机器可读验收日志：`@@ACCEPTANCE@@`

## 结论与边界

- 对外结论：@@HEADLINE@@
- 证据覆盖：@@COVERAGE@@（✓ = 该维确实测量过；✗ = 没有测量，理由与修法见验收日志的 `evidence_coverage`）
- 降级说明：@@DEGRADATION_NOTE@@
- 各项分数与轮次记录以验收日志为准，分档由脚本按配置机械比较得出，不由人工或模型宣布，也不替代消费方复算。
- 判决是三值的：`pass` / `fail` / `indeterminate`。第三个值表示**证据不足、结论未下**——没测出来不等于质量不合格。它与「能不能交付」是两个独立问题，见验收日志的 `deliverable_status`。

## 复核

如需继续编辑，请在 VectorCraft 中打开原生文件；验收日志中的 `scores` 与 `rounds` 字段记录逐项测量值、阈值与轮次。
