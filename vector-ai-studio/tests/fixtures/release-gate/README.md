# 冻结的门禁基线（r94 / Phase 2-4）

这个文件是**换实现之前**跑出来的。r94 把 `scripts/selftest.py` 里手写的门禁层
（passed/failed/skipped 统计、`A60` 计数守卫、`A76` 跳过声明、`status` 三态推导、
退出码 0/1/3）换成 vendored `verdict_kit.assertions`（`Harness` / `HarnessReport`），
断言 A113 钉的是换实现**之后**的门禁语义；这份基线钉的是**之前**的逐条结果，
供 QA 在同环境下做新旧对照。

| 文件 | 内容 | 约束 |
|---|---|---|
| `gate-baseline.json` | 旧门禁层（v1.25.1 / r93.1）一次完整 `scripts/selftest.py --json` 的 `id` / `ok` / 顺序，以及 `status` / `passed` / `failed` / `skipped` / `partial` | 纯数据：无路径、无 run_id、无时间戳、无耗时 |

## 怎么用

1. 在同一环境（同一 Python、同一可选依赖）跑 `scripts/selftest.py --json`；
2. 去掉 A113 之后的 112 条（A113 插在 #111，按「前 112 条」序号截取会错位），与
   `gate-baseline.json` 的 `assertions` 比 **`id`、顺序、`ok`** 三元组；
   `detail` 允许差异（措辞由 kit 生成，语义不变）；
3. 顶层读数按 `--fixtures` / 非 `--fixtures` 分别比较：基线（v1.25.1）是
   `passed=112`、`failed=0`、`skipped=[]`、`status=ok`、退出码 0；v1.26.0 起
   非 `--fixtures` 为 113、`--fixtures` 为 114（`failed` / `skipped` / `status` /
   退出码不变）。

## 为什么这份基线能进包

它只有 id 与布尔值，没有绝对路径、没有时间戳、没有 run_id、没有环境指纹，
换机器也能逐条对比。它**不**参与任何运行时判定，只是回归对照的数据。
