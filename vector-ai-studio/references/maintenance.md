# verdict-kit 工程维护记录

## 目录

- Phase 0：骨架与哈希
- Phase 1a / 1b：信封与承诺登记
- Phase 2-1 至 2-4：契约、攻击、fixtures 与发布门禁

**verdict-kit Phase 0（r86）**：`scripts/_vendor/verdict-kit/` 是 vendored 的判定工具骨架（信封 / claims / 断言 / 契约），A106 在异目录跑 33 条冒烟并校验 `scripts/_vendor/verdict-kit/VENDORED.sha256`；现有 import 一个都没改。

**verdict-kit Phase 1a（r87/r88）**：`scripts/lib_common.py` 的 `envelope()` / `emit()` 改为调用 vendored `verdict_kit.envelope`，schema 仍是 vector-ai-studio/v1；`ToolError` 与 `run_cli` 暂不动。A107 用改动前冻结的 golden bytes 逐字节比对（13 组，含 Linux 上常见的 `partial` / unicode / degraded / missing / fail / error / ok+code1 / artifacts），不是拿同一份代码自证。

**verdict-kit Phase 1b（r89）**：`scripts/validate_claims.py` 也变成 vendored `ClaimRegistry` 的适配器——CLI flags 与结果 schema 不变；A108 用改动前冻结的 4 组 golden（无日志 / 通过日志 / 坏跳过 / 哈希漂移）做语义 parity（`by_mechanism` 的键序不是承诺）。

**verdict-kit Phase 2-1（r90）**：`scripts/gen_contract.py` 的契约装载、逐字节比对、缺目标即漂移与原子写改由 vendored `verdict_kit.contract` 承担；CLI flags、信封 data 形状、退出码与四个错误码（`E-CONTRACT-MISSING` / `E-CONTRACT-PARSE` / `E-CONTRACT-SCHEMA` / `E-CONTRACT-DRIFT`）逐项保持一致。四个渲染器、必需键清单与 `config/thresholds.json` 的文案键一致性检查仍在本技能。A109 用改动前冻结的 7 组 golden（通过 / 生成 / 手改生成物 / 坏 JSON / 缺文件 / schema 不符 / 缺必需键）做 parity：解析后 JSON 全等 + status/code（键序不是承诺）。

**verdict-kit Phase 2-2（r92）**：`scripts/redteam.py` 的六种 mutation op、点路径取值与六类声明式期望校验改由 vendored `verdict_kit.attacks` 承担；诚实基线 `base_run`、验收日志的读取、被攻击脚本的 subprocess 调用与 CLI 形状（`--attack-dir` / `--only` / `--json`）留在本技能，攻击数据仍旧是 `tests/attacks/*.json`。A111 用改动前冻结的两份 golden 做 parity：整份 `--json` 信封语义全等（18 条结果的 id/status/codes/detail 与 summary 四项计数），外加 18 条逐条的 `id / expect / exit / codes / problems`。「达标 / pass」这个通过谓词以 `too_good` 显式注入；kit 侧无默认值，未注入而命中 `expect_no_pass` 时报 `ValueError`。

**verdict-kit Phase 2-3（r93）**：`scripts/run_fixtures.py` 的三占位符展开（`@F@` / `@OUT@` / `@RUN@`）、`setup_dir` 物化、单 case 子进程执行与码匹配（去重 + 排序 + 精确相等）、汇总与声明条数守卫改由 vendored `verdict_kit.fixtures` 承担；`FIX_SCHEMA`、`tests/fixtures/fixtures.json` 的 `cases_expected` 读取、脚本目录、CLI 形状（`--json` / `--fixtures` / `--log-dir` / `--run-id`）、`E-FIXTURE-MISMATCH` 与 artifacts 写盘留在本技能。warning 的 `E-` 前缀凑不上想要的码，多一个码或少一个码都判红。A112 用改动前冻结的整份 `--json` 信封做逐字节 parity，37 条 case 的 `id` / `ok` / `exit` / `codes` / `acceptance` 摘要与「为什么没有易变字段」记在 `tests/fixtures/fixtures-run/` 的说明里。

**verdict-kit Phase 2-4（r94）**：`scripts/selftest.py` 里手写的门禁层改由 vendored `verdict_kit.assertions` 的 `Harness` / `HarnessReport` 承担——passed / failed / skipped 统计、`A60-assertion-count-declared` 计数守卫（含 id 去重）、`A76-capability-skips-are-declared` 跳过声明、`ok / partial / fail` 三态与退出码 0 / 1 / 3。留在本技能的是 112 条断言的实现体、`EXPECTED_ASSERTIONS`、`CAPABILITY_SKIPS`、L.emit 与 artifacts / run_id、CLI 形状。A60 / A76 的 id、语义、detail 文案（`declared=N ran=N` / `skipped=...`）与顺序（先跳过声明、后计数守卫）逐字不变；`EXPECTED_ASSERTIONS` 的语义是「完整运行留下的条数，含两条元断言」，因此加 A113 后从 112 变 113，`--fixtures` 仍是 +1。报告新增 `problems` 字段；该字段非空仅发生在元守卫自己红（计数不符 / 未声明跳过）时。A113 用五份纯内存报告直接验门禁语义（ok / fail / partial / 计数不符 / 未声明跳过），不起子进程、不落临时文件，跨平台读数一致；换实现前的 112 条 `id` / 顺序 / `ok` 基线冻结在 `tests/fixtures/release-gate/`。
