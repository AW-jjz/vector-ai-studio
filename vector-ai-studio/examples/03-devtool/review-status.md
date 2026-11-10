# 评审状态（03-devtool）

- 证据等级：machine_only —— 三个独立模型实例，不是外部评审。
- 盲评表：**不空**（3 份 × 15 格已填），归档在 04-QA/test-log-r51/machine-review/。
- 对外可用：**false**。review.py 的 usable_for_claim 只在「≥2 名 external + 零空格」时为真。

## 机器评审读数（r51）

| 维度 | 均分 | 极差 |
|---|---|---|
| 概念 | 1.000 | 0 |
| 层级 | 2.000 | 0 |
| 比例 | 1.667 | 1 |
| 细节 | 1.000 | 0 |
| 耐久 | 1.000 | 0 |

总分 6.667 / 10，conditional。分歧 1 格：比例（2 / 2 / 1）。

## 需求符合度（conformance.json，机械可判）

| 硬约束 | 实测 | 判定 |
|---|---|---|
| 240×240，16px 可辨 | legibility 判 OK：对比度 17.90，16px 覆盖率 0.371 | 通过 |
| 只用直角矩形，圆角 0 | 只有 rect 与 line；rect 无 rx / ry | 通过 |
| 柱体 4 根，高度等差递增 | 60 / 84 / 108 / 132，公差 24 | 通过 |
| 蓝线唯一彩色，面积 ≤5% | 蓝线面积占比 1.12%；另一色为墨黑 | 通过 |
| 线宽两档：蓝线 4、底线 4 | 描边集合 {4}；约束两个值相同，实际即一档 | 通过（按约束字面） |
| 不要火箭、齿轮、代码括号；不要投影、发光；不要 3D | 无 image / gradient / filter / text | 通过（几何层面） |

以上由 examples/03-devtool/conformance.json 与 scripts/conformance.py 判定，21 项全部通过；复跑一条命令即可核对。素材账本只有一条 own 记录：全部构件自绘，无外部素材入图。

判不了的：气质是否「准、冷静」、四柱与斜线的张力是否成立——这两条只能人判。

## 还缺什么

两名真人 × 15 分钟。请求件在 templates/review-request.md，盲评包是 05-发布/vector-ai-studio_盲评包-r52.zip（16 件、不含 key.json）。
回收后跑 review.py，reviewer-kind 取 external，usable_for_claim 才可能变真。
