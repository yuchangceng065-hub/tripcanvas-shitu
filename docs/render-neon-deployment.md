# Render Free + Neon Free 部署

目标：保留 Vue + FastAPI + PostgreSQL。Render 运行单实例 Python API，Neon 持久保存账号、会话、行程、版本和分享。实际服务已经创建，公开部署信息见 deployment.json。后端健康检查：https://tripcanvas-api.onrender.com/api/v1/health。真实 HTTPS 集成验证已通过。

## 免费资源

- Render Blueprint 使用 `plan: free`，没有声明付费磁盘或 Render 数据库。
- Neon 选择 Free，建立一个 TripCanvas 项目；优先选择与 Render Oregon 接近的可用区域。
- Neon 数据库 URL 使用 SSL，推荐 pooled connection string；只通过 Render 服务端环境变量传递。
- Render 空闲 15 分钟可能休眠，前端连接时最多等待约 100 秒，显示唤醒提示，失败后允许手动重连。只按访问需求唤醒，不使用定时保活。

## 创建顺序

1. 登录 GitHub，将本项目源码发布到私有仓库。排除 `.env*`、本地数据库、虚拟环境、构建输出、平台凭据；保留 `.env.example`。
2. 登录 Neon，确认 Free 套餐后创建项目与数据库，获得带 `sslmode=require` 的 PostgreSQL 连接字符串。不要贴进聊天或提交到 Git。
3. 登录 Render，授权读取该仓库，通过 `render.yaml` 创建 `tripcanvas-api`，明确选择 Free。
4. 填入 `DATABASE_URL`；`FRONTEND_ORIGINS` 已限定现有 Sites 域名，`COOKIE_SECURE=1`。
5. Docker 自动读取 Render `PORT`，运行一个 Uvicorn worker。云端未配置 PostgreSQL 时拒绝启动，防止临时 SQLite 数据丢失。
6. 检查 `/api/v1/health` 返回 `status=ok`、`storage=postgresql`；验证注册、Bearer 登录、行程同步、版本保护、分享与撤销、路线计算。
7. 前端构建时设置 `VITE_API_BASE=https://实际服务名.onrender.com/api/v1`，通过 Sites 工作流重新发布。模型密钥仅配置到 Render，不使用 VITE_ 前缀。
8. 验证冷启动和刷新后的登录状态。发生写入超时时不自动重发注册或分享操作，先连接后检查服务端结果。

## 数据库与运维边界

数据库启动连接有有限次数重试；连接池为 2 个常驻连接、最多额外 2 个连接，并启用失效检测。当前初始表由 SQLAlchemy 创建，后续结构变更应增加迁移。公共路线服务在单进程内限速与缓存；扩容前需要集中限流或替换路线提供方。

免费资源有用量限制；不得自动升级套餐或增加付费资源。模型现接 Groq Free Tier 的 openai/gpt-oss-20b，使用专用 GROQ_API_KEY；不自动升级付费档或切换付费模型。

## 参考

- https://render.com/docs/free
- https://render.com/docs/blueprint-spec
- https://neon.com/pricing

## 兼容版本的发布验收

Render Docker 实例在开始接收请求前执行 Alembic。对于旧版由 ORM 创建、且列结构与已知旧版一致的数据库，启动时登记原有版本，再补齐 `runs.generation`。遇到无法识别的结构时启动会报错，便于核对数据库。已有数据应通过 Neon 的备份或分支保留。应用回滚到旧版时可以保留新增列。

发布后读取 `/api/v1/health` 核对后端提交。Blueprint 的 `FRONTEND_ORIGINS` 包含 GitHub Pages 和 Sites 来源。先前手动创建的 Render 服务使用 `RENDER_EXTERNAL_HOSTNAME` 补齐 GitHub Pages 来源，限定为 `tripcanvas-api.onrender.com`。

用独立 QA 账号运行以下采集，`<commit>` 填后端发布的完整提交。它验证重复确认只生成一个版本和一条对应的确认事件。采集会留下 QA 运行记录与一条测试行程。

```bash
.venv/bin/python scripts/capture_live_run.py \
  --mode full --allowed-place-ids quyuan,museum \
  --release-commit <commit> --output /tmp/tripcanvas-release-capture.json
```

本地用 `backend/tests/test_agent_graph.py` 验证持久检查点、SSE 游标续读、冲突修复、地图故障和重复确认。地图故障用受控夹具模拟，避免在生产环境人为停用地图。真实供应商采集和本地故障模拟各自记录环境与结果。
