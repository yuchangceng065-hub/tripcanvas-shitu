# 任务评测与可复现验收

版本 1.0，2026-11-05。这里区分工程任务、供应商冒烟任务和浏览器验收。它们不能合并成一个“AI 成功率”。

## 离线工程任务

`fixtures/evaluation/cases.json` 包含 30 条人工编写的固定任务：10 条需求解析、14 条多方案规划、6 条确认/版本/归属/分享保护。其中 10 条开发、20 条固定回归；回归任务并非盲测，不能证明模型泛化能力。

```sh
.venv/bin/python scripts/evaluate.py --output-dir /tmp/shitu-evaluation
```

评测进程强制临时 SQLite、空供应商密钥和规则模式，并禁止 HTTP 网络传输。道路是明确标记的合成夹具，外部请求和模型调用均为 0。报告记录任务、错误码、源文件与任务集 SHA256、Git 基线、Python 版本和本机耗时。单项可用 `--case S02` 重放；未知任务类型、重复 ID、拼错保护动作会拒绝运行。

验收判定不调用规划器的约束校验函数：`backend/evaluation.py` 独立核对候选守恒、必去/可选取舍、日期和固定返程、停留下限、道路与时间算术、步行预算及展示指标。也单独统计可行任务生成率，避免靠拒绝所有请求得到通过率。

首次结果为 28/30：下午五点半未解析、否定自驾误识别。修复后为 30/30，其中 10/10 可行规划任务生成有效方案，4 条不可行/上游失败任务没有错误接受方案。这些数字是已知固定工程任务的回归结果。报告见 `evaluation-baseline/` 与 `evaluation-final/`。

本机夹具 p50/p95 只用于发现工程退化，不能代表实际模型、高德或用户端延迟。

## 真实供应商任务

`fixtures/evaluation/live-cases.json` 单列 6 条固定任务：一日两站、一日三站、两日四站和返程、只缩短一站、只延后出发、雨天换室内。它们使用生产 Groq `openai/gpt-oss-20b` 和高德道路，不使用夹具。它们是小样本冒烟验收，不是通用 AI 准确率基准。

部署验证后，显式执行：

```sh
HTTPS_PROXY=http://127.0.0.1:7890 .venv/bin/python scripts/evaluate_live.py \
  --run-production-free-tier --release-commit <已验证的40位部署提交> \
  --output-dir /tmp/shitu-live-evaluation
```

无需向脚本提供模型密钥。脚本仅连接固定的本项目 API，注册独立 QA 账号并在最后退出；只写 QA 旅行。最多 6 个模型任务，服务端每任务最多一次结构修复（最多 12 次供应商请求），间隔至少 35 秒。失败逐条记录，不自动换模型或付费服务。确认重试必须只产生一个版本。

每个成功的模型响应附 `modelUsage`：请求次数、结构修复次数、供应商调用阶段耗时、供应商报告的输入/输出/总 token。计数为请求隔离上下文；不会记录提示词、密钥或推理内容。缺少用量时 `tokensComplete=false`，已有数字仅表示报告到的部分，不能作为完整成本。失败响应未附用量，同样视为未知。

本次真实验收为 6/6，供应商共报告 6 次请求、0 次结构修复、9,592 token；见 [逐项报告](evaluation-live/report.md)。验收对应已上线后端提交 `7d4b8b419798d2cd95485ad9d7246e984a8fde08`，后续仅修复前端返程输入可见性、评测 oracle 与文档。每项 2.73—4.60 秒包含各自的道路/预览/保存核验，任务耗时口径不同，不构成延迟 SLA。没有失败项不等于任意需求都能理解。

## 自动化与边界

（历史快照，2026-11-07 前）前端 81 项、后端 121 项测试通过；当前数字见 README「当前状态」段，类型检查与构建通过；GitHub Actions 自动执行测试、构建、离线评测和空库迁移。测试覆盖实际 Vue 组件按键、方案卡切换与确认、独立 oracle 故意破坏数据会失败、错误任务类型会拒绝、模型用量不会串到下一次请求。

仍缺：大规模/盲测模型任务、供应商故障注入后的真实 UI 全覆盖、长期容量与延迟监测、真实逐阶段后台事件、数据库迁移和演示视频。版本中的历史验收文档保留时间边界，不能当作当前线上能力的唯一结论。

## 本轮浏览器复验

使用隔离的本地 QA 账号、规则模式与真实 Photon/OSRM。390×844 手机视口下页面宽度为 390、弹窗为 362，无横向溢出；确认 17:30 与步行识别、取消终点后返程时间可见且可清空。跑通四候选（两必去、两可选）→三方案→选择充分游览→确认保存→刷新保留。1280×900 桌面比较卡布局正常。Enter 发送“晚出发一小时”生成逐站顺延预览，未确认不修改原计划。此浏览器结果不是线上高德 UI 或真实中文输入法选词的验收；线上 Groq＋高德单列在上文。

## 轨迹级评测（2026-11-06 新增）

第三种评测单列：`fixtures/evaluation/trajectory-cases.json` + `backend/trajectory_eval.py`，判的是 Agent 的**行为轨迹**而非方案结果。过程与结果分开判，只有两者都通过才算通过；固定集含 7 条注入违规的负例，用 `evaluatorValid` / `falseAccepts` / `falseRejects` 验收评测器本身，并用 `proposalRate` + `refusalRate` 防止「靠拒绝任务刷遵循率」。

```sh
.venv/bin/python scripts/evaluate_trajectory.py --output /tmp/shitu-trajectory-report.json
```

零模型/网络/外部服务调用（DB 只用于 `--from-spec` 读取本机持久化运行；`--from-capture` 直接读已采集的 API payload），套件 SHA256 记录在报告里。当前固定集 **14 条 `agreement=14/14`**、6 条 `result_only`（结果正确但过程违规，正是结果评测会漏掉的样本）；另已跑通线上 observed 评测两个样本：intake 澄清阶段（17 事件、`needs_input`、`written=false`）与**完整「规划→确认→保存」链路**（19 事件、`completed`、`written=true`、确认接口 200 / `revision=1`）均为 `both_pass`，见 [轨迹级评测](trajectory-evaluation.md)。**固定集是离线合成轨迹、用于验收评测器本身；真实运行请用 `--from-spec` / `--from-capture` 得到 observed 报告。两者都不能与 30/30 或 6/6 合并成任何「AI 成功率」。**

## 四类评测必须分别报告（2026-11-07）

以下四类量的是不同对象，**报告里没有任何合并后的「AI 成功率」**：

| 类别 | 命令 | 报告口径 |
| --- | --- | --- |
| 固定合成工程任务 | `scripts/evaluate.py` | 30 条确定性结果断言的 `passed/total` |
| 评测器标签一致性 | `scripts/evaluate_trajectory.py`（默认固定集） | `agreement` / `falseAccepts` / `falseRejects`，验的是 oracle 本身 |
| 合成 Agent 样本 | `scripts/evaluate_agent.py` | 桩模型下的逐条 verdict，不是准确率 |
| 脱敏真实任务 | `scripts/evaluate_real_tasks.py` | 四象限 verdict、`processPass` / `outcomePass` 分开列、覆盖率 |

`fixtures/evaluation/real-tasks/v1.json` 由 `scripts/build_real_task_suite.py` 从 `capture_live_run.py` 的线上采集构建：账号名、API 地址、UUID 全部清除或替换为稳定别名；只保留 oracle 需要的事件、终态、方案与显式写入证据。每个任务的**运行前预期**（`spec`）单独存一份 SHA256，评测时校验它没有被事后改写——预期绝不能从运行结果反推。

```sh
# 生成/刷新脱敏套件（缺采集的分类保持 not_captured，不编造轨迹）
.venv/bin/python scripts/build_real_task_suite.py --use-known-captures
# 评测某一代码版本
.venv/bin/python scripts/evaluate_real_tasks.py --version <commit> --output /tmp/real-v1.json
# 用同一套任务集比较两版，输出逐任务 improved / regressed / unchanged
.venv/bin/python scripts/evaluate_real_tasks.py --version <new-commit> \
  --baseline /tmp/real-v1.json --output /tmp/real-v2.json --fail-on-regression
```

套件声明 5 个分类：规划成功、反问续接、不可行约束、工具失败、断线恢复。当前只有前两类有真实线上采集；其余三类是**覆盖率缺口**，报告为 `not_captured` 而不是失败，需要部署恢复后按 `captureHint` 补齐。版本对比要求两份报告的 `suiteSha256` 一致，`sameSuite=false` 时只作提示、不把结论当回归。

### 一键验证入口

`scripts/verify.sh` 固化了全套基线（顺序与 CI 一致），避免每次手敲命令或漏参数：

```sh
bash scripts/verify.sh             # 全量：lint / 后端 269 / 前端 124 / typecheck / build / 四类评测 / 空库迁移 / diff-check
bash scripts/verify.sh --quick     # 后端 + 评测（跳过前端与构建）
```

脚本为本机沙箱的 `tmp_path` 冲突统一传入 `--basetemp=/tmp/tc-verify-*`（环境问题，非产品缺陷），并显式打印真实任务集的 3 个 `not_captured` 分类，防止把覆盖缺口误读成"全部通过"。四类评测仍各自独立输出，脚本不做任何合并。
