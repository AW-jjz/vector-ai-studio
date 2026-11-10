# 评审状态（01-bookshop）

- 证据等级：machine_only —— 三个独立模型实例，不是外部评审。
- 盲评表：**不空**（3 份 × 15 格已填），归档在 04-QA/test-log-r51/machine-review/。
- 对外可用：**false**。review.py 的 usable_for_claim 只在「≥2 名 external + 零空格」时为真。

## 机器评审读数（r51）

| 维度 | 均分 | 极差 |
|---|---|---|
| 概念 | 1.000 | 0 |
| 层级 | 2.000 | 0 |
| 比例 | 1.000 | 0 |
| 细节 | 1.000 | 0 |
| 耐久 | 1.000 | 0 |

总分 6.000 / 10，conditional。三人逐格完全一致（无分歧格）。

## 需求符合度（conformance.json，机械可判）

| 硬约束 | 实测 | 判定 |
|---|---|---|
| 240×240 方形画板，成品可编辑矢量 | viewBox 0 0 240 240；4 个原生矢量元素 | 通过 |
| 红色只做点缀，面积 ≤8% | 朱红折角面积占比 1.39% | 通过 |
| 单色时结构仍可辨认 | 全墨黑重渲染后 legibility 判 OK：对比度 18.42，16px 覆盖率 0.406，组件 3→2（折角与书页合并，两页+基线仍在） | 通过 |
| 不要写实书本、渐变、投影 | 无 image / gradient；纯几何 | 通过 |
| 不含文字、不模拟衬线字形 | 无 text 元素 | 通过 |

以上由 examples/01-bookshop/conformance.json 与 scripts/conformance.py 判定，23 项全部通过；复跑一条命令即可核对。素材账本只有一条 own 记录：全部构件自绘，无外部素材入图。`LICENSE-SOURCE` 与 `LICENSE-VERIFIED` 在本例无适用条目（非 own 条目为零），属「未触发」而非「已验证」；这两条检查的失败路径由 selftest A58 单独覆盖。

判不了的：气质是否「安静、克制」、主次是否成立、折角是否算「可爱」——这三条只能人判。

## 还缺什么

两名真人 × 15 分钟。请求件在 templates/review-request.md，盲评包是 05-发布/vector-ai-studio_盲评包-r52.zip（16 件、不含 key.json）。
回收后跑 review.py，reviewer-kind 取 external，usable_for_claim 才可能变真。
