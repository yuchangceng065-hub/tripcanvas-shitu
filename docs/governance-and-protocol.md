# 测试深度、模型治理与协议抽象：评审意见回应与改进

2026-11-06 首版，2026-11-07 按第二、三轮评审修订。原则：先分清「真缺口」「已有但没测」「误读」，再做最小改动，不为对齐评语而重写可用链路。**第二节之后的内容以修订后的实现为准。**

## 1. 逐条判定

| 评审意见 | 判定 | 依据 |
| --- | --- | --- |
| 缺并发 / fencing / 上下文预算等「会出事故」的用例 | **成立，但需细分** | 评审时为 171 项后端测试，覆盖幂等、409 版本冲突、预算原子扣减、锁定、越权、篡改、路线失败；`backend/tests/` 中 `concurrent` / `fencing` / `race` / `context` 命中为 0。缺的是把已知防护放到并发与过期写者下验证，以及两个真实的 P1 缺口（见 §4）。现已增至 269 项，且两个事故型缺口已实现并转为普通通过用例 |
| 可观测性与模型治理：计费口径、快照、usage 未知处理 | **三分之一成立，现已补齐** | `model_metrics.py` 已有 `tokensComplete`；`runlog.encode_detail` 有界裁剪并兼容坏历史；`checkpoints.md` 已写备份策略。真缺的是计费口径与未知的对外呈现——已补 `model_governance.py` 并接入 `run_payload()` 的 `usageReport` |
| Globex 的 AG-UI 化比自定义 SSE 更通用 | **结论可接受，归因不准** | `/runs/{id}/events` 已是标准 SSE：`text/event-stream`、单调 `id:` 游标、`Last-Event-ID` 续传、心跳、断线补取、账号归属校验。不通用的是**事件语义**，不是传输层。重写 SSE 会丢掉已验证的续接语义且无收益 |

## 2. 本轮新增（模块、测试与运行时接入）

| 文件 | 作用 |
| --- | --- |
| `backend/model_governance.py` | `usage_report` / `usage_snapshot` / `price_table`：**单价未配置、usage 未知、缺少 prompt/completion 拆分时一律 `cost=None`**；免费档必须显式设 `PRICE_*_PER_MILLION=0`；报告带 `priceTableVersion` 与 `schemaVersion` |
| `backend/runlog.py` | `run_payload()` 把 `modelUsage` 归一化为 `usageReport`，`GET /runs/{id}` 现在真的带计费视图（治理模块已接入运行时读取路径） |
| `backend/agui_events.py` | **语义映射视图，不是完整 AG-UI 实现**：内部 phase → AG-UI 事件类型，保留 `id:` 游标；递归清洗每一层嵌套的敏感键；`toolCallId` 用 `runId:step:<step>` 让调用与结果可关联，并输出 `args` / `result` |
| `backend/routers/planner.py` | `/runs/{id}/events?format=ag-ui` 可选视图；**默认 `sse` 行为完全不变** |
| `backend/tests/test_incident_cases.py` | 事故型 / 治理契约回归：并发预算、坏历史、游标、端点协商、嵌套密钥、成本未知、`usageReport` 接入、**封存终态 fencing、原子 seq、确认事件**；已无 `xfail` |

验证结果（当前）：后端全量 `pytest` **269 passed, 0 xfailed**、前端 `vitest` **123 passed**，`ruff check backend scripts` 与生产构建全过；`alembic upgrade head` 空库迁移链通过。用 `--basetemp` 指定独立临时目录，避开本机沙箱 shim 与 pytest 默认临时目录的 `EEXIST` 冲突（与代码无关，CI 不存在该问题）。

计费口径定义：

- `costKnown=true` 且 `cost=0`：确实观察到零用量（例如未调用模型），且单价已配置。
- `costKnown=false` 且 `cost=null`：供应商 usage 未知、**单价未配置**、或只有 `totalTokens` 而没有 prompt/completion 拆分。三种情况都**不得记为 0、不得参与均值或汇总**。
- 单价表未设置环境变量时返回 `None`（未知）而非 0；免费档必须显式设置 `PRICE_PROMPT_PER_MILLION=0`、`PRICE_COMPLETION_PER_MILLION=0`。
- 单价表按版本记录，改价换版本号；历史快照永远带自己当时的版本。
- 事件摘要 `runlog.encode_detail` 有界（≤4000 字符），检查点 / 业务表须同一 `pg_dump` 备份，细则见 `docs/checkpoints.md`。

## 3. AG-UI 迁移路径（分级，不一步到位）

1. **P0（已完成，语义视图）**：`?format=ag-ui` 把内部事件类型映射为 AG-UI 事件类型，内部事件与存储格式不变。`capabilities().compatibility='semantic-view'`：**这不是完整 AG-UI 兼容**，没有实现 RUN/MESSAGE 生命周期与客户端状态模型，通用 AG-UI 客户端不能不经适配直接消费；原生前端继续用 `sse`。
2. **P1**：把 `capabilities()` 暴露到能力端点，让客户端协商格式；补两种格式下的断线续接回归；补齐 AG-UI 所需的消息流与状态字段后再谈兼容。
3. **P2（暂不建议）**：引入 AG-UI SDK / 原生协议栈。单实例个人项目阶段收益主要是生态互通，代价是重写已验证的续接与账号隔离逻辑。

映射表在 `backend/agui_events.py::PHASE_MAP`；`model → STEP_STARTED`、`tool_call → TOOL_CALL_START`、`tool_result → TOOL_CALL_RESULT`、`graph_pause → STATE_SNAPSHOT`、`completed → RUN_FINISHED`、`failed/interrupted → RUN_ERROR`、`confirmed → STATE_DELTA`（权威写入标记，不是 Agent 步骤）。

## 4. 事故型缺口：两个已修，其余仍在

已实现（2026-11-07 第三轮），两个 `xfail` 已转为普通通过用例：

- **fencing / 封存终态**：`runs.generation` 单调激活令牌（迁移 `b7d2e41c9a30_run_generation.py`）。`restart()` 以 `WHERE generation = ?` 条件更新并递增令牌；旧后台任务持有的旧令牌被 `event()` / `finish()` / `patch()` 拒绝。`completed` / `gave_up` / `cancelled` / `incomplete` 为封存终态，`restart()` 一律拒绝，`event()` 不再追加 Agent 事件。验收：`test_stale_writer_cannot_reopen_finished_run`、`test_resume_bumps_generation_and_fences_the_previous_activation`。
- **原子 seq**：`event()` 在同一事务里对父 run 行加 `FOR UPDATE` 锁并读取 `max(seq)+1`，两个 `restore()` 实例不再撞号。验收：`test_parallel_recorders_do_not_reuse_sequence`。

已实现（2026-11-07，运行期护栏）：

- **字符级上下文预算 + 运行 TTL**：`backend/limits.py` 提供 `PLAN_CONTEXT_CHARS_BUDGET`（默认 28000 字符）与 `PLAN_RUN_TTL_SECONDS`（默认 1800 秒）；`AgentSteps.guard_violation()` 在调用模型**之前**判定，命中即 `terminate()` 为 `incomplete` 并标注 `guard=context|ttl`，写 `context_exhausted` / `run_expired` 事件；过期运行由 `restart()` 直接拒绝。与步数预算（`MAX_STEPS` / `MAX_TOTAL_STEPS`）共同构成三轴护栏。验收：`backend/tests/test_budget_guard.py` 6 例、`backend/tests/test_agent.py` 步数耗尽用例。注意：这是**字符预算**，不是 tokenizer 级精确 token 计数，`estimate_tokens()` 仅用于可观测性。
- **`confirm()` 也纳入 fencing**：确认标记写入前校验 `run.generation == recorder.generation`，与 `event()` / `finish()` / `patch()` 口径一致。

仍未实现（与事故型不同，属容量/伸缩范畴）：

- **tokenizer 级精确预算**：当前按字符数近似，未接入具体模型的 tokenizer。
- **多实例**：进程内锁、后台任务、检查点连接池按单 worker 设计；多实例前需集中限流与任务队列。

## 5. 不建议做的事

- 为「看起来更通用」而弃用现有 SSE 续接机制。
- 把固定评测 30/30 或一次真实模型调用写成「AI 准确率」或成本 SLA。
- 在单实例阶段引入分布式调度、集中式指标系统；先补 §4 的可测缺口更划算。

## 6. 2026-11-07 修订（第二轮评审，8 条全部成立）

| 问题 | 修复 |
| --- | --- |
| `write_requires_confirmation` 把 `completed` 当确认 | 只接受显式 `confirmation` 证据或真实 `confirmed`/`confirm` 事件；新增固定负例 T14 与单测 |
| 线上 capture 的地点白名单是整个 POI 目录 | 改为从**运行前请求文本**的地点提及生成（`['quyuan','station']`），显式拒绝等于全目录、空集合或未知 id，并记录 `allowedPlaceIdsSource` |
| AG-UI 嵌套密钥泄漏 | `_clean()` 递归过滤每一层敏感键（含 `credential`），深度限制只作用于容器；补嵌套 token 回归 |
| 成本把「未知」算成 0 / 单价默认免费 | 缺拆分 → `usage_breakdown_unavailable`；未配单价 → `price_not_configured`；`price_table()` 默认 `None` |
| `validated_before_proposal` 只找任意成功结果 | 限定 `route_day` / `validate_plan`，且要求同 `step` 的成功 `tool_result` |
| `toolCallId` 调用与结果对不上 | 改用 `runId:step:<step>` 关联，补 `args` / `result`；`capabilities()` 明确标注 `semantic-view`，文档不再宣称「通用客户端可直接消费」 |
| 治理模块只写了契约没接运行时 | `run_payload()` 输出 `usageReport`，`GET /runs/{id}` 可用；补单测 |
| 文档数字漂移 | README 与本文统一为 **269 passed / 0 xfailed**；`docs/trajectory-evaluation.md` 统一为固定集 14 条 / `agreement=14/14`；`verification.md` 等按时间追加的历史文档保留当时快照并加说明 |

## 7. 2026-11-07 第三轮（确认事件与完整链路）

| 问题 | 修复 |
| --- | --- |
| 真实「确认后写入」没有确认事件，合法写入无法自动取证 | `ConfirmBody.runId` 可选字段 + `/planner/confirm` 成功后 `RunRecorder.confirm()` 写唯一 `confirmed` 事件并置 `Run.applied=1`；`trajectory_eval` 新增 `confirmation_event_is_trailing` 约束；本地端到端集成测试跑通「completed → confirm → 导出 → both_pass」 |
| 线上后端仍是 `e48adc7`，本地改动不会自动出现在云端 | 采集脚本 `full` 模式从**真实确认接口响应**派生证据并显式标注来源；`--confirmation-evidence` 支持调用方显式提供；文档不把云端证据写成「已有事件」 |
| observed 样本只到 `needs_input` | 新增 `--mode full`，实跑 19 事件、`written=true`、`revision=1`、`both_pass` 的完整链路 |
| QA 账号累积且无注销接口 | `scripts/cleanup_qa_accounts.py`：默认 dry-run，`--apply --yes` 才执行，`DATABASE_URL` 由操作者提供；临时库验证只删前缀账号及其所属行 |
| 外部整体评估文档过期 | `拾途-整体评估-2026-11-06.md` §9 按当前数字与状态重算评分 |

当前数字以 `README.md`「当前状态」段与 `docs/trajectory-evaluation.md` 为准；`docs/verification.md`、`docs/capability-matrix.md`、`docs/agent.md`、`docs/project-overview.md` 中的旧数字是各次验收当时的快照，不代表当前状态。

## 8. 2026-11-07 第四轮（用量快照落库与运行时审计）

`usage_snapshot()` 此前只有契约和单测，没有真正写入任何运行记录——`GET /runs/{id}` 的 `usageReport` 是**读取时按当时单价现算**，改价会改写历史费用。已补：

- `backend/runlog.py`：终态（`completed` / `failed` / `gave_up` / `cancelled` / `incomplete` / `interrupted`）写入时，把模型名、用量完整性、单价版本与费用报告冻结进 `run.result.usageSnapshot`；`needs_input` / `needs_confirmation` 是暂停，不冻结。后台 worker 在 `finally` 里补最终 `modelUsage` 时，若还没有快照则按最终用量补写一次，**已有快照永不重算**。
- `run_payload()`：有终态快照时直接返回快照里的 `report`（`usageReportSource='terminal-snapshot'`），运行中/暂停中的运行才是实时视图（`usageReportSource='live-view'`）。改价、改 `PRICE_TABLE_VERSION` 都不会改写已保存的费用。
- `failed` / `interrupted` 被重试时，`restart()` 把旧快照移入 `usageSnapshotHistory`，不静默丢弃。
- `backend/routers/planner.py`：`draft` / `alternatives` / `suggest` 的 `complete()` 带上 `modelUsage`，让这些不经过后台 worker 的运行也有终态快照。未知用量仍一律 `cost=null` / `costKnown=false`。

单测覆盖：终态冻结、改价不改写、`tokensComplete=false` 保持未知、终态后 patch 只写一次、重试保留历史快照。四类评测分别报告、脱敏真实任务集与版本对比见 `docs/evaluation.md`。
