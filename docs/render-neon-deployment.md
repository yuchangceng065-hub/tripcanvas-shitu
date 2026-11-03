# Render Free + Neon Free 部署

目标：保留 Vue + FastAPI + PostgreSQL。Render 运行单实例 Python API，Neon 持久保存账号、会话、行程、版本和分享。当前文件是部署配置说明，不代表云端服务已经创建。

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

免费资源有用量限制；不得自动升级套餐或增加付费资源。模型调用独立计费，目前未配置。

## 参考

- https://render.com/docs/free
- https://render.com/docs/blueprint-spec
- https://neon.com/pricing
