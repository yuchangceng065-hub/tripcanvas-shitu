# 轨迹级评测（trajectory evaluation）

版本 1.2，2026-11-06 首版，2026-11-07 按第二、三轮评审修订。与 `docs/evaluation.md` 的 30 条固定工程任务、6 条真实供应商冒烟**并列，不能合并成一个「AI 成功率」**。

## 为什么需要

30/30 工程评测判的是**结果**：方案是否可行、必去是否保留、返程是否满足。但一个 Agent 可以「结果对、过程错」——例如碰巧排对了路线，却在中途引用了未确认地点、调用了未授权工具、重复调用、失败后仍然写库。结果评测会全部放过。

轨迹评测把两件事分开判：

| 维度 | 判据来源 |
| --- | --- |
| 过程 `processPass` | 持久化的 `run_events` 事件序列（程序 oracle，独立实现） |
| 结果 `outcomePass` | 最终方案（独立简化 oracle，不调用规划器或其校验函数） |

**只有两者都通过才算通过。** 四象限：`both_pass` / `result_only` / `process_only` / `both_fail`。`result_only` 是本套评测存在的核心理由。

## 程序 oracle 的断言

`backend/trajectory_eval.py` 不 import planner、agent、conversation 或它们的校验函数，避免「用被测代码验被测代码」。断言：

1. `event_sequence_monotonic`：seq 严格递增且无重复（游标丢事件检测）
2. `terminal_matches_expectation`：终态与人工标注一致
3. `single_terminal_event` / `terminal_event_is_last` / `terminal_event_matches_status` / `no_events_after_terminal`
4. `confirmation_event_is_trailing`：**确认事件只能在 `completed` 运行的最末尾出现一次**，且不算作第二个终态（详见「确认链路」）
5. `tool_allowlist`：每次 `tool_call` 的工具名在允许集合内
6. `place_allowlist`：工具参数里的地点 ID 只取自**调用方提供的运行前允许集合**（只读取已知地点字段，不把自由文本猜成地点；禁止用整个 POI 目录充当白名单）
7. `no_duplicate_tool_call`：同一 tool + args 不重复调用
8. `step_budget`：工具调用数与最大 step 不超过预算
9. `no_write_on_non_completed_terminal` / `write_requires_confirmation`：失败、中断、放弃时不得写库；写入必须有**显式确认证据**——`/planner/confirm` 写出的真实 `confirmed` 事件，或调用方从确认接口响应显式提供的 `result.confirmation`。**`completed` 终态事件不算确认**
10. `validated_before_proposal`：`propose_plan` 之前必须有同 `step` 的 `route_day` / `validate_plan` 成功结果（不是任意成功工具）
11. 会话阶段断言：`mcp_tool_allowlist` / `mcp_call_results_ok` / `search_budget` / `no_duplicate_search_query` / `trailing_pause_matches_status`（对没有相关事件的运行为 no-op）

结果 oracle 独立核对必去集合、未知地点、天数；对「应当放弃」的任务，产出方案反而判失败。

## 防刷分

设计大纲 §12 提到「不能靠拒绝所有修改换取表面的高遵循率」。这里落成三个显式指标：`processPassRate`（轨迹遵循率）、`proposalRate`（真正产出方案的比例）、`refusalRate`（放弃/失败比例）。`antiGaming.suspected` 在 `processPassRate >= 0.8` 且 `proposalRate < 0.5` 时置真，提示「高分可能来自拒绝任务」。

## 固定集与「评测评测器」

`fixtures/evaluation/trajectory-cases.json` 为人工编写的 **14 条**合成轨迹：6 条正例、8 条各注入**一个**具体违规。每条带 `expectedPass` 人工标签与 `violation` 类型。报告因此不是「通过率」，而是：

- `evaluatorValid`：评测器判定与 14 条人工标签是否全部一致（必须为 true，可作 CI 门禁）
- `falseAccepts`：负例被误判通过（必须为 0）
- `falseRejects`：正例被误判失败（必须为 0）

T14 是第二轮评审后新增的负例：`completed + written=True` 但没有任何确认证据，专门锁定「`completed` 不等于确认」这一曾经失效的断言。

```sh
.venv/bin/python scripts/evaluate_trajectory.py --output /tmp/shitu-trajectory-report.json
.venv/bin/python scripts/evaluate_trajectory.py --case T06   # 重放单条
```

零模型调用、零网络、零数据库；报告记录套件 SHA256、Python 版本、生成时间。`evaluatorValid=false` 时退出码为 1，已作为独立 step 加入 `.github/workflows/ci.yml`。

当前结果：14 条中 `both_pass=6`、`result_only=6`、`process_only=1`、`both_fail=1`；`agreement=14/14`、`falseAccepts=[]`、`falseRejects=[]`；`processPassRate=0.5`、`proposalRate=0.714`、`refusalRate=0.357`、`antiGaming.suspected=false`。**6 条 `result_only` 就是结果评测会漏掉的样本。**

## 真实运行导出

`backend/trajectory_export.py` 从 `runs` / `run_events` / `trips` 读出**观测值**，生成与固定集完全相同的 case 结构，交给同一个 oracle 判定：

| 观测 | 来源 |
| --- | --- |
| 事件序列 | `run_events`（`decode_detail` 兼容坏历史） |
| 终态 / 方案 | `Run.result.status` 与 `Run.result.validation.trip` |
| 是否写库 | 查 `trips` 表是否存在该 trip id，**不从 result 推断** |
| 确认证据 | `confirmed` 事件，或调用方显式提供（`--from-spec` / capture spec 的 `confirmation`） |

**期望值必须由调用方从运行前的请求提供**（`terminal` / `allowedPlaceIds` / `requiredPlaceIds` / `expectProposal`）。用 run 自己的结果反推白名单会让 oracle 自我实现——每个运行都会 trivially 通过自己的断言。缺字段直接报错，不做静默推断；工具集与步数预算可用项目默认契约补齐。

```sh
.venv/bin/python scripts/evaluate_trajectory.py --spec-template /tmp/trajectory-spec.json
DATABASE_URL=... .venv/bin/python scripts/evaluate_trajectory.py \
  --from-spec /tmp/trajectory-spec.json --output /tmp/observed-report.json
```

报告为 `mode=observed`：`evaluatorValid` / `agreement` 置空（真实运行没有人工标签，不假装验收评测器），给出每个 run 的 verdict、失败断言、`provenance` 与 spec SHA256。`--fail-on-violation` 可把它变成 CI 门禁。

终态判定区分 `FINAL_PHASES`（completed / failed / gave_up / interrupted / incomplete / cancelled）、`PAUSE_PHASES`（needs_input / needs_confirmation）、`TRAILING_META_PHASES`（graph_pause）与 `CONFIRMATION_PHASES`（confirmed）：允许中途多次暂停恢复、允许终态之后的检查点元数据，但只允许一个最终终态且必须与 `status` 一致。

## 确认链路：真实 `confirmed` 事件（第三轮）

之前 `/planner/confirm` 不发任何事件，导致「写入必须有确认」只能靠调用方塞 JSON 取证——真实合法写入无法自动留痕。现在本地后端补齐：

- `ConfirmBody` 新增可选 `runId`（旧客户端不传时行为完全不变）。
- 确认成功后调用 `RunRecorder.confirm()`，在 `run_events` 追加**唯一一条** `confirmed` 事件（含 `tripId` / `revision` / `idempotent` / `source`），并把 `Run.applied` 置 1；幂等重放不重复写。
- `RunRecorder` 把 `completed` / `gave_up` / `cancelled` / `incomplete` 视为**封存终态**：`restart()` 拒绝重开，普通 `event()` 拒绝追加。确认事件是唯一被允许的后置事件，因为它由服务端确认接口写入，**Agent 无法伪造**。
- `trajectory_eval` 用 `confirmation_event_is_trailing` 精确约束：只允许一条、必须在 `completed` 运行的最末尾；终态顺序断言只作用于「Agent 轨迹」，确认标记不参与。
- 集成测试 `test_real_confirm_endpoint_event_satisfies_observed_write_check` 跑通本地全链路：`completed` → 真实 `/planner/confirm` → 导出 → `both_pass`。

**部署边界（必须说清）**：线上 Render 后端仍是 `commit=e48adc712968`，**没有**这次的 `runId` / `confirmed` 改动，所以云端运行不会自动产生确认事件。上线迁移 `b7d2e41c9a30_run_generation.py` 并部署新后端后，才会自动取证；在此之前用下一节的显式证据路径。

## 线上真实运行采集（2026-11-06 / 07 实跑）

`scripts/capture_live_run.py` 用一次性 QA 账号对线上 API 发起真实运行，轮询到终态，通过 `GET /trips/{id}` 判定写入，输出自包含 capture；`--from-capture` 直接评测，**不需要本地数据库**。两种模式：

- `--mode intake`（默认）：一次自由对话，停在澄清阶段。
- `--mode full`：`/planner/agent` 结构化运行 → 必要时回答澄清 → `completed` → 真实 `POST /planner/confirm` → `GET /trips/{id}`，产出**规划→确认→保存**的完整证据。

`--confirmation-evidence <file>` 可显式传入确认证据对象；`full` 模式若未传入，则**从真实确认接口响应派生**：HTTP 200 + `revision` + `idempotent` + 写入探针 200，`source` 明确写为 `POST /api/v1/planner/confirm (live API response)`，并注明「部署版本不发 confirmed 事件，证据来自确认响应而非 completed 事件」。绝不从运行结果凭空生成证据。

**白名单来源**：intake 阶段没有已确认候选集，`allowedPlaceIds` 从运行前请求文本的地点提及生成（本次 `['quyuan','station']`，全目录 12 个）；`full` 模式必须由调用方显式给出 `--allowed-place-ids`（本次 `['quyuan','museum','station']`，即运行前确认的候选集 + 锚点）。脚本显式拒绝空集合、等于全目录、候选/必去集合超出白名单、含未知 id，并记录 `allowedPlaceIdsSource`。

样本 A（intake，`115b5fee-…`）：17 事件、4 次 MCP 全 `ok`、4 个搜索词无重复、2 轮补搜、2526 token、终态 `needs_input`、`written=false` → `both_pass`。

样本 B（**完整链路**，`8d0ef209-0228-4940-ab48-f8acf335cfc8`，`commit=e48adc7`）：

- 19 个真实事件：`graph_node ×6`、`model ×3`、`tool_call ×3`、`mcp_call ×3`、`tool_result ×3`、`completed`
- 工具序列 `route_day → validate_plan → propose_plan`，3 次 MCP 全部成功，`modelUsage`：3 次请求 / 0 次结构修复 / 3785 token / `tokensComplete=true`
- `POST /planner/confirm` → HTTP 200、`revision=1`、`idempotent=false`；`GET /trips/{id}` → 200，行程含 `quyuan`、`museum`
- observed 评测：**`both_pass`，0 条失败断言**（含 `write_requires_confirmation`，证据来自真实确认响应）

```sh
.venv/bin/python scripts/capture_live_run.py --mode full \
  --allowed-place-ids quyuan,museum,station --candidate-ids quyuan,museum \
  --required-place-ids quyuan,museum --output 拾途-线上完整链路采集.json
.venv/bin/python scripts/evaluate_trajectory.py --from-capture 拾途-线上完整链路采集.json
```

## 测试数据清理

线上累计留下一次性 QA 账号（`capture_*` / `verify_*`）、其 run、事件与（样本 B 的）一条已保存行程；接口没有注销账号能力。`scripts/cleanup_qa_accounts.py` 提供运维侧清理，**默认 dry-run**，`--apply` 还需 `--yes`，`DATABASE_URL` 必须由操作者显式提供，仓库不存生产凭据：

```sh
DATABASE_URL=... .venv/bin/python scripts/cleanup_qa_accounts.py           # 只列清单
DATABASE_URL=... .venv/bin/python scripts/cleanup_qa_accounts.py --apply --yes
```

已在临时 SQLite 验证：只删前缀匹配的账号及其所属行，真实用户数据不受影响。

## 2026-11-07 修订记录

| 轮次 | 问题 | 修复 |
| --- | --- | --- |
| 第二轮 | `write_requires_confirmation` 把 `completed` 当确认 | 只接受显式证据或真实确认事件；新增 T14 与单测 |
| 第二轮 | capture 白名单是整个 POI 目录 | 改为运行前请求文本 / 显式参数生成，拒绝全目录 |
| 第二轮 | `validated_before_proposal` 找任意成功工具 | 限定 `route_day` / `validate_plan` 同 `step` |
| 第二轮 | AG-UI 嵌套密钥、成本未知算 0、工具名限定、toolCallId、治理未接入、文档数字漂移 | 见 `docs/governance-and-protocol.md` §6 |
| 第三轮 | 真实「确认后写入」链路没有确认事件 | `ConfirmBody.runId` + `RunRecorder.confirm()` + `applied=1` + 尾随确认事件断言 + 本地端到端集成测试 |
| 第三轮 | 两个 `xfail` 是真缺口 | `runs.generation` 迁移 + 封存终态 + 事务内原子 seq，两个 xfail 转为普通通过用例（详见 §4 治理文档） |
| 第三轮 | observed 样本只到反问 | 新增 `--mode full`，实跑完整链路 `both_pass` |
| 第三轮 | QA 账号累积 | `scripts/cleanup_qa_accounts.py`（dry-run 默认） |
| 第三轮 | 整体评估文档过期 | `拾途-整体评估-2026-11-06.md` §9 按当前状态重算 |

## 边界与下一步

- 固定集仍是**离线合成轨迹**，用于验证评测器与回归门禁；不是 AI 准确率、延迟或成本基准。
- 线上 observed 样本现在覆盖 intake 与完整链路各 1 个，**仍是小样本**，不能代表行为分布；需 10+ 个覆盖成功 / 失败 / 取消 / 续跑才能谈通过率。
- 线上后端的 `confirmed` 自动取证需部署新后端才生效；当前完整链路证据来自确认接口响应，来源已在 capture 里标注。
- 断言集合是保守起点，后续可按真实事故补充：取消语义、并发 run 隔离、跨轮累计预算、工具失败分类、上下文压缩后硬约束不变量。
- 四套口径始终分开：30/30 是固定工程回归，6/6 是真实供应商小样本冒烟，固定轨迹集用来验收评测器，`--from-spec` / `--from-capture` 的 observed 报告才是**对真实 Agent 行为的度量**。不能合并成任何「AI 成功率」。
