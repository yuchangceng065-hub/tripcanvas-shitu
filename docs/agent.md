# 有界工具调用 Agent

已扩展自由描述 → 多轮澄清 → 真实地点选择 → 需求确认 → 道路规划；规划反问后可继续原任务。新增链路见 conversation.md。Agent 仍不能擅自扩大已确认的游览候选集合。

## 接口与状态

`POST /api/v1/planner/agent` 接收与 alternatives 相同的 title、requirements、candidateIds、optionalIds，立即返回 `{runId,status:"running"}`。请求进入后台前检查地点是否存在、候选是否重复、起终点是否混入游览候选、候选是否足够分日。

`GET /runs/{runId}` 返回实际运行状态、持久化事件、模型用量及完成后的 `validation`。终态包括 completed、needs_input、gave_up、incomplete、failed、interrupted。方案不自动保存，仍需 `/planner/confirm`。

## 服务端边界

- 默认最多 6 次模型决策；整个 Agent 执行超过 240 秒会停止并写入 failed。
- 白名单动作：route_day、validate_plan、compare_plans、propose_plan、need_input、give_up。
- 游览工具参数必须属于本次候选集合；道路查询可额外使用明确的起终点。已入库但未选中的地点同样拒绝。
- 所有游览候选全程最多一次，必去全保留；只有 optionalIds 可被舍弃。起终点由程序注入，允许同站出发与返程。
- 最终方案经真实道路与 evaluate 核验，保存时再次核验；保留用户输入的旅行标题。
- 每用户每日默认 6 次，每分钟 2 次，与多方案共享进程内运行锁。进程重启标记 interrupted，不承诺跨进程恢复计算。

## 实时轨迹与恢复

`GET /runs/{id}/events` 每个连接最多 90 秒，每 10 秒发送注释心跳；Last-Event-ID 支持续传，CORS 允许该请求头。SSE 的 data 为 `{status,detail}`，与事件序号、阶段共同返回；前端兼容旧的平铺 data。

前端在连接结束或断网后查询持久化结果，仍 running 时带最后序号续接并去重。查不到结果连续三次或观察超过六分钟，显示“继续查看本次运行”，不会重新 POST 消耗生成次数。相同需求的待完成 runId 保存在当前浏览器会话，重新打开可续看；修改需求使旧预览失效，需要明确重新生成。中止查看不会取消服务端计算。

事件按字段裁剪后编码为完整 JSON，最长 4,000 字符；模型 observation 同样保留有效 JSON。较早版本已截坏的历史事件显示“摘要不完整”，不阻止读取运行结果。事件流开始前释放认证查询占用的数据库连接。

## 保存前的完整预览

完成结果包含用户标题、真实道路算出的逐站 timeline、逐日指标、includedIds、omittedIds、路线来源与警告。前端展示日期、顺序、开始/结束、停留、转场、等待、必去和固定时间、可选地点取舍。缺少完整逐站预览或有硬冲突时不能保存。

空余时间未自动安排餐休，景区内部步行、营业、票务和预约仍需确认。模型 summary 只是说明文字，实际约束与警告以程序核验结果为准。

## 验证口径

本轮自动化：前端 97 项、后端 160 项、Ruff、类型检查与构建通过；原固定工程任务 30/30。新增 Agent 回归覆盖候选越界、同日/跨日重复、必去遗漏、可选取舍、标题、同站往返与返程晚到、有效长 JSON/历史损坏、SSE 状态与跨域游标、断线恢复、去重、取消查看、待运行恢复和旧预览失效。

原来的 30 条是工程固定任务，并非 Agent 模型准确率。真实模型与浏览器验收在 verification.md 追加，测试中脚本化模型不冒充真实 AI。

供应商 JSON 生成失败：仅 HTTP 400 且 error.code 为 json_validate_failed 或 output_parse_failed 时允许模型在剩余步骤内重试；不增加单次运行的 6 步预算，不对其他 400、鉴权或限额错误盲目重试。未报告的 token 仍视为未知。[Groq JSON 模式说明](https://console.groq.com/docs/structured-outputs)。

Groq GPT-OSS 20B/120B 的动作回复使用 strict JSON Schema（仅精确供应商主机与支持型号启用）；候选枚举、动作和各类参数形状显式约束。不同动作的参数联合使用互不重叠的必填键，满足供应商 schema 限制；其他兼容供应商继续 JSON object 模式。仍由服务端校验动作与参数是否对应、必去、重复、道路及时间，Schema 不替代业务约束。针对真实 output_parse_failed 的步骤重放已返回 200，完整发布后闭环另行记录。
