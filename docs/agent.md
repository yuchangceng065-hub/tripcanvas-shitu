# 有界工具调用 Agent（P0）

P0 只做后端：在“候选已确认”之后，模型多步选择工具、服务端执行、校验后返回方案。前端仍走现有向导；不做自由对话、不做后台任务。

## 接口

```http
POST /api/v1/planner/agent
Authorization: Bearer <token>
{
  "title": "我的杭州旅行",
  "requirements": { "...": "与 /planner/alternatives 相同" },
  "candidateIds": ["museum", "quyuan"],
  "optionalIds": []
}
```

返回：

- `status=completed`：`runId`、`plan.trip`、`plan.issues`、`plan.metrics`、`plan.summary`、`steps`、`modelUsage`
- `status=needs_input`：`question`
- `status=gave_up`：`message`
- `status=incomplete`：超过步数上限，`message`

方案不会自动保存；仍需 `POST /planner/confirm`。

## 循环

- 最多 `PLAN_AGENT_STEPS`（默认 6）步；每步模型只输出一个动作 JSON。
- 动作：`route_day`、`validate_plan`、`compare_plans`、`propose_plan`、`need_input`、`give_up`。
- 工具在服务端执行，白名单动作；地点必须是已确认候选，未知 id 直接拒绝。
- `propose_plan` 通过 `evaluate` 后才算完成；有硬冲突会作为 observation 返回，模型继续调整。
- 每用户每日次数 `PLAN_AGENT_DAILY_LIMIT`（默认 6），每分钟 2 次；与多方案共用进程内锁。
- 模型不可用/规则模式返回 503，规则草案仍可使用。

## 事件

每个运行写入 `runs` / `run_events`：`model`（动作）、`tool_call`、`tool_result`、`model_error`、`completed`/`failed`/`need_input`。

`GET /api/v1/runs/{id}/events` 按 `Last-Event-ID` 游标回放；P0 不是实时推送，也不是后台队列。

## 边界

- 不存储 prompt、思维链或密钥；工具参数与结果摘要截断到 4KB。
- 不生成坐标、地点 id；不写数据库。
- 只覆盖“已确认候选 → 方案”这一段；地点搜索/消歧留给 P2。
- 尚未接入前端轨迹面板；真实 Groq 线上已跑通一次（3 步，4.6 秒，2093 token）。
