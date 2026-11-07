# 运行期护栏设计与验证

一次规划运行（planning run）受三条相互独立的上限约束，避免失控的模型调用无限消耗预算或锁死会话。本文档记录设计约束、实现位置与验收口径。

## 1. 三轴护栏

| 轴 | 含义 | 默认上限 | 环境变量 |
|----|------|----------|----------|
| 步数预算 | 模型调用次数硬上限 | 单轮 `MAX_STEPS=6`、累计 `MAX_TOTAL_STEPS=12` | `PLAN_AGENT_STEPS` |
| 上下文预算 | 每次运行喂给模型的字符数上限 | 28000 字符 | `PLAN_CONTEXT_CHARS_BUDGET` |
| 墙钟 TTL | 运行存活时长上限 | 1800 秒 | `PLAN_RUN_TTL_SECONDS` |

三轴相互独立：步数限制模型调用次数，上下文限制 prompt 增长，TTL 限制被遗弃运行的存活时长。上限均在**调用时**从环境变量读取，便于测试注入而不必 reload 模块。

## 2. 设计约束

- **护栏在调用模型之前判定**。`decide()` 返回 `None` 会重新进入 `choose` 节点，若护栏不前置判定就会形成死循环；`run_agent()` 与 `agent_graph.choose()` 都在 `decide()` 之前检查 `guard_violation()`。
- **安全停机而非失败**。任一护栏触发时运行置为 `incomplete`（不是 `failed`），按原因写 `context_exhausted` / `run_expired` 事件，保留已生成方案，`guard` 标注 `context|ttl|steps`。触发后绝不调用模型。
- **TTL 优先于上下文预算**。`guard_violation()` 先查 TTL，再查字符预算，保证停机原因唯一且可解释。
- **不污染计费快照**。`usageSnapshot` 只在终态冻结，护栏在终态前触发，不影响用量与费用口径。
- **过期运行拒绝恢复**。`restart()` 检查 `time.time() - run.created > run_ttl_seconds()`，弃用运行（用户隔天再回答反问）直接拒绝，返回清晰提示，不续接旧激活。

## 3. 实现位置

| 文件 | 内容 |
|------|------|
| `backend/limits.py` | 纯标准库配置模块：`context_budget_chars()` / `run_ttl_seconds()` / `context_chars()` / `estimate_tokens()` |
| `backend/agent.py::AgentSteps` | `context_budget` / `ttl_seconds` 字段；`context_chars_used()` / `over_context_budget()` / `run_expired()` / `guard_violation()` / `terminate()`；`incomplete()` 标 `guard='steps'` |
| `backend/agent_graph.py::choose()` | 进入节点先判护栏，命中即 `terminate()` 后 `END` |
| `backend/runlog.py::RunRecorder` | `__init__` / `restore` 暴露 `created`（epoch 秒）；`restart()` 增加 TTL 检查 |

`estimate_tokens()` 只用于可观测性，不参与判定；权威口径是字符数。这是字符级预算，不是 tokenizer 级精确 token 计数。

## 4. 验收口径

```sh
bash scripts/verify.sh              # 全套基线
bash scripts/verify.sh --quick      # 后端 + 评测
```

- 护栏单测：`pytest backend/tests/test_budget_guard.py -q` → **6 passed**，覆盖 上下文超额停机与事件 / TTL 停机与事件 / 新鲜运行不误杀 / TTL 优先 / `restart()` 拒绝过期运行 / `run_agent` 护栏触发时**不调用模型**。
- 步数护栏由 `backend/tests/test_agent.py` 覆盖。
- 全量基线：后端 **269 passed**、前端 **124 passed**，`ruff check backend scripts` 与生产构建通过；固定轨迹集 `agreement=14/14`、固定工程任务 30/30、Agent 样本 3/3。
- 不可接受：用 `xfail` 绕过护栏用例；护栏触发后仍调用模型；TTL 与上下文判定顺序颠倒。

> 本机运行 `pytest` 时带上 `--basetemp=/tmp/tc-*`：沙箱对 `/private/var` 目录代理的 mkdir 会让 pytest 默认 `tmp_path` 触发 `EEXIST`。这是环境限制，与代码无关，CI 不存在该问题。`verify.sh` 已统一处理。

## 5. 边界与注意点

- env 注入时机：各函数在调用时读 `os.getenv`，测试中 `monkeypatch.setenv('PLAN_CONTEXT_CHARS_BUDGET','100')` 即时生效。
- 异步用例：仓库未装 `pytest-asyncio`，`run_agent` 用例用 `asyncio.run(...)` 包裹，不要改成 `@pytest.mark.asyncio`。
- `Run.created` 列已存在，`__init__` / `restore` 已读写，无需新增迁移。
- 护栏仅作用于后端规划运行链路，网页入口不需要改动。
- TTL 以 `run.created` 为起点且 `restart()` 不刷新：多轮澄清累计超过窗口后需新建任务，这是刻意设计（避免旧激活无限续命）。

## 6. 尚未覆盖的容量项

以下属于容量/伸缩范畴，与护栏本身的正确性无关：

- 未接入具体模型的 tokenizer，上下文预算按字符近似。
- 进程内锁、后台任务与检查点连接池按单 worker 设计；多实例部署前需集中限流与任务队列。
- 真实 PostgreSQL 并发压测未在真库实跑，当前并发回归在 SQLite 行锁模拟下验证。
- Sites 发布仍被账号归属阻断，本地改动尚未部署；相关部署配置与线上地址保留在 `README.md`、`docs/deployment.json` 与部署文档中。
